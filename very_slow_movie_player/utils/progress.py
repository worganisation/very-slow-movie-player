"""Durable progress storage for local video playback."""

from __future__ import annotations

from json import dumps, loads
from os import O_DIRECTORY, O_RDONLY, close, fsync
from os import open as open_fd
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import time_ns
from typing import NotRequired, TypedDict, cast

from . import const
from .logging import logger


class ProgressInfo(TypedDict):
    """Last displayed frame and optional total frame count."""

    current: int
    total: NotRequired[int]


def _sync_directory(directory: Path) -> None:
    """Persist a renamed directory entry across a power loss."""
    directory_fd = open_fd(directory, O_RDONLY | O_DIRECTORY)
    try:
        fsync(directory_fd)
    finally:
        close(directory_fd)


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


def _validate_progress(data: object) -> dict[str, ProgressInfo]:
    """Reject damaged progress data before it can affect playback."""
    if not isinstance(data, dict):
        raise TypeError("Progress log must contain an object")

    entries = cast("dict[object, object]", data)
    for video_path, info in entries.items():
        _validate_entry(video_path, info)

    return cast("dict[str, ProgressInfo]", data)


def write_progress(
    data: dict[str, ProgressInfo],
    path: Path = const.PROGRESS_LOG,
) -> None:
    """Replace the progress log only after the complete JSON has reached disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            _ = temporary_file.write(dumps(data, indent=2, sort_keys=True))
            temporary_file.flush()
            fsync(temporary_file.fileno())

        _ = Path(temporary_path).replace(path)
        _sync_directory(path.parent)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def load_progress(path: Path = const.PROGRESS_LOG) -> dict[str, ProgressInfo]:
    """Load progress, preserving a damaged log before starting a fresh one."""
    if not path.is_file():
        logger.warning("Progress log not found at `{}`", path)
        write_progress({}, path)
        return {}

    try:
        return _validate_progress(cast("object", loads(path.read_text(encoding="utf-8"))))
    except (TypeError, ValueError) as exc:
        backup = path.with_name(f"{path.name}.corrupt-{time_ns()}")
        _ = path.replace(backup)
        _sync_directory(path.parent)
        logger.warning("Invalid progress log preserved at `{}`: {}", backup, exc)
        write_progress({}, path)
        return {}


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

    log_data = load_progress()
    progress: ProgressInfo = {"current": current_frame}
    if frame_count is not None:
        progress["total"] = frame_count

    logger.debug("Updating log for `{}` to frame #{}", video_path, current_frame)
    log_data[video_path.as_posix()] = progress
    write_progress(log_data)
