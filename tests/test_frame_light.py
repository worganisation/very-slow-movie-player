"""Exercise PWM state, hardware failures, and MQTT discovery end to end."""

# ruff: noqa: PT009, PT027, PLC0415 - unittest matches the existing test suite.

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from frame_light import FrameLight
from pydantic import ValidationError


class FrameLightTests(unittest.TestCase):
    """Validate commands against the actual light controller."""

    def setUp(self) -> None:
        """Start an isolated GPIO output."""
        self.gpio = Mock()
        self.light = FrameLight(self.gpio)
        self.addCleanup(self.light.close)
        self.light.start()
        self.pwm = self.gpio.PWM.return_value

    def test_brightness_and_toggle_restore(self) -> None:
        """An off/on pair restores brightness, including after brightness zero."""
        self.pwm.start.assert_called_once_with(0)
        result = json.loads(self.light.apply('{"state":"ON","brightness":64}'))
        self.assertEqual(
            result, {"state": "ON", "brightness": 64, "color_mode": "brightness"}
        )
        self.pwm.ChangeDutyCycle.assert_called_with(64 * 100 / 255)

        for payload in ('{"state":"OFF"}', '{"state":"ON","brightness":0}'):
            self.assertEqual(json.loads(self.light.apply(payload))["state"], "OFF")
            self.pwm.ChangeDutyCycle.assert_called_with(0)
            self.assertEqual(
                json.loads(self.light.apply('{"state":"ON"}'))["brightness"], 64
            )
            self.pwm.ChangeDutyCycle.assert_called_with(64 * 100 / 255)

    def test_invalid_commands_preserve_output_and_confirmed_state(self) -> None:
        """Malformed and out-of-range payloads cannot alter the hardware state."""
        good = self.light.apply('{"state":"ON","brightness":100}')
        for payload in (
            "null",
            "[]",
            "{}",
            "not json",
            '{"state":"on"}',
            '{"state":"ON","brightness":-1}',
            '{"state":"ON","brightness":256}',
            '{"state":"ON","brightness":true}',
            '{"state":"ON","brightness":12.5}',
            '{"state":"ON","brightness":"20"}',
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                self.light.apply(payload)
            self.assertEqual(self.light.state(), good)
            self.pwm.ChangeDutyCycle.assert_called_once_with(100 * 100 / 255)

    def test_failed_output_does_not_confirm_new_state(self) -> None:
        """PWM errors preserve the last state that successfully reached GPIO."""
        good = self.light.apply('{"state":"ON","brightness":100}')
        self.pwm.ChangeDutyCycle.side_effect = RuntimeError("GPIO failed")
        with self.assertRaises(RuntimeError):
            self.light.apply('{"state":"OFF"}')
        self.assertEqual(self.light.state(), good)

    def test_close_releases_pin_even_if_pwm_stop_fails(self) -> None:
        """A failed PWM stop still drives low and cleans up its own pin."""
        self.pwm.stop.side_effect = RuntimeError("stop failed")
        with self.assertRaises(RuntimeError):
            self.light.close()
        self.gpio.output.assert_called_with(12, 0)
        self.gpio.cleanup.assert_called_once_with(12)

    def test_partial_startup_can_be_cleaned_up(self) -> None:
        """PWM construction failure still allows the configured output to close."""
        gpio = Mock()
        gpio.PWM.side_effect = RuntimeError("PWM unavailable")
        light = FrameLight(gpio)
        with self.assertRaises(RuntimeError):
            light.start()
        light.close()
        gpio.output.assert_called_once_with(12, 0)
        gpio.cleanup.assert_called_once_with(12)


class LightMQTTTests(unittest.TestCase):
    """Run real MQTT handlers with a mocked transport and GPIO."""

    def setUp(self) -> None:
        """Provide valid settings and a connected client without live services."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        video = Path(directory.name) / "video.mp4"
        video.touch()
        environment = patch.dict(
            os.environ,
            {
                "VSMP_VIDEO_PATH": str(video),
                "IMMICH_URL": "https://immich.invalid",
                "IMMICH_API_KEY": "test-key",
                "IMMICH_ALBUM_ID": "00000000-0000-0000-0000-000000000001",
                "YT_PLAYLIST_ID": "test",
                "MQTT_HOST": "localhost",
                "VSMP_ALLOW_MOCK_HARDWARE": "true",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        module = importlib.import_module("mqtt_controls")
        controls = importlib.import_module("controls")
        self.client = module.HAClient(controls.CommandMailbox())
        self.client.client = Mock()
        self.client.client.is_connected.return_value = True
        self.gpio = Mock()
        self.light = FrameLight(self.gpio)
        self.light.start()
        self.addCleanup(self.light.close)
        self.client.frame_light = self.light
        self.client.state("frame_light", self.light.state())
        self.client.state("frame_light_availability", "online")

    def command(self, payload: bytes, *, retained: bool = False) -> None:
        """Deliver a real Paho message through the subscription handler."""
        from paho.mqtt.client import MQTTMessage

        message = MQTTMessage(topic=f"{self.client.root}/command/frame_light".encode())
        message.payload = payload
        message.retain = retained
        self.client._on_message(self.client.client, None, message)

    def test_discovery_and_reconnect_republish_confirmed_brightness(self) -> None:
        """Fresh light commands work without draining the playback mailbox."""
        self.command(b'{"state":"ON","brightness":128}')
        self.gpio.PWM.return_value.ChangeDutyCycle.assert_called_with(128 * 100 / 255)
        self.assertEqual(self.client.mailbox.drain(), ({}, set()))

        for _ in range(2):
            self.client.client.publish.reset_mock()
            self.client._announce()
            calls = self.client.client.publish.call_args_list
            config = next(
                json.loads(call.args[1])
                for call in calls
                if call.args[0]
                == f"{self.client.discovery}/light/vsmp_{self.client.device_id}_frame_light/config"
            )
            self.assertEqual(config["schema"], "json")
            self.assertEqual(config["supported_color_modes"], ["brightness"])
            self.assertEqual(config["availability_mode"], "all")
            self.client.client.publish.assert_any_call(
                f"{self.client.root}/state/frame_light",
                self.light.state(),
                qos=1,
                retain=True,
            )

    def test_replayed_and_invalid_commands_preserve_live_brightness(self) -> None:
        """Replayed or malformed commands cannot override the live output."""
        self.command(b'{"state":"ON","brightness":25}')
        self.command(b'{"state":"ON","brightness":255}', retained=True)
        self.command(b"\xff")
        self.assertEqual(json.loads(self.light.state())["brightness"], 25)
        self.gpio.PWM.return_value.ChangeDutyCycle.assert_called_once_with(25 * 100 / 255)

    def test_gpio_failure_marks_light_unavailable_until_success(self) -> None:
        """A failed command is not reported as a successful brightness change."""
        pwm = self.gpio.PWM.return_value
        pwm.ChangeDutyCycle.side_effect = RuntimeError("GPIO failure")
        self.command(b'{"state":"ON","brightness":200}')
        self.client._announce()
        self.client.client.publish.assert_any_call(
            f"{self.client.root}/state/frame_light_availability",
            "offline",
            qos=1,
            retain=True,
        )
        self.assertEqual(json.loads(self.light.state())["state"], "OFF")

        pwm.ChangeDutyCycle.side_effect = None
        self.command(b'{"state":"ON","brightness":50}')
        self.client.client.publish.assert_any_call(
            f"{self.client.root}/state/frame_light_availability",
            "online",
            qos=1,
            retain=True,
        )
        self.assertEqual(json.loads(self.light.state())["brightness"], 50)
