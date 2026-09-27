"""Verify playback seeks on containers whose audio begins before video."""

# ruff: noqa: PT009 - unittest and delayed imports avoid hardware dependencies.

from __future__ import annotations

import importlib
import os
import shutil
import subprocess  # noqa: S404 - local synthetic ffmpeg fixture only
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


class PlaybackTimestampTests(unittest.TestCase):
    """Use actual frame extraction with a non-zero video timestamp origin."""

    def test_audio_earlier_than_video_seeks_distinct_frames(self) -> None:  # noqa: PLR0914
        """Frames zero through two match absolute source PTS five through seven."""
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            self.skipTest("ffmpeg and ffprobe are required for playback regression")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "audio-first.mkv"
            _ = subprocess.run(  # noqa: S603 - fixed local executable and arguments
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-copyts",
                    "-itsoffset",
                    "5",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=64x64:rate=1:duration=3",
                    "-itsoffset",
                    "3",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=16000:duration=5",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "ffv1",
                    "-c:a",
                    "pcm_s16le",
                    str(video),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
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
            with environment:
                settings = importlib.import_module("settings")
                with patch.object(
                    settings.SETTINGS, "vsmp_allow_mock_hardware", new=True
                ):
                    main = importlib.import_module("main")
            frame_count, fps, stream_index, start_time = main.video_metadata(video)
            self.assertEqual(frame_count, 3)
            self.assertEqual((fps, stream_index, start_time), (1.0, 0, 5.0))

            actual_frames: list[bytes] = []
            for frame in range(3):
                actual = root / f"actual-{frame}.jpg"
                _ = main.extract_frame(
                    video,
                    frame,
                    fps=fps,
                    stream_index=stream_index,
                    start_time=start_time,
                    extract_output_path=actual,
                )
                expected = root / f"expected-{frame}.jpg"
                _ = subprocess.run(  # noqa: S603 - independently seek absolute source PTS
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-seek_timestamp",
                        "1",
                        "-ss",
                        str(5 + frame),
                        "-i",
                        str(video),
                        "-map",
                        "0:0",
                        "-frames:v",
                        "1",
                        str(expected),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
                with Image.open(actual) as image:
                    actual_pixels = image.convert("RGB").tobytes()
                with Image.open(expected) as image:
                    expected_pixels = image.convert("RGB").tobytes()
                self.assertEqual(actual_pixels, expected_pixels)
                actual_frames.append(actual_pixels)
            self.assertEqual(len(set(actual_frames)), 3)
