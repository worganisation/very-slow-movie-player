"""Persistent, offline media catalogue with atomic import promotion."""

# ruff: noqa: S404, S603 - ffprobe uses a fixed executable and argument list.

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Generator

from storage import connect

type ImportStatus = Literal["importing", "ready", "failed"]
type JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class LibraryItem:
    """One source version and its local playback assets."""

    id: str
    title: str
    video_path: Path | None
    captions_path: Path | None
    status: ImportStatus
    error: str | None
    source: str
    source_id: str
    version_id: str
    progress: float
    caption_status: str = "missing"
    caption_error: str | None = None


class MediaLibrary:
    """Own import records and files without changing storage schema version 1."""

    def __init__(self, database: Path, root: Path) -> None:
        self.database: Path = database
        self.root: Path = root

    def initialize(self) -> None:
        """Create independent tables and mark interrupted imports retryable."""
        self.root.mkdir(parents=True, exist_ok=True)
        connection = connect(self.database)
        try:
            with connection:
                _ = connection.execute("""CREATE TABLE IF NOT EXISTS media_library (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('importing', 'ready', 'failed')),
                    progress REAL NOT NULL DEFAULT 0 CHECK(progress >= 0 AND progress <= 1),
                    error TEXT,
                    video_path TEXT,
                    captions_path TEXT,
                    caption_status TEXT NOT NULL DEFAULT 'missing',
                    caption_error TEXT,
                    UNIQUE(source, source_id, version_id)
                )""")
        finally:
            connection.close()
        try:
            with self.import_lock():
                connection = connect(self.database)
                try:
                    with connection:
                        _ = connection.execute("""UPDATE media_library
                            SET status='failed', error='Import interrupted; retry to resume',
                            progress=0 WHERE status='importing'""")
                finally:
                    connection.close()
        except BlockingIOError:
            pass  # An active importer owns recovery until it finishes.

    @contextmanager
    def import_lock(self) -> Generator[None]:
        """Allow one importer across processes; prevent staging and recovery races."""
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".imports.lock").open("a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def identity(source: str, source_id: str, version_id: str) -> str:
        """Return a stable opaque ID for one distinct source version."""
        payload = json.dumps([source, source_id, version_id], separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()[:24]

    def list_items(self) -> list[LibraryItem]:
        """List all imports, including failures and in-progress records."""
        connection = connect(self.database)
        try:
            rows = cast(
                "list[tuple[object, ...]]",
                connection.execute(
                    "SELECT * FROM media_library ORDER BY title, id"
                ).fetchall(),
            )
            return [self._item(row) for row in rows]
        finally:
            connection.close()

    def list_ready(self) -> list[LibraryItem]:
        """List only intact, locally playable imports."""
        return [
            item
            for item in self.list_items()
            if item.status == "ready"
            and item.video_path is not None
            and item.video_path.is_file()
        ]

    def get(self, media_id: str) -> LibraryItem:
        """Fetch an import or raise ``KeyError``."""
        connection = connect(self.database)
        try:
            row = cast(
                "tuple[object, ...] | None",
                connection.execute(
                    "SELECT * FROM media_library WHERE id=?", (media_id,)
                ).fetchone(),
            )
            if row is None:
                raise KeyError(media_id)
            return self._item(row)
        finally:
            connection.close()

    @staticmethod
    def _item(row: tuple[object, ...]) -> LibraryItem:
        return LibraryItem(
            id=cast("str", row[0]),
            source=cast("str", row[1]),
            source_id=cast("str", row[2]),
            version_id=cast("str", row[3]),
            title=cast("str", row[4]),
            status=cast("ImportStatus", row[5]),
            progress=cast("float", row[6]),
            error=cast("str | None", row[7]),
            video_path=Path(cast("str", row[8])) if row[8] else None,
            captions_path=Path(cast("str", row[9])) if row[9] else None,
            caption_status=cast("str", row[10]),
            caption_error=cast("str | None", row[11]),
        )

    def begin(
        self, source: str, source_id: str, version_id: str, title: str
    ) -> LibraryItem:
        """Start or retry an import, discarding only its stale staging files."""
        media_id = self.identity(source, source_id, version_id)
        existing = self._get_optional(media_id)
        if (
            existing is not None
            and existing.status == "ready"
            and existing.video_path is not None
            and existing.video_path.is_file()
        ):
            return existing
        if existing is not None and self._recover_promoted(media_id):
            return self.get(media_id)
        shutil.rmtree(self.staging_dir(media_id), ignore_errors=True)
        self.staging_dir(media_id).mkdir(parents=True)
        connection = connect(self.database)
        try:
            with connection:
                _ = connection.execute(
                    """INSERT INTO media_library
                    (id, source, source_id, version_id, title, status, progress)
                    VALUES (?, ?, ?, ?, ?, 'importing', 0)
                    ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                    status='importing', progress=0, error=NULL,
                    video_path=NULL, captions_path=NULL,
                    caption_status='missing', caption_error=NULL""",
                    (media_id, source, source_id, version_id, title),
                )
        finally:
            connection.close()
        return self.get(media_id)

    def set_progress(self, media_id: str, fraction: float) -> None:
        """Persist a bounded download fraction for status consumers."""
        if not 0 <= fraction <= 1:
            raise ValueError("Progress must be between zero and one")
        connection = connect(self.database)
        try:
            with connection:
                _ = connection.execute(
                    "UPDATE media_library SET progress=? WHERE id=? AND status='importing'",
                    (fraction, media_id),
                )
        finally:
            connection.close()

    def fail(self, media_id: str, error: str) -> None:
        """Keep a visible retryable record after an import error."""
        connection = connect(self.database)
        try:
            with connection:
                _ = connection.execute(
                    """UPDATE media_library
                    SET status='failed', error=?, progress=0 WHERE id=?""",
                    (error[:500], media_id),
                )
        finally:
            connection.close()
        shutil.rmtree(self.staging_dir(media_id), ignore_errors=True)

    def staging_dir(self, media_id: str) -> Path:
        """Return the private staging directory for an import."""
        return self.root / ".staging" / media_id

    def promote(
        self,
        media_id: str,
        video: Path,
        captions: Path | None = None,
        caption_error: str | None = None,
    ) -> LibraryItem:
        """Validate staged assets, then atomically publish one complete directory."""
        stage = self.staging_dir(media_id)
        self._validate_staged_assets(stage, video, captions)
        destination = self.root / media_id
        self._move_staging_dir(media_id, stage, destination)
        final_video = destination / video.name
        final_captions = destination / captions.name if captions is not None else None
        caption_status = self._caption_status(final_captions, caption_error)
        connection = connect(self.database)
        try:
            with connection:
                _ = connection.execute(
                    """UPDATE media_library SET status='ready',
                    error=NULL, progress=1, video_path=?, captions_path=?,
                    caption_status=?, caption_error=? WHERE id=?""",
                    (
                        str(final_video),
                        str(final_captions) if final_captions else None,
                        caption_status,
                        caption_error[:500] if caption_error else None,
                        media_id,
                    ),
                )
        finally:
            connection.close()
        return self.get(media_id)

    def _validate_staged_assets(
        self, stage: Path, video: Path, captions: Path | None
    ) -> None:
        if video.parent != stage or (captions is not None and captions.parent != stage):
            raise ValueError("Import assets must be in their staging directory")
        self.probe(video)
        if captions is not None and (
            not captions.is_file() or captions.stat().st_size == 0
        ):
            raise ValueError("Caption file is missing or empty")

    def _move_staging_dir(self, media_id: str, stage: Path, destination: Path) -> None:
        backup = self.root / f".{media_id}.backup"
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            _ = destination.rename(backup)
        try:
            _ = stage.rename(destination)
        except OSError:
            if backup.exists():
                _ = backup.rename(destination)
            raise
        shutil.rmtree(backup, ignore_errors=True)

    @staticmethod
    def _caption_status(captions: Path | None, error: str | None) -> str:
        if captions is not None:
            return "ready"
        if error:
            return "failed"
        return "missing"

    @staticmethod
    def probe(video: Path) -> None:
        """Require a readable nonempty video stream before publication."""
        if not video.is_file() or video.stat().st_size == 0:
            raise ValueError("Downloaded video is empty or missing")
        entries = "stream=codec_type,duration,avg_frame_rate:stream_disposition=attached_pic:format=duration"
        result = subprocess.run(
            [
                "/usr/bin/env",
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                entries,
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            raise ValueError("Downloaded video failed ffprobe integrity check")
        data = cast("JsonObject", json.loads(result.stdout))
        streams = data.get("streams")
        typed_streams = (
            [
                cast("JsonObject", stream)
                for stream in cast("list[object]", streams or [])
                if isinstance(stream, dict)
            ]
            if isinstance(streams, list)
            else []
        )
        viable = [
            stream
            for stream in typed_streams
            if stream.get("codec_type") == "video"
            and not MediaLibrary._attached_picture(stream)
        ]
        if not viable:
            raise ValueError("Downloaded file contains no video stream")
        format_data = data.get("format")
        duration = (
            cast("JsonObject", format_data).get("duration")
            if isinstance(format_data, dict)
            else None
        )
        if duration is None:
            duration = viable[0].get("duration")
        if (
            duration is None
            or not math.isfinite(float(str(duration)))
            or float(str(duration)) <= 0
        ):
            raise ValueError("Downloaded video has no positive duration")
        frame_rate = str(viable[0].get("avg_frame_rate") or "0")
        try:
            parsed_rate = Fraction(frame_rate)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError("Downloaded video has invalid frame rate") from exc
        if parsed_rate <= 0:
            raise ValueError("Downloaded video has no positive frame rate")

    @staticmethod
    def _attached_picture(stream: dict[str, object]) -> bool:
        disposition = stream.get("disposition")
        return (
            bool(cast("JsonObject", disposition).get("attached_pic"))
            if isinstance(disposition, dict)
            else False
        )

    def _recover_promoted(self, media_id: str) -> bool:
        """Finish a prior promotion interrupted between rename and DB commit."""
        destination = self.root / media_id
        videos = list(destination.glob("video.*")) if destination.is_dir() else []
        if len(videos) != 1:
            return False
        try:
            self.probe(videos[0])
        except (ValueError, OSError, subprocess.SubprocessError):
            return False
        captions = destination / "captions.json"
        connection = connect(self.database)
        try:
            with connection:
                _ = connection.execute(
                    """UPDATE media_library SET status='ready',
                    error=NULL, progress=1, video_path=?, captions_path=?,
                    caption_status=?, caption_error=NULL WHERE id=?""",
                    (
                        str(videos[0]),
                        str(captions) if captions.is_file() else None,
                        "ready" if captions.is_file() else "missing",
                        media_id,
                    ),
                )
        finally:
            connection.close()
        return True

    def _get_optional(self, media_id: str) -> LibraryItem | None:
        try:
            return self.get(media_id)
        except KeyError:
            return None
