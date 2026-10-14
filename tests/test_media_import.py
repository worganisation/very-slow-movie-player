"""Exercise source selection and caption timestamp decisions."""

# ruff: noqa: PT009, PT027, S404, S603 - unittest and fixed local ffmpeg fixture.

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from captions import CaptionCue

# ruff: noqa: PT009, PT027 - unittest requires no extra test dependency.


class MediaImportTests(unittest.TestCase):
    def setUp(self) -> None:
        """Build an isolated service under synthetic eager settings."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        database = root / "state.sqlite3"
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
        service_class = importlib.import_module("media_import").ImportService
        initialize(
            database,
            progress_file=root / "absent.json",
            controls_file=root / "absent-controls.json",
        )
        library = library_class(database, root / "media")
        library.initialize()
        self.service = service_class(
            library,
            jellyfin_url="http://localhost:8096",
            jellyfin_api_key="test",
            jellyfin_user_id="user",
        )
        self.fixture = root / "fixture.mp4"
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
                str(self.fixture),
            ],
            check=True,
            capture_output=True,
        )

    def test_version_and_audio_language_must_be_explicit_when_ambiguous(self) -> None:
        """Multiple versions and language tracks require unambiguous choices."""
        item = {"Type": "Movie", "Name": "Film"}
        sources = [
            {"Id": "one", "MediaStreams": [{"Type": "Audio", "Language": "eng"}]},
            {"Id": "two", "MediaStreams": [{"Type": "Audio", "Language": "fra"}]},
        ]
        with (
            patch.object(self.service, "_jf_item", return_value=item),
            patch.object(self.service, "jellyfin_versions", return_value=sources),
            self.assertRaisesRegex(ValueError, "media_source_id"),
        ):
            self.service.import_jellyfin("film")
        with self.assertRaisesRegex(ValueError, "audio_language"):
            self.service._choose_audio([{"Language": "eng"}, {"Language": "fra"}], None)

    def test_forced_only_caption_is_skipped(self) -> None:
        """A forced only subtitle cannot represent the full dialogue."""
        with patch.object(
            self.service, "_asr_or_missing", return_value=(None, "unavailable")
        ):
            caption, error = self.service._jellyfin_captions(
                "film",
                "source",
                [
                    {
                        "Type": "Subtitle",
                        "Codec": "subrip",
                        "Language": "eng",
                        "IsForced": True,
                        "Index": 1,
                    }
                ],
                Path("unused"),
                Path("unused"),
                None,
            )
        self.assertIsNone(caption)
        self.assertEqual(error, "unavailable")

    def test_nonzero_stream_origin_maps_to_frame_zero(self) -> None:
        """Embedded cue times must be relative to the first video frame."""
        cues = [CaptionCue(start=10.5, end=12.0, text="hello")]
        adjusted = self.service._relative_cues(cues, 10.0)
        self.assertEqual((adjusted[0].start, adjusted[0].end), (0.5, 2.0))
        early_audio = [CaptionCue(start=0.5, end=2.5, text="early")]
        clipped = self.service._relative_cues(early_audio, 2.0)
        self.assertEqual((clipped[0].start, clipped[0].end), (0.0, 0.5))

    def test_youtube_import_retries_corrupt_download_and_separates_settings(self) -> None:
        """A corrupt transfer stays failed and a retry publishes the valid version."""
        info = {
            "id": "test-video",
            "title": "Example",
            "format_id": "22",
            "duration": 1,
            "upload_date": "20260101",
        }
        url = "https://www.youtube.com/watch?v=test-video"

        def corrupt(_url: str, stage: Path) -> None:
            _ = (stage / "video.mp4").write_bytes(b"corrupt")

        def valid(_url: str, stage: Path) -> None:
            _ = shutil.copyfile(self.fixture, stage / "video.mp4")

        with (
            patch.object(self.service, "_yt_json", return_value=info),
            patch.object(
                self.service, "_youtube_captions", return_value=(None, "no captions")
            ),
            patch.object(self.service, "_yt_download", side_effect=corrupt),
            self.assertRaises(ValueError),
        ):
            self.service.import_youtube(url)
        failed = self.service.library.list_items()[0]
        self.assertEqual(failed.status, "failed")
        with (
            patch.object(self.service, "_yt_json", return_value=info),
            patch.object(
                self.service, "_youtube_captions", return_value=(None, "no captions")
            ),
            patch.object(self.service, "_yt_download", side_effect=valid),
        ):
            ready = self.service.import_youtube(url)
        self.assertEqual(ready.id, failed.id)
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.caption_status, "failed")

        service_class = importlib.import_module("media_import").ImportService
        changed = service_class(
            self.service.library, caption_language="fr", asr_backend="local"
        )
        with (
            patch.object(changed, "_yt_json", return_value=info),
            patch.object(
                changed, "_youtube_captions", return_value=(None, "no captions")
            ),
            patch.object(changed, "_yt_download", side_effect=valid),
        ):
            second = changed.import_youtube(url)
        self.assertNotEqual(second.id, ready.id)

    def test_external_jellyfin_captions_keep_media_relative_timestamps(self) -> None:
        """External subtitle API timing is not shifted by video stream origin."""
        stage = self.service.library.root / "caption-test"
        stage.mkdir()
        subtitle = "1\n00:00:00,500 --> 00:00:01,500\nHello\n"
        with (
            patch.object(self.service, "_jf_text", return_value=subtitle),
            patch.object(self.service, "_video_origin", return_value=10.0),
        ):
            path, error = self.service._jellyfin_captions(
                "film",
                "source",
                [
                    {
                        "Type": "Subtitle",
                        "Codec": "subrip",
                        "Language": "eng",
                        "IsExternal": True,
                        "Index": 2,
                    }
                ],
                self.fixture,
                stage,
                None,
            )
        self.assertIsNone(error)
        self.assertIsNotNone(path)
        track_class = importlib.import_module("captions").CaptionTrack
        track = track_class.model_validate_json(path.read_text())
        self.assertEqual(track.cues[0].start, 0.5)
        with (
            patch.object(self.service, "_jf_text", return_value=subtitle),
            patch.object(self.service, "_video_origin", return_value=10.0),
        ):
            delivered, _ = self.service._jellyfin_captions(
                "film",
                "source",
                [
                    {
                        "Type": "Subtitle",
                        "Codec": "subrip",
                        "Language": "eng",
                        "DeliveryUrl": "/Videos/film/source/Subtitles/2/0/Stream.subrip",
                    }
                ],
                self.fixture,
                stage,
                None,
            )
        self.assertIsNotNone(delivered)

    def test_youtube_human_caption_precedes_auto(self) -> None:
        """A usable human track avoids automatic-caption download."""
        stage = self.service.library.root / "subtitle-test"
        stage.mkdir()
        info = {
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {"en": [{"ext": "vtt"}]},
        }

        def write_subtitle(command: list[str], **_kwargs: object) -> object:
            output = Path(command[command.index("--output") + 1])
            path = output.parent / "subtitle.en.vtt"
            _ = path.write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHuman speech\n",
                encoding="utf-8",
            )
            return object()

        with patch("media_import.subprocess.run", side_effect=write_subtitle) as run:
            path, error = self.service._youtube_captions(
                "https://www.youtube.com/watch?v=id", info, stage, self.fixture
            )
        self.assertIsNone(error)
        self.assertIsNotNone(path)
        self.assertEqual(run.call_count, 1)

    def test_youtube_auto_caption_uses_own_output_after_human_failure(self) -> None:
        """A stale human file cannot masquerade as automatic captions."""
        stage = self.service.library.root / "fallback-test"
        stage.mkdir()
        info = {
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {"en": [{"ext": "vtt"}]},
        }

        def write_subtitle(command: list[str], **_kwargs: object) -> object:
            output = Path(command[command.index("--output") + 1])
            if "subtitles" in output.parts:
                _ = (output.parent / "subtitle.en.vtt").write_text("bad subtitle")
                raise subprocess.CalledProcessError(1, command)
            _ = (output.parent / "subtitle.en.vtt").write_text(
                "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nAuto speech\n",
                encoding="utf-8",
            )
            return object()

        with patch("media_import.subprocess.run", side_effect=write_subtitle):
            path, error = self.service._youtube_captions(
                "https://www.youtube.com/watch?v=id", info, stage, self.fixture
            )
        self.assertIsNone(error)
        self.assertIsNotNone(path)
        track_class = importlib.import_module("captions").CaptionTrack
        track = track_class.model_validate_json(path.read_text())
        self.assertEqual(track.source, "youtube-auto")
        self.assertEqual(track.cues[0].text, "Auto speech")

    def test_jellyfin_import_downloads_selected_version_and_captions(self) -> None:
        """The requested source ID publishes a real offline video and text track."""
        item = {
            "Type": "Episode",
            "Name": "Pilot",
            "SeriesName": "Example",
            "ParentIndexNumber": 1,
            "IndexNumber": 2,
        }
        sources = [
            {
                "Id": "version-a",
                "Container": "mp4",
                "ETag": "revision-a",
                "MediaStreams": [
                    {"Type": "Audio", "Language": "eng", "Index": 1},
                    {
                        "Type": "Subtitle",
                        "Codec": "subrip",
                        "Language": "eng",
                        "IsExternal": True,
                        "Index": 2,
                    },
                ],
            }
        ]
        subtitle = "1\n00:00:00,500 --> 00:00:01,500\nLine\n"

        def download(_item: str, _source: str, target: Path) -> None:
            _ = shutil.copyfile(self.fixture, target)

        with (
            patch.object(self.service, "_jf_item", return_value=item),
            patch.object(self.service, "jellyfin_versions", return_value=sources),
            patch.object(self.service, "_jf_download", side_effect=download),
            patch.object(self.service, "_jf_text", return_value=subtitle),
        ):
            ready = self.service.import_jellyfin(
                "episode", "version-a", audio_language="eng"
            )
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.caption_status, "ready")
        self.assertEqual(ready.title, "Example S01E02 — Pilot")
        self.assertTrue(ready.video_path and ready.video_path.is_file())
        self.assertTrue(ready.captions_path and ready.captions_path.is_file())

    def test_asr_does_not_transcribe_a_different_spoken_language(self) -> None:
        """Missing subtitles cannot silently force French speech into English ASR."""
        service_class = importlib.import_module("media_import").ImportService
        service = service_class(
            self.service.library, caption_language="en", asr_backend="local"
        )
        with patch("media_import.transcribe_media") as transcribe:
            path, error = service._asr_or_missing(
                self.fixture,
                self.service.library.root,
                {"Language": "fra", "Index": 1},
                [],
            )
            youtube_path, youtube_error = service._asr_or_missing(
                self.fixture,
                self.service.library.root,
                None,
                [],
                spoken_language="fr",
            )
        self.assertIsNone(path)
        self.assertIsNone(youtube_path)
        self.assertIn("cannot translate", error or "")
        self.assertIn("cannot translate", youtube_error or "")
        transcribe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
