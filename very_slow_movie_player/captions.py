"""Normalize timed subtitles and optionally cache local speech recognition."""

from __future__ import annotations

import hashlib
import html
import importlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Protocol, cast

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[,.](\d{1,3})")
_TAG = re.compile(r"<[^>]*>|\{\\[^}]*\}")
_SPACE = re.compile(r"[^\S\n]+")
_ASS_FIELDS = 10
_SPEECH_GAP_SECONDS = 0.5
_MAX_PHRASE_SECONDS = 5.0
_MAX_PHRASE_CHARS = 60
_CLOCK_BASE = 60


class CaptionCue(BaseModel):
    """Text visible on the half-open interval ``[start, end)``."""

    start: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    end: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    text: str

    @model_validator(mode="after")
    def valid_interval(self) -> CaptionCue:
        """Reject zero-length and backwards cues."""
        if self.end <= self.start:
            raise ValueError("caption end must follow start")
        return self


class CaptionTrack(BaseModel):
    """Cues together with their language and provenance."""

    cues: list[CaptionCue]
    language: str
    source: str

    @field_validator("language", "source")
    @classmethod
    def nonempty(cls, value: str) -> str:
        """Require useful track metadata."""
        if not value.strip():
            raise ValueError("caption metadata must be nonempty")
        return value


def _seconds(value: str) -> float:
    match = _TIME.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"invalid subtitle timestamp: {value}")
    hours, minutes, seconds, fraction = match.groups()
    if int(seconds) >= _CLOCK_BASE or (hours is not None and int(minutes) >= _CLOCK_BASE):
        raise ValueError(f"subtitle timestamp out of range: {value}")
    return (
        int(hours or 0) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(fraction.ljust(3, "0")) / 1000
    )


def _clean_text(value: str) -> str:
    value = value.replace(r"\N", "\n").replace(r"\n", "\n")
    lines = html.unescape(_TAG.sub("", value)).splitlines()
    return "\n".join(
        cleaned for line in lines if (cleaned := _SPACE.sub(" ", line).strip())
    )


def _rolling_dedup(cues: list[CaptionCue]) -> list[CaptionCue]:
    result: list[CaptionCue] = []
    for cue in cues:
        if result and cue.start < result[-1].end:
            previous = result[-1]
            words = previous.text.split()
            next_words = cue.text.split()
            prefix_matches = [word.casefold() for word in words] == [
                word.casefold() for word in next_words[: len(words)]
            ]
            if prefix_matches:
                if cue.start <= previous.start:
                    _ = result.pop()
                else:
                    result[-1] = previous.model_copy(update={"end": cue.start})
        result.append(cue)
    return result


def _ass_dialogue(line: str) -> CaptionCue | None:
    fields = line.partition(":")[2].split(",", 9)
    if len(fields) != _ASS_FIELDS:
        raise ValueError("malformed ASS dialogue")
    start, end = _seconds(fields[1]), _seconds(fields[2])
    cleaned = _clean_text(fields[9])
    return CaptionCue(start=start, end=end, text=cleaned) if cleaned else None


def _ass_cues(text: str) -> list[CaptionCue]:
    cues: list[CaptionCue] = []
    in_events = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.casefold() == "[events]"
        if in_events and stripped.startswith("Dialogue:"):
            cue = _ass_dialogue(stripped)
            if cue is not None:
                cues.append(cue)
    return cues


def _timed_block(block: str) -> CaptionCue | None:
    lines = block.splitlines()
    if not block.strip() or lines[0].startswith((
        "WEBVTT",
        "NOTE",
        "STYLE",
        "REGION",
        "X-TIMESTAMP-MAP",
    )):
        return None
    for index, line in enumerate(lines):
        if "-->" in line:
            left, right = line.split("-->", 1)
            start = _seconds(left)
            parts = right.strip().split()
            if not parts:
                raise ValueError("missing subtitle end timestamp")
            end = _seconds(parts[0])
            cleaned = _clean_text("\n".join(lines[index + 1 :]))
            return CaptionCue(start=start, end=end, text=cleaned) if cleaned else None
    raise ValueError("subtitle cue has no timing line")


def _text_cues(text: str) -> list[CaptionCue]:
    blocks = re.split(r"\n{2,}", text.replace("\r\n", "\n").lstrip("\ufeff"))
    cues: list[CaptionCue] = []
    for block in blocks:
        cue = _timed_block(block)
        if cue is not None:
            cues.append(cue)
    return cues


def normalize_subtitles(
    text: str,
    format: str,  # noqa: A002 - public API names the subtitle format.
    *,
    rolling: bool = False,
) -> list[CaptionCue]:
    """Read SRT, WebVTT or ASS events into ordered, clean cues.

    Malformed cue blocks raise rather than silently producing partial tracks.
    ``rolling=True`` is only for verified rolling auto-captions.
    """
    kind = format.lower().lstrip(".")
    if kind not in {"srt", "vtt", "webvtt", "ass", "ssa"}:
        raise ValueError(f"unsupported subtitle format: {format}")
    cues = _ass_cues(text) if kind in {"ass", "ssa"} else _text_cues(text)
    cues.sort(key=lambda cue: (cue.start, cue.end, cue.text))
    if not cues:
        raise ValueError("subtitle track has no usable cues")
    return _rolling_dedup(cues) if rolling and kind in {"vtt", "webvtt"} else cues


