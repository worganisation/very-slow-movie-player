"""Checks for local video playback without E-paper hardware."""

from __future__ import annotations

from json import loads
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import call, patch

from very_slow_movie_player import main as player


class LocalVideoTests(TestCase):
    """Verify metadata, progress, and startup behavior."""

    def test_video_metadata_uses_real_frame_rate(self) -> None:
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

        self.assertEqual(frame_count, 1685)
        self.assertAlmostEqual(fps, 60000 / 1001)

    def test_video_metadata_estimates_missing_frame_count(self) -> None:
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
            self.assertEqual(player.video_metadata(Path("video.mp4")), (51, 24.0))

    def test_progress_uses_json_path_key(self) -> None:
        """A displayed frame must be recorded with a JSON-compatible key."""
        with TemporaryDirectory() as directory:
            progress_log = Path(directory) / "progress.json"
            progress_log.write_text("{}")
            with patch.object(player.const, "PROGRESS_LOG", progress_log):
                player.set_progress(Path("/movies/video.mp4"), 12, 120)

            self.assertEqual(
                loads(progress_log.read_text()),
                {"/movies/video.mp4": {"current": 12, "total": 120}},
            )

    def test_completed_video_restarts_from_first_frame(self) -> None:
        """The next service run loops a completed video from frame zero."""
        with TemporaryDirectory() as directory:
            video = Path(directory) / "video.mp4"
            video.touch()
            with (
                patch.dict("os.environ", {"ALWAYS_RESTART_VIDEOS": "false"}),
                patch.object(player, "video_metadata", return_value=(24, 24.0)),
                patch.object(player, "get_progress", return_value=24),
                patch.object(player, "set_progress") as set_progress,
                patch.object(player, "extract_frame", return_value=video) as extract_frame,
                patch.object(player, "display_image"),
            ):
                player.play_video(video)

            extract_frame.assert_has_calls(
                [call(video, 0, fps=24.0), call(video, 12, fps=24.0)],
            )
            self.assertEqual(set_progress.call_args, call(video, 24, 24))

    def test_main_creates_progress_directory_and_plays_selected_video(self) -> None:
        """Startup must use the configured file without accessing Google Photos."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "video.mp4"
            video.touch()
            progress_log = root / ".media" / "progress_log.json"
            with (
                patch.dict("os.environ", {"VSMP_VIDEO_PATH": str(video)}),
                patch.object(player.const, "PROGRESS_LOG", progress_log),
                patch.object(player, "play_video") as play_video,
                patch.object(player.DISPLAY, "init"),
                patch.object(player.DISPLAY, "clear"),
                patch.object(player.DISPLAY, "sleep"),
                patch.object(player.DISPLAY.pi, "module_exit"),
            ):
                player.main()

            play_video.assert_called_once_with(video)
            self.assertEqual(progress_log.read_text(), "{}")
