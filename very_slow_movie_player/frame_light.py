"""GPIO12 frame lighting with confirmed MQTT JSON state."""

from __future__ import annotations

from json import dumps
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from utils.epd.epdconfig import GPIOInterface, PWMInterface

PIN = 12
FREQUENCY = 500
MAX_BRIGHTNESS = 255


class LightCommand(BaseModel):
    """Validate a complete command before changing the output."""

    model_config = ConfigDict(extra="forbid", strict=True)

    state: Literal["ON", "OFF"]
    brightness: Annotated[int, Field(ge=0, le=MAX_BRIGHTNESS)] | None = None


class FrameLight:
    """Own the frame's PWM output; the MQTT thread serializes commands."""

    def __init__(self, gpio: GPIOInterface) -> None:
        self.gpio: GPIOInterface = gpio
        self.pwm: PWMInterface | None = None
        self.enabled: bool = False
        self.brightness: int = MAX_BRIGHTNESS
        self._configured: bool = False

    def start(self) -> None:
        """Initialize the MOSFET input low before starting PWM."""
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setup(PIN, self.gpio.OUT, initial=0)
        self._configured = True
        self.pwm = self.gpio.PWM(PIN, FREQUENCY)
        self.pwm.start(0)

    def apply(self, payload: str) -> str:
        """Apply validated brightness and return state only after GPIO succeeds."""
        command = LightCommand.model_validate_json(payload)
        if self.pwm is None:
            raise RuntimeError("Frame light is not initialized")

        brightness = command.brightness
        enabled = command.state == "ON" and brightness != 0
        remembered = brightness or self.brightness
        self.pwm.ChangeDutyCycle(remembered * 100 / MAX_BRIGHTNESS if enabled else 0)
        self.enabled = enabled
        self.brightness = remembered
        return self.state()

    def state(self) -> str:
        """Return the confirmed state and last nonzero brightness."""
        return dumps(
            {
                "state": "ON" if self.enabled else "OFF",
                "brightness": self.brightness,
                "color_mode": "brightness",
            },
            separators=(",", ":"),
        )

    def close(self) -> None:
        """Stop PWM and release only the light's pin, including partial startup."""
        try:
            if self.pwm is not None:
                self.pwm.stop()
        finally:
            self.pwm = None
            if self._configured:
                try:
                    self.gpio.output(PIN, 0)
                finally:
                    self.gpio.cleanup(PIN)
                    self._configured = False
                    self.enabled = False
