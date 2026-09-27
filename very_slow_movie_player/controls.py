"""Validated, persistent playback controls and a coalescing command mailbox."""

from __future__ import annotations

from json import dumps, loads
from os import R_OK, access, fsync
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Lock
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator
from settings import SETTINGS
from utils.const import MEDIA_DIR

CONTROL_FILE = MEDIA_DIR / "ha_controls.json"
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


def load_controls(path: Path = CONTROL_FILE) -> tuple[PlaybackControls, set[str]]:
    """Overlay validated, persisted HA values on complete environment defaults."""
    defaults = PlaybackControls.defaults()
    if not path.exists():
        return defaults, set()
    raw = cast("object", loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        raise TypeError("Invalid persisted HA controls")
    overrides = cast("dict[object, object]", raw)
    if not all(isinstance(key, str) and key in CONTROL_NAMES for key in overrides):
        raise ValueError("Invalid persisted HA controls")
    controls = PlaybackControls.model_validate({
        **defaults.model_dump(),
        **cast("dict[str, object]", overrides),
    })
    return controls, set(cast("dict[str, object]", overrides))


def save_controls(
    controls: PlaybackControls, overridden: set[str], path: Path = CONTROL_FILE
) -> None:
    """Replace the override file atomically after successful validation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".ha_controls-",
            delete=False,
        ) as output:
            temporary = Path(output.name)
            Path(temporary).chmod(0o600)
            values = controls.model_dump(mode="json")
            _ = output.write(
                dumps({key: values[key] for key in overridden}, separators=(",", ":"))
            )
            output.flush()
            fsync(output.fileno())
        _ = temporary.replace(path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
