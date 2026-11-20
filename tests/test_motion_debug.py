"""Verify debug clip timing and playable output without a physical camera."""

# ruff: noqa: PT009 - unittest runs without extra dependencies.

from __future__ import annotations

import json
import subprocess  # noqa: S404 - these tests use local FFmpeg and FFprobe
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from shutil import which
from unittest.mock import patch

from motion_debug import HEIGHT, WIDTH, EventRecorder, jpeg_frames, motion_frames
from PIL import Image


class MotionDebugTests(unittest.TestCase):
    """Exercise the camera stream framing and event recording."""

    def test_jpeg_frames_across_pipe_reads(self) -> None:
        """A split end marker and several frames in one read preserve every frame."""
        with Image.new("L", (32, 18), 50) as image, BytesIO() as jpeg:
            image.save(jpeg, format="JPEG")
            frame = jpeg.getvalue()

        self.assertEqual(
            list(jpeg_frames([frame[:-1], frame[-1:] + frame])), [frame, frame]
        )

    def test_monitor_detects_motion_after_camera_settles(self) -> None:
        """Ignore the change during startup and detect it after establishing a baseline."""
        with Image.new("L", (640, 360), 50) as image, BytesIO() as jpeg:
            image.save(jpeg, format="JPEG")
            still = jpeg.getvalue()

        with Image.new("L", (640, 360), 50) as image, BytesIO() as jpeg:
            image.paste(200, (0, 0, 160, 120))
            image.save(jpeg, format="JPEG")
            changed = jpeg.getvalue()

        with patch("motion_debug.monotonic", side_effect=[10, 11, 13.9, 14, 15]):
            frames = motion_frames(
                [still, changed, changed, still, changed], 1, settle_seconds=4
            )
            triggers = [timestamp for _, timestamp, moving in frames if moving]

        self.assertEqual(triggers, [15])

    def test_event_records_at_full_resolution_and_extends_until_quiet(self) -> None:
        """Probe a real saved clip and verify a second detection extends its deadline."""
        ffmpeg, ffprobe = which("ffmpeg"), which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            self.skipTest("ffmpeg and ffprobe are required for motion clip validation")

        with Image.new("RGB", (WIDTH, HEIGHT), "red") as image, BytesIO() as jpeg:
            image.save(jpeg, format="JPEG")
            frame = jpeg.getvalue()

        with Image.new("RGB", (WIDTH, HEIGHT), "red") as image, BytesIO() as jpeg:
            image.paste("white", (0, 0, WIDTH // 3, HEIGHT // 3))
            image.save(jpeg, format="JPEG")
            moved = jpeg.getvalue()

        with tempfile.TemporaryDirectory() as work:
            output = Path(work)
            notified: list[Path] = []
            recorder = EventRecorder(output, ffmpeg, notified.append)
            with patch("motion_debug.monotonic", side_effect=[130, 400, 430, 699, 700]):
                recorder.record([frame, moved, moved, moved, moved], triggered_at=129)

            (clip,) = output.glob("motion-*.mkv")
            self.assertEqual(notified, [clip])

            result = subprocess.run(  # noqa: S603 - fixed local FFprobe command
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-count_frames",
                    "-show_entries",
                    "stream=codec_name,nb_read_frames,width,height,r_frame_rate",
                    "-of",
                    "json",
                    str(clip),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            stream = json.loads(result.stdout)["streams"][0]
            self.assertEqual(
                stream,
                {
                    "codec_name": "mjpeg",
                    "width": WIDTH,
                    "height": HEIGHT,
                    "r_frame_rate": "287/20",
                    "nb_read_frames": "5",
                },
            )
