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

    def test_dithering_discovery_validation_and_persistence(self) -> None:
        """All advertised methods round-trip through validated durable controls."""
        import json
        import sqlite3

        from dithering import DitheringMethod
        from mqtt_controls import HAClient

        client = HAClient(self.controls.CommandMailbox())
        client.client = Mock()
        client.client.is_connected.return_value = True
        client._announce()
        topic = (
            f"{client.discovery}/select/vsmp_{client.device_id}_dithering_method/config"
        )
        config = next(
            json.loads(call.args[1])
            for call in client.client.publish.call_args_list
            if call.args[0] == topic
        )
        self.assertEqual(config["options"], list(DitheringMethod))
        self.assertEqual(config["icon"], "mdi:dots-grid")
        self.assertFalse(config["optimistic"])
        database = self.root / "dither.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE control_overrides (name TEXT PRIMARY KEY, value TEXT)"
            )
        with (
            patch.object(self.controls, "initialize"),
            patch.object(
                self.controls, "connect", side_effect=lambda: sqlite3.connect(database)
            ),
            patch.object(
                self.controls.PlaybackControls, "defaults", return_value=self.defaults
            ),
        ):
            for method in DitheringMethod:
                candidate = self.controls.apply_command(
                    self.defaults, "dithering_method", method
                )
                self.controls.save_controls(candidate, {"dithering_method"})
                restored, names = self.controls.load_controls()
                self.assertEqual(restored.dithering_method, method)
                self.assertEqual(names, {"dithering_method"})
            with self.assertRaises(ValueError):
                self.controls.apply_command(self.defaults, "dithering_method", "invalid")
            self.assertEqual(self.controls.load_controls()[0], restored)

    def test_display_uses_selected_method_for_panel_and_mqtt_image(self) -> None:
        """Physical pixels and the HA preview use the same selected renderer."""
        from io import BytesIO

        from dithering import DitheringMethod, dither_image

        path = self.root / "frame.png"
        source = Image.linear_gradient("L").resize((800, 480))
        source.save(path)
        controls = self.defaults.model_copy(
            update={"dithering_method": DitheringMethod.ORDERED}
        )
        expected = dither_image(source, DitheringMethod.ORDERED).tobytes()
        mqtt = Mock()
        with (
            patch.object(self.main, "format_image", return_value=path),
            patch.object(self.main, "DISPLAY") as panel,
        ):
            panel.getbuffer.side_effect = lambda image: image.tobytes()
            self.main.display_image(path, 1.0, mqtt, controls=controls)
            panel.display.assert_called_once_with(expected)
        with Image.open(BytesIO(mqtt.image.call_args.args[0])) as preview:
            self.assertEqual(preview.tobytes(), expected)

    def test_dithering_environment_and_mailbox_validation(self) -> None:
        """Invalid defaults fail early and replayed commands cannot change settings."""
        from dithering import DitheringMethod
        from pydantic import ValidationError
        from settings import SETTINGS, Settings

        with self.assertRaises(ValidationError):
            Settings.model_validate({
                **SETTINGS.model_dump(),
                "vsmp_dithering_method": "invalid",
            })
        mailbox = self.controls.CommandMailbox()
        mailbox.submit("dithering_method", DitheringMethod.FLOYD_SQUARE, retained=True)
        self.assertEqual(mailbox.drain(), ({}, set()))
        mailbox.submit("dithering_method", DitheringMethod.ORDERED, retained=False)
        mailbox.submit(
            "dithering_method", DitheringMethod.ORDERED_VERTICAL, retained=False
        )
        self.assertEqual(
            mailbox.drain(),
            ({"dithering_method": DitheringMethod.ORDERED_VERTICAL}, set()),
        )

    def test_dithering_change_redisplays_same_frame_while_paused_after_dwell(
        self,
    ) -> None:
        """Changing the mode coalesces a redisplay without advancing the movie."""
        from dithering import DitheringMethod

        runtime = object.__new__(self.main.PlaybackRuntime)
        runtime.controls = self.defaults.model_copy(update={"playback_enabled": False})
        runtime.overridden = set()
        runtime.buttons = set()
        runtime.mqtt = Mock()
        runtime.current_path = self.root / "frame.png"
        runtime.current_video = (self.root / "video.mp4", 16385, 24.0, 0, 0.0)
        runtime.current_media = "movie"
        runtime.current_frame = 16386
        runtime.current_frame_count = 115939
        runtime.current_kind = "video"
        runtime.selection = runtime.selection_key()
        with patch.object(self.main, "save_controls"):
            runtime.apply_setting("dithering_method", DitheringMethod.ORDERED)
        self.assertEqual(runtime.buttons, {"redisplay"})
        with (
            patch.object(runtime, "_minimum_wait", return_value=1),
            patch.object(self.main, "display_image") as display,
        ):
            self.assertFalse(runtime.redisplay_if_ready())
            display.assert_not_called()
        with (
            patch.object(runtime, "_minimum_wait", return_value=0),
            patch.object(
                self.main, "extract_frame", return_value=runtime.current_path
            ) as extract,
            patch.object(self.main, "display_image") as display,
            patch.object(runtime, "mark_displayed") as mark,
        ):
            self.assertTrue(runtime.redisplay_if_ready())
            extract.assert_called_once_with(
                self.root / "video.mp4", 16385, fps=24.0, stream_index=0, start_time=0.0
            )
            self.assertEqual(
                display.call_args.kwargs["controls"].dithering_method,
                DitheringMethod.ORDERED,
            )
            self.assertEqual(mark.call_args.args[2], 16386)
        self.assertEqual(runtime.buttons, set())
        self.assertFalse(runtime.controls.playback_enabled)

    def test_import_fields_allow_empty_state_and_reannounce_after_restart(self) -> None:
        """Blank request fields remain valid after startup, clearing and HA birth."""
        import json

        from mqtt_controls import HAClient

        for _ in range(2):  # Fresh instances represent process restarts.
            client = HAClient(self.controls.CommandMailbox())
            client.client = Mock()
            client.client.is_connected.return_value = True
            for announce in range(2):  # Reannouncement also serves reconnect/HA birth.
                if announce:
                    client.state("import_jellyfin", "")
                    client.state("import_youtube", "")
                    client.client.reset_mock()
                client._announce()
                publications = client.client.publish.call_args_list
                for name in ("import_jellyfin", "import_youtube"):
                    topic = (
                        f"{client.discovery}/text/vsmp_{client.device_id}_{name}/config"
                    )
                    config = next(
                        json.loads(call.args[1])
                        for call in publications
                        if call.args[0] == topic
                    )
                    self.assertEqual(config["min"], 0)
                    client.client.publish.assert_any_call(
                        f"{client.root}/state/{name}", "", qos=1, retain=True
                    )
                self.assertEqual(client.mailbox.drain(), ({}, set()))

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
        candidate = self.controls.apply_command(candidate, "caption_background", "dark")
        with (
            patch.object(self.controls, "initialize"),
            patch.object(
                self.controls, "connect", side_effect=lambda: sqlite3.connect(database)
            ),
            patch.object(
                self.controls.PlaybackControls, "defaults", return_value=self.defaults
            ),
        ):
            self.controls.save_controls(
                candidate, {"captions_enabled", "caption_offset", "caption_background"}
            )
            restored, names = self.controls.load_controls()
        self.assertFalse(restored.captions_enabled)
        self.assertEqual(restored.caption_offset, 1.25)
        self.assertEqual(restored.caption_background, "dark")
        self.assertEqual(
            names, {"captions_enabled", "caption_offset", "caption_background"}
        )
        for name, payload in [
            ("caption_offset", "nan"),
            ("caption_font_size", "4"),
            ("caption_style", "invalid"),
            ("caption_background", "invalid"),
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
        mqtt.state.assert_any_call("current_caption", "A caption for this frame.")

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
        mqtt.state.assert_any_call("current_caption", "")
        self.assertTrue(
            any(
                call.args[0] == "caption_error" and call.args[1] != "none"
                for call in mqtt.state.call_args_list
            )
        )

    def test_caption_sensor_clears_on_silence_and_preserves_failed_panel(self) -> None:
        """Publish verbatim text only after display success; clear it on silence."""
        path = self.root / "frame.png"
        Image.new("L", (800, 480), 128).save(path)
        display = SimpleNamespace(getbuffer=Mock(return_value=b"frame"), display=Mock())
        mqtt = Mock()
        caption = 'Hello, world!\n"Yes?"'
        with (
            patch.object(self.main, "DISPLAY", display),
            patch.object(self.main, "format_image", return_value=path),
        ):
            self.main.display_image(
                path, 1.7, mqtt, caption=caption, controls=self.defaults
            )
            mqtt.state.assert_any_call("current_caption", caption)
            mqtt.reset_mock()
            self.main.display_image(path, 1.7, mqtt, controls=self.defaults)
            mqtt.state.assert_any_call("current_caption", "")
            mqtt.reset_mock()
            display.display.side_effect = RuntimeError("panel failed")
            with self.assertRaises(self.main.PanelRefreshError):
                self.main.display_image(path, 1.7, mqtt, controls=self.defaults)
            mqtt.state.assert_not_called()

    def test_timestamp_reports_displayed_frame_and_clears_for_photo(self) -> None:
        """Use media-relative frame time, including hours, and no photo timestamp."""
        runtime = object.__new__(self.main.PlaybackRuntime)
        runtime.mqtt = Mock()
        runtime.controls = self.defaults
        path = self.root / "frame.png"
        runtime.mark_displayed(path, "movie", video_frame=(path, 7323, 2.0, 0, 5.0))
        runtime.mqtt.state.assert_any_call("video_timestamp", "01:01:01")
        runtime.mqtt.reset_mock()
        runtime.mark_displayed(path, "photo", kind="photo")
        runtime.mqtt.state.assert_any_call("video_timestamp", "")

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
