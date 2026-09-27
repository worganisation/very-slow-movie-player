"""Hardware-free integration coverage for library controls and displayed captions."""

# ruff: noqa: PT009, PT027, PLC0415 - unittest runs without extra dependencies.

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image


class PlaybackLibraryTests(unittest.TestCase):
    """Exercise the actual control and display paths with isolated state."""

    def setUp(self) -> None:
        """Provide complete eagerly validated settings without real services."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        video = self.root / "video.mp4"
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
        settings = importlib.import_module("settings")
        with patch.object(settings.SETTINGS, "vsmp_allow_mock_hardware", new=True):
            self.main = importlib.import_module("main")
        self.controls = importlib.import_module("controls")
        settings = importlib.import_module("settings")
        with patch.object(settings.SETTINGS, "vsmp_video_path", video):
            self.defaults = self.controls.PlaybackControls.defaults()

    def test_caption_controls_validate_and_persist(self) -> None:
        """Caption values survive SQLite restart while invalid settings are rejected."""
        import sqlite3

        database = self.root / "state.db"
        with sqlite3.connect(database) as connection:
            _ = connection.execute(
                "CREATE TABLE control_overrides(name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
        candidate = self.controls.apply_command(self.defaults, "captions_enabled", "OFF")
        candidate = self.controls.apply_command(candidate, "caption_offset", "1.25")
        with (
            patch.object(self.controls, "initialize"),
            patch.object(
                self.controls, "connect", side_effect=lambda: sqlite3.connect(database)
            ),
            patch.object(
                self.controls.PlaybackControls, "defaults", return_value=self.defaults
            ),
        ):
            self.controls.save_controls(candidate, {"captions_enabled", "caption_offset"})
            restored, names = self.controls.load_controls()
        self.assertFalse(restored.captions_enabled)
        self.assertEqual(restored.caption_offset, 1.25)
        self.assertEqual(names, {"captions_enabled", "caption_offset"})
        for name, payload in [
            ("caption_offset", "nan"),
            ("caption_font_size", "4"),
            ("caption_style", "invalid"),
        ]:
            with self.assertRaises(ValueError):
                self.controls.apply_command(candidate, name, payload)

    def test_import_commands_are_not_persisted_controls(self) -> None:
        """Retained requests are ignored and imports cannot become startup actions."""
        mailbox = self.controls.CommandMailbox()
        mailbox.submit("import_youtube", "https://youtu.be/example", retained=True)
        self.assertEqual(mailbox.drain(), ({}, set()))
        mailbox.submit("import_youtube", "https://youtu.be/example", retained=False)
        self.assertIn("import_youtube", mailbox.drain()[0])
        with self.assertRaises(ValueError):
            self.controls.apply_command(self.defaults, "import_youtube", "url")

    def test_preview_matches_captioned_panel(self) -> None:
        """MQTT publishes the same composed bitmap sent to the panel."""
        image_path = self.root / "image.png"
        Image.new("L", (800, 480), 128).save(image_path)
        panel_images = []
        display = SimpleNamespace(
            WIDTH=800,
            HEIGHT=480,
            getbuffer=lambda image: panel_images.append(image.copy()) or b"buffer",
            display=Mock(),
        )
        mqtt = Mock()
        output = self.root / "formatted.png"
        with (
            patch.object(self.main, "DISPLAY", display),
            patch.object(self.main, "format_image", return_value=image_path),
        ):
            self.main.display_image(
                image_path,
                1.7,
                mqtt,
                caption="A caption for this frame.",
                controls=self.defaults,
            )
        output.write_bytes(mqtt.image.call_args.args[0])
        with Image.open(output) as preview:
            self.assertEqual(preview.mode, "1")
            self.assertEqual(preview.tobytes(), panel_images[0].tobytes())
        display.display.assert_called_once_with(b"buffer")

    def test_oversize_caption_keeps_playback_and_reports_error(self) -> None:
        """A layout failure shows the image and remains visible as caption status."""
        image_path = self.root / "image.png"
        Image.new("L", (800, 480), 128).save(image_path)
        display = SimpleNamespace(
            WIDTH=800, HEIGHT=480, getbuffer=Mock(return_value=b"frame"), display=Mock()
        )
        mqtt = Mock()
        with (
            patch.object(self.main, "DISPLAY", display),
            patch.object(self.main, "format_image", return_value=image_path),
        ):
            self.main.display_image(
                image_path, 1.7, mqtt, caption="word " * 500, controls=self.defaults
            )
        display.display.assert_called_once_with(b"frame")
        mqtt.image.assert_called_once()
        self.assertTrue(
            any(
                call.args[0] == "caption_error" and call.args[1] != "none"
                for call in mqtt.state.call_args_list
            )
        )

    def test_background_import_is_bounded_and_failure_visible(self) -> None:
        """A running request cannot accumulate a queue or block playback polling."""
        from library_runtime import LibraryRuntime

        runtime = object.__new__(LibraryRuntime)
        runtime.mqtt = Mock()
        runtime.library = Mock()
        runtime.library.list_ready.return_value = []
        runtime.library.list_items.return_value = []
        process = Mock()
        process.poll.return_value = None
        runtime.pending = None
        with patch("library_runtime.subprocess.Popen", return_value=process) as spawn:
            runtime.submit("import_youtube", "https://youtu.be/example")
            runtime.submit("import_youtube", "https://youtu.be/another")
        spawn.assert_called_once()
        runtime.mqtt.state.assert_any_call("import_error", "An import is already running")
        runtime.mqtt.state.assert_any_call("import_youtube", "")
        process.poll.return_value = 1
        process.communicate.return_value = (b"", None)
        runtime.refresh()
        self.assertIsNone(runtime.pending)
        runtime.mqtt.state.assert_any_call("import_status", "failed")

    def test_caption_uses_displayed_frame_timestamp(self) -> None:
        """Frame position, rather than wall clock or next frame, chooses the cue."""
        runtime = object.__new__(self.main.PlaybackRuntime)
        runtime.controls = self.defaults.model_copy(
            update={"source": "library", "library_id": "media", "caption_offset": 0.4}
        )
        runtime.library = Mock()
        runtime.mqtt = Mock()
        runtime.library.caption.return_value = "Dialogue"
        self.assertEqual(runtime.caption_text(48 / 24), "Dialogue")
        runtime.library.caption.assert_called_once_with("media", 2.0, 0.4)
        runtime.controls = runtime.controls.model_copy(update={"captions_enabled": False})
        self.assertEqual(runtime.caption_text(2), "")


if __name__ == "__main__":
    unittest.main()
