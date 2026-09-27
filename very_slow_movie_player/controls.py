"""Validated, persistent playback controls and a coalescing command mailbox."""

from __future__ import annotations

from json import dumps, loads
from os import R_OK, access
from pathlib import Path  # noqa: TC003 - Pydantic resolves this annotation at runtime
from threading import Event, Lock
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator
from settings import SETTINGS
from storage import connect, initialize

CONTROL_NAMES = frozenset({
    "source",
    "playback_enabled",
    "video_interval",
    "photo_interval",
    "frame_advance",
    "gamma",
    "video_path",
    "album",
    "media_type",
    "always_restart_videos",
})
BUTTON_NAMES = frozenset({"next", "redisplay", "restart_video"})


class PlaybackControls(BaseModel):
    """Only non-secret settings exposed to Home Assistant."""

    source: Literal["local", "immich"]
    playback_enabled: bool
    video_interval: Annotated[float, Field(ge=180, le=86400, allow_inf_nan=False)]
    photo_interval: Annotated[float, Field(ge=180, le=86400, allow_inf_nan=False)]
    frame_advance: Annotated[int, Field(ge=1, le=100000)]
    gamma: Annotated[float, Field(ge=0.1, le=10, allow_inf_nan=False)]
    video_path: Path
    album: UUID
    media_type: Literal["photos", "videos", "both"]
    always_restart_videos: bool

    @field_validator("video_path")
    @classmethod
    def readable_video(cls, value: Path) -> Path:
        """Reject missing, relative, or unreadable paths for either source."""
        _ = cls
        path = value.expanduser()
        if not path.is_absolute() or not path.is_file() or not access(path, R_OK):
            raise ValueError("video_path must be an existing readable absolute file")
        return path

    @classmethod
    def defaults(cls) -> PlaybackControls:
        """Construct complete defaults from eagerly validated environment settings."""
        return cls(
            source=SETTINGS.vsmp_source,
            playback_enabled=SETTINGS.vsmp_playback_enabled,
            video_interval=SETTINGS.vsmp_video_frame_delay_seconds,
            photo_interval=SETTINGS.vsmp_photo_frame_delay_seconds,
            frame_advance=SETTINGS.vsmp_video_frame_advance,
            gamma=SETTINGS.vsmp_image_gamma,
            video_path=SETTINGS.vsmp_video_path,
            album=SETTINGS.immich_album_id,
            media_type=SETTINGS.immich_media_type,
            always_restart_videos=SETTINGS.always_restart_videos,
        )


def load_controls() -> tuple[PlaybackControls, set[str]]:
    """Overlay explicit HA overrides on eagerly validated environment defaults."""
    defaults = PlaybackControls.defaults()
    initialize()
    connection = connect()
    try:
        overrides = {
            name: loads(value)
            for name, value in connection.execute(
                "SELECT name, value FROM control_overrides"
            )
        }
    finally:
        connection.close()
    if not all(name in CONTROL_NAMES for name in overrides):
        raise ValueError("Invalid persisted HA controls")
    return PlaybackControls.model_validate({**defaults.model_dump(), **overrides}), set(
        overrides
    )


def save_controls(controls: PlaybackControls, overridden: set[str]) -> None:
    """Save only explicit overrides in one short transaction."""
    if not overridden <= CONTROL_NAMES:
        raise ValueError("Invalid HA control override names")
    initialize()
    values = controls.model_dump(mode="json")
    connection = connect()
    try:
        with connection:
            existing = {
                row[0] for row in connection.execute("SELECT name FROM control_overrides")
            }
            connection.executemany(
                "DELETE FROM control_overrides WHERE name = ?",
                ((name,) for name in existing - overridden),
            )
            connection.executemany(
                "INSERT INTO control_overrides(name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value "
                "WHERE value IS NOT excluded.value",
                ((key, dumps(values[key])) for key in overridden),
            )
    finally:
        connection.close()


class CommandMailbox:
    """Keep the latest value per setting and one pending press per button."""

    def __init__(self) -> None:
        self.wake: Event = Event()
        self._lock: Lock = Lock()
        self._settings: dict[str, str] = {}
        self._buttons: set[str] = set()

    def submit(self, name: str, payload: str, *, retained: bool) -> None:
        """Ignore broker-replayed commands and wake playback for fresh input."""
        if retained or name not in CONTROL_NAMES | BUTTON_NAMES:
            return
        with self._lock:
            if name in BUTTON_NAMES:
                if payload != "PRESS":
                    return
                self._buttons.add(name)
            else:
                self._settings[name] = payload
            self.wake.set()

    def drain(self) -> tuple[dict[str, str], set[str]]:
        """Take a consistent batch without holding the lock during slow work."""
        with self._lock:
            settings, buttons = self._settings, self._buttons
            self._settings, self._buttons = {}, set()
            self.wake.clear()
        return settings, buttons


def apply_command(
    controls: PlaybackControls, name: str, payload: str
) -> PlaybackControls:
    """Validate a single command without mutating the last good configuration."""
    if name not in CONTROL_NAMES:
        raise ValueError("Unknown control")
    if name in {"playback_enabled", "always_restart_videos"}:
        if payload not in {"ON", "OFF"}:
            raise ValueError(f"{name} expects ON or OFF")
        value: object = payload == "ON"
    elif name == "album":
        value = UUID(payload)
    else:
        value = payload
    try:
        return PlaybackControls.model_validate({**controls.model_dump(), name: value})
    except ValidationError as exc:
        raise ValueError(f"Invalid {name}: {exc.errors()[0]['msg']}") from exc
