"""Durable progress storage for local video playback."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - runtime Path default
from typing import NotRequired, TypedDict, cast

from storage import connect, initialize
from wg_utilities.loggers import get_streaming_logger

LOGGER = get_streaming_logger(__name__)


class ProgressInfo(TypedDict):
    """Last displayed frame and optional total frame count."""

    current: int
    total: NotRequired[int]


def _validate_entry(video_path: object, info: object) -> None:
    """Check one path and its saved frame counters."""
    if not isinstance(video_path, str) or not isinstance(info, dict):
        raise TypeError("Progress entries must map paths to objects")
    values = cast("dict[object, object]", info)
    current = values.get("current")
    if type(current) is not int:
        raise TypeError("Progress entries need an integer current frame")
    if current < 0:
        raise ValueError("Progress entries need a nonnegative current frame")
    if "total" in values:
        total = values["total"]
        if type(total) is not int:
            raise TypeError("Progress totals must be integers")
        if total < 0:
            raise ValueError("Progress totals must be nonnegative")


def validate_progress(data: object) -> dict[str, ProgressInfo]:
    """Reject damaged progress data before it can affect playback."""
    if not isinstance(data, dict):
        raise TypeError("Progress log must contain an object")

    entries = cast("dict[object, object]", data)
    for video_path, info in entries.items():
        _validate_entry(video_path, info)

    return cast("dict[str, ProgressInfo]", data)


def load_progress() -> dict[str, ProgressInfo]:
    """Return all saved media positions after one-time migration."""
    initialize()
    connection = connect()
    try:
        return {
            path: (
                {"current": current, "total": total}
                if total is not None
                else {"current": current}
            )
            for path, current, total in connection.execute(
                "SELECT media_path, current, total FROM progress"
            )
        }
    finally:
        connection.close()


def get_progress(video_path: Path, default: int = 0) -> int:
    """Return the last displayed frame, or the default for a new video."""
    LOGGER.info("Getting progress for `%s`", video_path)
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
            connection.execute(
                "INSERT INTO progress(media_path, current, total) VALUES (?, ?, ?) "
                "ON CONFLICT(media_path) DO UPDATE SET current=excluded.current, total=excluded.total "
                "WHERE current IS NOT excluded.current OR total IS NOT excluded.total",
                (video_path.as_posix(), current_frame, frame_count),
            )
    finally:
        connection.close()
