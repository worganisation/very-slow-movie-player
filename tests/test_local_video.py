"""Checks for local video playback without E-paper hardware."""

from __future__ import annotations

from importlib import reload
from json import loads
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import call, patch

import main as player
from PIL import Image
from utils import const


class LocalVideoTests(TestCase):
    """Verify metadata, progress, and startup behavior."""

    @staticmethod
    def test_constants_load_without_youtube_credentials() -> None:
        """Local playback can load shared constants without YouTube keys."""
        environment = {
            key: value
            for key, value in environ.items()
            if key not in {"YT_API_KEY", "YT_PLAYLIST_ID"}
        }
        with patch.dict("os.environ", environment, clear=True):
            reload(const)

    @staticmethod
    def test_display_darkens_midtones_before_dithering() -> None:
        """Midtones become black at the default gamma while endpoints stay fixed."""
        with TemporaryDirectory() as directory:
            frame = Path(directory) / "frame.png"
            image = Image.new("L", (3, 1))
            image.putdata([0, 160, 255])
            image.save(frame)
            with (
                patch.dict("os.environ", {"VSMP_IMAGE_GAMMA": "1.7"}),
                patch.object(player, "format_image", return_value=frame),
                patch.object(
                    player.DISPLAY,
                    "getbuffer",
                    side_effect=lambda value: value,
                ),
                patch.object(player.DISPLAY, "display") as display,
                patch.object(player, "sleep"),
            ):
                player.display_image(frame, 0)

        assert list(display.call_args.args[0].getdata()) == [0, 0, 255]

    @staticmethod
    def test_extract_frame_passes_ffmpeg_a_filename_and_real_timestamp() -> None:
        """ffmpeg-python needs a string output filename and a rate-based seek time."""
        video = Path("video.mp4")
        output = Path("frame.jpg")
        with patch.object(player, "ffmpeg_input") as ffmpeg_input:
            assert (
                player.extract_frame(video, 12, fps=60, extract_output_path=output)
                == output
            )

        ffmpeg_input.assert_called_once_with(video, ss="0.200000")
        ffmpeg_input.return_value.output.assert_called_once_with(
            "frame.jpg",
            vframes=1,
        )

    @staticmethod
    def test_video_metadata_uses_real_frame_rate() -> None:
        """A 60 fps source must seek at its own frame rate."""
        with patch.object(
            player,
            "probe",
            return_value={
                "streams": [
                    {
                        "codec_type": "video",
                        "avg_frame_rate": "60000/1001",
                        "nb_frames": "1685",
                    },
                ],
            },
        ):
            frame_count, fps = player.video_metadata(Path("video.mp4"))

        assert frame_count == 1685
        assert abs(fps - (60000 / 1001)) < 0.000001

    @staticmethod
    def test_video_metadata_estimates_missing_frame_count() -> None:
        """Some MP4 files report N/A for the frame count."""
        with patch.object(
            player,
            "probe",
            return_value={
                "streams": [
                    {
                        "codec_type": "video",
                        "avg_frame_rate": "24/1",
                        "nb_frames": "N/A",
                        "duration": "2.1",
                    },
                ],
            },
        ):
            assert player.video_metadata(Path("video.mp4")) == (51, 24.0)

    @staticmethod
    def test_progress_uses_json_path_key() -> None:
        """A displayed frame must be recorded with a JSON-compatible key."""
        with TemporaryDirectory() as directory:
            progress_log = Path(directory) / "progress.json"
            progress_log.write_text("{}")
            with patch.object(const, "PROGRESS_LOG", progress_log):
                player.set_progress(Path("/movies/video.mp4"), 12, 120)

            assert loads(progress_log.read_text()) == {
                "/movies/video.mp4": {"current": 12, "total": 120},
            }

    @staticmethod
    def test_completed_video_restarts_from_first_frame() -> None:
        """The next service run loops a completed video from frame zero."""
        with TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.touch()
            with (
                patch.dict("os.environ", {"ALWAYS_RESTART_VIDEOS": "false"}),
                patch.object(player, "video_metadata", return_value=(24, 24.0)),
                patch.object(player, "get_progress", return_value=24),
                patch.object(player, "set_progress") as set_progress,
                patch.object(
                    player,
                    "extract_frame",
                    return_value=video,
                ) as extract_frame,
                patch.object(player, "display_image"),
            ):
                player.play_video(video)

            extract_frame.assert_has_calls(
                [call(video, 0, fps=24.0), call(video, 12, fps=24.0)],
            )
            assert set_progress.call_args == call(video, 24, 24)

    @staticmethod
    def test_main_creates_progress_directory_and_plays_selected_video() -> None:
        """Startup must use the configured file without accessing Google Photos."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mp4"
            video.touch()
            progress_log = root / ".media" / "progress_log.json"
            with (
                patch.dict("os.environ", {"VSMP_VIDEO_PATH": str(video)}),
                patch.object(const, "PROGRESS_LOG", progress_log),
                patch.object(player, "play_video") as play_video,
                patch.object(player.DISPLAY, "init"),
                patch.object(player.DISPLAY, "clear"),
                patch.object(player.DISPLAY, "sleep"),
                patch.object(player.DISPLAY.pi, "module_exit"),
            ):
                player.main()

            play_video.assert_called_once_with(video)
            assert progress_log.read_text() == "{}"
