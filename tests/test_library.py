"""Exercise persistent media identities, recovery, and publication."""

# ruff: noqa: PT009, PT027, S404, S603 - unittest and local ffmpeg fixture.

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# ruff: noqa: PT009, PT027 - unittest requires no extra test dependency.


class MediaLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create a database under complete synthetic settings."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.database = root / "state.sqlite3"
        video = root / "settings-video.mp4"
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
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        initialize = importlib.import_module("storage").initialize
        library_class = importlib.import_module("library").MediaLibrary
        initialize(
            self.database,
            progress_file=root / "absent.json",
            controls_file=root / "absent-controls.json",
        )
        self.library = library_class(self.database, root / "media")
        self.library.initialize()

    def test_stable_identity_retry_and_schema_preserved(self) -> None:
        """Retries retain identity and do not alter the storage schema version."""
        item = self.library.begin("youtube", "abc", "original", "First")
        self.library.fail(item.id, "network failed")
        retried = self.library.begin("youtube", "abc", "original", "Updated")
        self.assertEqual(item.id, retried.id)
        self.assertEqual(retried.title, "Updated")
        self.assertEqual(retried.status, "importing")
        self.assertEqual(self.library.list_ready(), [])
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_interrupted_import_recovers_except_while_locked(self) -> None:
        """Startup recovery cannot mark a live import failed."""
        item = self.library.begin("jellyfin", "film", "source", "Film")
        with self.library.import_lock():
            self.library.initialize()
            self.assertEqual(self.library.get(item.id).status, "importing")
            with self.assertRaises(BlockingIOError), self.library.import_lock():
                pass
        self.library.initialize()
        self.assertEqual(self.library.get(item.id).status, "failed")

    def test_probe_rejects_corrupt_file_and_promotes_valid_video(self) -> None:
        """Only a valid staged video can become ready."""
        item = self.library.begin("youtube", "video", "original", "Video")
        video = self.library.staging_dir(item.id) / "video.mp4"
        _ = video.write_bytes(b"not a video")
        with self.assertRaises(ValueError):
            self.library.promote(item.id, video)
        _ = subprocess.run(
            [
                "/usr/bin/env",
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=size=16x16:duration=1",
                "-c:v",
                "mpeg4",
                "-y",
                str(video),
            ],
            check=True,
            capture_output=True,
        )
        ready = self.library.promote(item.id, video)
        self.assertEqual(ready.status, "ready")
        self.assertEqual(self.library.list_ready()[0].id, item.id)
        self.assertTrue(ready.video_path and ready.video_path.is_file())

    def test_probe_rejects_nonfinite_duration_and_cover_art(self) -> None:
        """Metadata alone cannot turn still artwork or NaN duration into video."""
        path = Path(self.directory.name) / "fake.mp4"
        _ = path.write_bytes(b"metadata")
        payloads = [
            {
                "streams": [{"codec_type": "video", "avg_frame_rate": "25/1"}],
                "format": {"duration": "nan"},
            },
            {
                "streams": [
                    {
                        "codec_type": "video",
                        "avg_frame_rate": "25/1",
                        "disposition": {"attached_pic": 1},
                    }
                ],
                "format": {"duration": "10"},
            },
            {
                "streams": [{"codec_type": "video", "avg_frame_rate": "0/0"}],
                "format": {"duration": "10"},
            },
        ]
        for payload in payloads:
            with self.subTest(payload=payload), patch("library.subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess(
                    [], 0, json.dumps(payload), ""
                )
                with self.assertRaises(ValueError):
                    self.library.probe(path)


if __name__ == "__main__":
    unittest.main()
