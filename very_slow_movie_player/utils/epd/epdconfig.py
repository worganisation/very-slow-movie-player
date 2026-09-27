"""Config for the EPD.

Adapted from https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd

* | File        :	  epdconfig.py
* | Author      :   Waveshare team
* | Function    :   Hardware underlying interface
* | Info        :
*----------------
* | This version:   V1.0
* | Date        :   2019-06-21
* | Info        :
******************************************************************************
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS OR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from logging import getLogger
from time import sleep
from typing import ClassVar, Literal, Protocol, cast
from unittest.mock import MagicMock

from settings import SETTINGS

LOGGER = getLogger(__name__)


class GPIOInterface(Protocol):
    """GPIO operations required by the display driver."""

    BCM: int
    OUT: int
    IN: int

    def output(self, pin: int, value: int) -> None:
        """Set a pin high or low."""
        ...

    def input(self, pin: int) -> int:
        """Read a pin."""
        ...

    def setmode(self, _mode: int) -> None:
        """Select pin-numbering mode."""
        ...

    def setwarnings(self, value: bool) -> None:  # noqa: FBT001
        """Enable or disable GPIO warnings."""
        ...

    def setup(self, pin: int, _direction: int) -> None:
        """Configure a pin."""
        ...

    def cleanup(self) -> None:
        """Release GPIO resources."""
        ...


class SPIInterface(Protocol):
    """SPI operations required by the display driver."""

    max_speed_hz: int
    mode: int

    def writebytes(self, data: list[int]) -> None:
        """Send bytes over SPI."""
        ...

    def close(self) -> None:
        """Close the SPI device."""
        ...


type SPIConstructor = Callable[[int, int], SPIInterface]


class RaspberryPi:
    """Raspberry Pi configuration."""

    # Pin definition
    RST_PIN: ClassVar[int] = 17
    DC_PIN: ClassVar[int] = 25
    CS_PIN: ClassVar[int] = 8
    BUSY_PIN: ClassVar[int] = 24

    def __init__(self) -> None:
        self.module_initialized: bool = False
        try:
            self.gpio: GPIOInterface = cast(
                "GPIOInterface",
                cast("object", import_module("RPi.GPIO")),
            )
            spi_device = cast(
                "SPIConstructor",
                import_module("spidev").SpiDev,
            )

            # SPI device, bus = 0, device = 0
            self.spi: SPIInterface = spi_device(0, 0)
        except ImportError as exc:
            if not SETTINGS.vsmp_allow_mock_hardware:
                message = "Display hardware is unavailable; install RPi.GPIO and spidev, "
                message += "or set VSMP_ALLOW_MOCK_HARDWARE=true for local development"
                raise RuntimeError(message) from exc

            self.gpio = MagicMock()
            self.spi = MagicMock()

    def digital_write(self, pin: int, *, value: bool) -> None:
        """Write the value to the pin."""
        self.gpio.output(pin, value)

    def digital_read(self, pin: int) -> bool:
        """Read the value of the pin."""
        return bool(self.gpio.input(pin))

    @staticmethod
    def delay_ms(delay_time: float) -> None:
        """Delay in milliseconds."""
        sleep(delay_time / 1000)

    def spi_writebyte(self, data: list[int]) -> None:
        """Write byte to SPI (Serial Peripheral Interface)."""
        self.spi.writebytes(data)

    def module_init(self) -> Literal[0]:
        """Module initialization."""
        self.module_initialized = False
        try:
            self.gpio.setmode(self.gpio.BCM)
            self.gpio.setwarnings(False)  # noqa: FBT003
            self.gpio.setup(self.RST_PIN, self.gpio.OUT)
            self.gpio.setup(self.DC_PIN, self.gpio.OUT)
            self.gpio.setup(self.CS_PIN, self.gpio.OUT)
            self.gpio.setup(self.BUSY_PIN, self.gpio.IN)
            self.spi.max_speed_hz = 4000000
            self.spi.mode = 0b00
        except BaseException:
            # GPIO may have configured only some pins. Avoid module_exit's pin writes.
            try:
                self.spi.close()
            except BaseException:
                LOGGER.exception("Failed to close SPI after display setup failed")
            try:
                self.gpio.cleanup()
            except BaseException:
                LOGGER.exception("Failed to release GPIO after display setup failed")
            raise
        self.module_initialized = True
        return 0

    def module_exit(self) -> None:
        """Module exit."""
        if not self.module_initialized:
            return
        first_error: BaseException | None = None
        for cleanup in (
            self.spi.close,
            lambda: self.gpio.output(self.RST_PIN, 0),
            lambda: self.gpio.output(self.DC_PIN, 0),
            self.gpio.cleanup,
        ):
            try:
                cleanup()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
                else:
                    LOGGER.exception("Additional display hardware cleanup failure")
        self.module_initialized = False
        if first_error is not None:
            raise first_error
