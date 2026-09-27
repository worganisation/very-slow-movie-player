"""Durable progress storage for local video playback."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - runtime Path default
from typing import NotRequired, TypedDict, cast

from storage import connect, initialize

from .logging import logger


class ProgressInfo(TypedDict):
    """Last displayed frame and optional total frame count."""

    current: int
    total: NotRequired[int]


def load_progress() -> dict[str, ProgressInfo]:
    """Return all saved media positions after one-time migration."""
    initialize()
    connection = connect()
    try:
        rows = cast(
            "list[tuple[str, int, int | None]]",
            connection.execute(
                "SELECT media_path, current, total FROM progress"
            ).fetchall(),
        )
        return {
            path: (
                {"current": current, "total": total}
                if total is not None
                else {"current": current}
            )
            for path, current, total in rows
        }
    finally:
        connection.close()


def get_progress(video_path: Path, default: int = 0) -> int:
    """Return the last displayed frame, or the default for a new video."""
    logger.info("Getting progress for `{}`", video_path)
    progress = load_progress().get(video_path.as_posix())
    return progress["current"] if progress is not None else default


def set_progress(
    video_path: Path,
    current_frame: int,
    frame_count: int | None = None,
) -> None:
    """Record the last displayed frame without losing other videos' progress."""
    if current_frame < 0 or (frame_count is not None and frame_count < 0):
        raise ValueError("Progress frames must be nonnegative")

    initialize()
    connection = connect()
    try:
        with connection:
            _ = connection.execute(
                """INSERT INTO progress(media_path, current, total) VALUES (?, ?, ?)
                ON CONFLICT(media_path) DO UPDATE SET
                    current=excluded.current, total=excluded.total
                WHERE current IS NOT excluded.current OR total IS NOT excluded.total""",
                (video_path.as_posix(), current_frame, frame_count),
            )
    finally:
        connection.close()
