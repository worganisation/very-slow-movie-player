"""Hardware-free subtitle and ASR cache behavior tests."""

# ruff: noqa: PT009, PT027 - unittest is available without test dependencies.

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from very_slow_movie_player.captions import (
    CaptionCue,
    CaptionTrack,
    caption_at,
    normalize_subtitles,
    transcribe_media,
)


class CaptionTests(unittest.TestCase):
    """Verify parsing, timing, and cache invalidation without model execution."""

    def test_model_rejects_invalid_intervals(self) -> None:
        """Cues must have finite, forward timestamps."""
        for start, end in [(1, 1), (2, 1), (-1, 1), (0, float("inf"))]:
            with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
                _ = CaptionCue(start=start, end=end, text="x")

    def test_srt_and_half_open_timing(self) -> None:
        """Text and markup normalize while silence remains blank."""
        cues = normalize_subtitles(
            "1\n00:00:01,000 --> 00:00:02,000\n<i>Hello &amp; bye</i>\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nSecond line\n",
            "srt",
        )
        self.assertEqual([cue.text for cue in cues], ["Hello & bye", "Second line"])
        self.assertEqual(caption_at(cues, 1), "Hello & bye")
        self.assertEqual(caption_at(cues, 2), "")
        self.assertEqual(caption_at(cues, 3.5, offset=0.5), "Second line")

    def test_vtt_space_only_payload_line_stays_in_cue(self) -> None:
        """YouTube's space-only payload lines do not separate cue blocks."""
        cues = normalize_subtitles(
            "WEBVTT\nKind: captions\nLanguage: en\n\n"
            "00:00:03.679 --> 00:00:08.709 align:start position:0%\n"
            " \nyou're<00:00:03.919><c> a wizard</c>\n\n"
            "00:00:08.719 --> 00:00:13.230\nHarry\n \n\n",
            "vtt",
            rolling=True,
        )
        self.assertEqual([cue.text for cue in cues], ["you're a wizard", "Harry"])
        self.assertAlmostEqual(cues[0].start, 3.679)
        self.assertAlmostEqual(cues[1].end, 13.230)

    def test_vtt_rolling_caption_dedup(self) -> None:
        """Only verified overlapping rolling cues remove repeated prefix words."""
        cues = normalize_subtitles(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello world\n\n"
            "00:00:02.000 --> 00:00:03.000\nHello world again\n",
            "vtt",
        )
        self.assertEqual([cue.text for cue in cues], ["Hello world", "Hello world again"])
        cues = normalize_subtitles(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello world\n\n"
            "00:00:02.000 --> 00:00:03.000\nHello world again\n",
            "vtt",
            rolling=True,
        )
        self.assertEqual([cue.text for cue in cues], ["Hello world", "Hello world again"])
        self.assertEqual(cues[0].end, 2.0)

    def test_rolling_duplicate_covers_full_later_interval(self) -> None:
        """Repeated rolling text stays visible through the later cue's end."""
        cues = normalize_subtitles(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nHello\n\n"
            "00:00:02.000 --> 00:00:04.000\nHello\n",
            "vtt",
            rolling=True,
        )
        self.assertEqual(caption_at(cues, 3.5), "Hello")

    def test_speaker_lines_remain_separate(self) -> None:
        """Two dialogue speakers must retain their visible line boundary."""
        cues = normalize_subtitles(
            "1\n00:00:01,000 --> 00:00:02,000\n- Alice\n- Bob\n",
            "srt",
        )
        self.assertEqual(cues[0].text, "- Alice\n- Bob")

    def test_repeated_reply_is_preserved(self) -> None:
        """Human VTT may legitimately repeat the same word."""
        cues = normalize_subtitles(
            "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nNo.\n\n"
            "00:00:02.000 --> 00:00:03.000\nNo.\n",
            "vtt",
        )
        self.assertEqual([cue.text for cue in cues], ["No.", "No."])

    def test_malformed_track_fails_and_timestamp_ranges_are_checked(self) -> None:
        """An invalid cue cannot silently become a partial complete track."""
        with self.assertRaises(ValueError):
            _ = normalize_subtitles(
                "1\n00:00:01,000 --> 00:00:02,000\nValid\n\n"
                "2\n00:00:61,000 --> 00:00:62,000\nBroken\n",
                "srt",
            )
        with self.assertRaises(ValueError):
            _ = normalize_subtitles("1\n00:60:01,000 --> 00:60:02,000\nBroken", "srt")

    def test_ass_events(self) -> None:
        """ASS overrides are removed and explicit line breaks remain."""
        cues = normalize_subtitles(
            "[Script Info]\nTitle: Sample\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\i1}Good{\\i0}\\Nday\n",
            "ass",
        )
        self.assertEqual(cues[0].text, "Good\nday")

    def test_cache_reuses_matching_media_and_recomputes_after_change(self) -> None:
        """A changed media digest cannot reuse stale ASR output."""
        track = CaptionTrack(
            cues=[CaptionCue(start=0, end=1, text="word")],
            language="en",
            source="local:small",
        )
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "media.mp4"
            cache = Path(directory) / "captions.json"
            media.write_bytes(b"first")
            with patch(
                "very_slow_movie_player.captions._transcribe_local", return_value=track
            ) as run:
                self.assertEqual(
                    transcribe_media(media, cache, backend="local", language="en"),
                    track,
                )
                self.assertEqual(
                    transcribe_media(media, cache, backend="local", language="en"),
                    track,
                )
                self.assertEqual(run.call_count, 1)
                media.write_bytes(b"second")
                _ = transcribe_media(media, cache, backend="local", language="en")
                self.assertEqual(run.call_count, 2)

    def test_local_asr_uses_vad_word_timing_on_whole_media(self) -> None:
        """The local model sees one complete media path and emits timed words."""
        calls: list[tuple[str, dict[str, object]]] = []

        class FakeModel:
            def __init__(self, name: str) -> None:
                self.assert_name = name

            @staticmethod
            def transcribe(audio: str, **kwargs: object) -> tuple[list[object], object]:
                calls.append((audio, kwargs))
                words = [
                    SimpleNamespace(start=0.1, end=0.4, word=" Hello"),
                    SimpleNamespace(start=0.4, end=0.8, word=" world"),
                    SimpleNamespace(start=2.0, end=2.3, word=" Again"),
                ]
                return [SimpleNamespace(words=words)], object()

        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "media.mp4"
            media.write_bytes(b"whole media")
            module = SimpleNamespace(WhisperModel=FakeModel)
            with patch(
                "very_slow_movie_player.captions.importlib.import_module",
                return_value=module,
            ):
                track = transcribe_media(
                    media,
                    Path(directory) / "cache.json",
                    backend="local:tiny",
                    language="en",
                )
        self.assertEqual([cue.text for cue in track.cues], ["Hello world", "Again"])
        self.assertEqual(
            [(cue.start, cue.end) for cue in track.cues], [(0.1, 0.8), (2.0, 2.3)]
        )
        self.assertEqual(
            calls,
            [
                (
                    str(media),
                    {
                        "language": "en",
                        "vad_filter": True,
                        "word_timestamps": True,
                    },
                )
            ],
        )

    def test_unconfigured_backend_is_explicit(self) -> None:
        """Disabled and remote modes do not imply a deployed ASR service."""
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            _ = transcribe_media(
                Path("unused"), Path("unused"), backend="disabled", language="en"
            )
        with self.assertRaisesRegex(RuntimeError, "no transcription service"):
            _ = transcribe_media(
                Path("unused"), Path("unused"), backend="remote", language="en"
            )