def caption_at(cues: Sequence[CaptionCue], timestamp: float, offset: float = 0) -> str:
    """Return active text at media time, with a subtitle timing offset."""
    if not math.isfinite(timestamp) or not math.isfinite(offset):
        raise ValueError("caption time and offset must be finite")
    effective_time = timestamp - offset
    active = [cue for cue in cues if cue.start <= effective_time < cue.end]
    active.sort(key=lambda cue: (cue.start, cue.end, cue.text))
    return "\n".join(cue.text for cue in active)


class _Word(Protocol):
    start: float
    end: float
    word: str


class _Segment(Protocol):
    words: list[_Word] | None


class _WhisperModel(Protocol):
    def transcribe(
        self, audio: str, *, language: str | None, vad_filter: bool, word_timestamps: bool
    ) -> tuple[Sequence[_Segment], object]:
        _ = self, audio, language, vad_filter, word_timestamps
        raise NotImplementedError


def _phrase_boundary(current: Sequence[_Word], word: _Word) -> bool:
    previous = current[-1]
    return (
        word.start - previous.end > _SPEECH_GAP_SECONDS
        or word.end - current[0].start > _MAX_PHRASE_SECONDS
        or len("".join(item.word for item in current)) + len(word.word)
        > _MAX_PHRASE_CHARS
        or previous.word.rstrip().endswith((".", "!", "?"))
    )


def _append_phrase(cues: list[CaptionCue], words: Sequence[_Word]) -> None:
    if words:
        text = _clean_text("".join(word.word for word in words))
        if text:
            cues.append(CaptionCue(start=words[0].start, end=words[-1].end, text=text))


def _word_cues(words: Sequence[_Word]) -> list[CaptionCue]:
    """Group consecutive word timestamps within one Whisper segment."""
    cues: list[CaptionCue] = []
    current: list[_Word] = []
    for word in words:
        if not _clean_text(word.word) or not word.end > word.start >= 0:
            continue
        if current and _phrase_boundary(current, word):
            _append_phrase(cues, current)
            current = []
        current.append(word)
    _append_phrase(cues, current)
    return cues


def _media_digest(media_path: Path) -> str:
    digest = hashlib.sha256()
    with media_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _transcribe_local(
    media_path: Path, *, model_name: str, language: str
) -> CaptionTrack:
    try:
        module = importlib.import_module("faster_whisper")
    except ImportError as exc:
        raise RuntimeError(
            "local captions require the optional faster-whisper package"
        ) from exc
    model_class = cast("Callable[[str], _WhisperModel]", module.WhisperModel)
    model = model_class(model_name)
    segments, _ = model.transcribe(
        str(media_path),
        language=None if language == "auto" else language,
        vad_filter=True,
        word_timestamps=True,
    )
    cues: list[CaptionCue] = []
    for segment in segments:
        cues.extend(_word_cues(segment.words or []))
    return CaptionTrack(cues=cues, language=language, source=f"local:{model_name}")


def transcribe_media(
    media_path: Path,
    cache_path: Path,
    *,
    backend: str,
    language: str,
    endpoint: str | None = None,
) -> CaptionTrack:
    """Transcribe a complete immutable media file once and reuse matching cache.

    ``backend='disabled'`` is the default safe mode. ``local`` or
    ``local:<model>`` enables faster-whisper when installed. No remote ASR endpoint
    is deployed in the GPU worker stack, so remote requests fail explicitly.
    """
    if backend == "disabled":
        raise RuntimeError("speech transcription is disabled")
    if backend == "remote":
        raise RuntimeError(
            "remote ASR is unavailable: no transcription service is deployed"
        )
    if not backend.startswith("local") or (
        backend != "local" and not backend.startswith("local:")
    ):
        raise ValueError(f"unsupported transcription backend: {backend}")
    model_name = backend.partition(":")[2] or "small"
    if not language.strip() or not model_name.strip():
        raise ValueError("transcription language and model must be nonempty")
    if endpoint is not None:
        raise ValueError("endpoint is only applicable to a configured remote backend")
    identity = {
        "sha256": _media_digest(media_path),
        "backend": "local",
        "model": model_name,
        "language": language,
    }
    if cache_path.is_file():
        try:
            record = TypeAdapter(dict[str, object]).validate_json(cache_path.read_bytes())
            if record.get("identity") == identity:
                return CaptionTrack.model_validate(record["track"])
        except (ValidationError, KeyError, OSError):
            pass
    track = _transcribe_local(media_path, model_name=model_name, language=language)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        {"identity": identity, "track": track.model_dump()}, ensure_ascii=False
    )
    descriptor, temporary = tempfile.mkstemp(prefix=".captions-", dir=cache_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            _ = output.write(data)
        _ = Path(temporary).replace(cache_path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return track
