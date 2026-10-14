"""Check ffmpeg timestamp origins using a tiny synthetic media container."""

# ruff: noqa: PT009 - unittest is the repository's test runner.

from __future__ import annotations

import json
import re
import shutil
import subprocess  # noqa: S404 - local ffmpeg fixture only
import tempfile
import unittest
from pathlib import Path
from typing import cast

from captions import CaptionCue, normalize_subtitles
from media_import import ImportService


class MediaTimestampTests(unittest.TestCase):
    """Exercise playback, subtitle, and audio timeline alignment."""

    def test_nonzero_stream_origins_align_with_frame_zero(self) -> None:  # noqa: PLR0914
        """Relative seeking and importer offsets map to one playback timeline."""
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg is None or ffprobe is None:
            self.skipTest("ffmpeg and ffprobe are required for timestamp regression")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subtitle = root / "input.srt"
            _ = subtitle.write_text(
                "1\n00:00:00,500 --> 00:00:01,500\nFirst frame caption\n",
                encoding="utf-8",
            )
            video = root / "offset.mkv"
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
                    "testsrc2=size=32x32:rate=1:duration=3",
                    "-itsoffset",
                    "7",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=16000:duration=1",
                    "-itsoffset",
                    "5",
                    "-f",
                    "srt",
                    "-i",
                    str(subtitle),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-map",
                    "2:0",
                    "-c:v",
                    "ffv1",
                    "-c:a",
                    "pcm_s16le",
                    "-c:s",
                    "srt",
                    str(video),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            result = subprocess.run(  # noqa: S603 - inspect the local fixture
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "stream=index,codec_type,start_time",
                    "-of",
                    "json",
                    str(video),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            probe = cast("dict[str, object]", json.loads(result.stdout))
            streams = cast("list[dict[str, object]]", probe["streams"])
            self.assertEqual(
                [float(str(s["start_time"])) for s in streams], [5.0, 7.0, 5.5]
            )

            for seek, expected_pts in [(0, 5), (1, 6), (2, 7)]:
                frame = subprocess.run(  # noqa: S603 - same pre-input seek used by extract_frame
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "info",
                        "-copyts",
                        "-ss",
                        str(seek),
                        "-i",
                        str(video),
                        "-map",
                        "0:0",
                        "-frames:v",
                        "1",
                        "-vf",
                        "showinfo",
                        "-f",
                        "null",
                        "-",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                match = re.search(r"pts_time:(\d+(?:\.\d+)?)", frame.stderr)
                self.assertIsNotNone(match)
                self.assertEqual(
                    float(cast("re.Match[str]", match).group(1)), expected_pts
                )

            embedded = subprocess.run(  # noqa: S603 - same subtitle extraction as importer
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-copyts",
                    "-i",
                    str(video),
                    "-map",
                    "0:2",
                    "-f",
                    "srt",
                    "-",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            absolute = normalize_subtitles(embedded.stdout, "srt")
            self.assertEqual((absolute[0].start, absolute[0].end), (5.5, 6.5))
            relative = ImportService._relative_cues(
                absolute, ImportService._video_origin(video)
            )
            self.assertEqual((relative[0].start, relative[0].end), (0.5, 1.5))

            wav = root / "selected.wav"
            _ = subprocess.run(  # noqa: S603 - same selected-audio extraction as importer
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-i",
                    str(video),
                    "-map",
                    "0:1",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(wav),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            duration = subprocess.run(  # noqa: S603 - inspect the local WAV
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(wav),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertAlmostEqual(float(duration.stdout.strip()), 1.0, places=2)
            offset = ImportService._stream_origin(video, 1) - ImportService._video_origin(
                video
            )
            self.assertEqual(offset, 2.0)
            asr = CaptionCue(start=0.1, end=0.4, text="spoken")
            self.assertEqual((asr.start + offset, asr.end + offset), (2.1, 2.4))
