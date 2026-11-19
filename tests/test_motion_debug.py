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

from motion_debug import HEIGHT, WIDTH, EventRecorder, jpeg_frames
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

    def test_event_includes_pre_roll_and_extends_until_quiet(self) -> None:
        """Probe a real saved clip for the expected pre-roll, trigger and tail frames."""
        ffmpeg, ffprobe = which("ffmpeg"), which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            self.skipTest("ffmpeg and ffprobe are required for motion clip validation")

        with Image.new("RGB", (WIDTH, HEIGHT), "red") as image, BytesIO() as jpeg:
            image.save(jpeg, format="JPEG")
            frame = jpeg.getvalue()

        with tempfile.TemporaryDirectory() as work:
            output = Path(work)
            pre_roll = output / "pre-roll"
            pre_roll.mkdir()
            notified: list[Path] = []
            recorder = EventRecorder(output, pre_roll, ffmpeg, notified.append)
            try:
                recorder.ingest(frame, 9, moving=False)  # Outside the pre-roll.
                recorder.ingest(frame, 10, moving=False)  # Exactly T minus two minutes.
                recorder.ingest(frame, 129, moving=False)
                recorder.ingest(frame, 130, moving=True)
                recorder.ingest(frame, 700, moving=True)
                recorder.ingest(frame, 730, moving=False)  # First deadline has elapsed.
                self.assertIsNotNone(recorder.clip)
                recorder.ingest(
                    frame, 1300, moving=False
                )  # Ten minutes since the last trigger.
                (clip,) = output.glob("motion-*.mkv")
                self.assertEqual(notified, [clip])
            finally:
                recorder.finish()

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
                    "nb_read_frames": "6",
                },
            )
