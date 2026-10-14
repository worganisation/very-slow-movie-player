"""Validated, non-secret Home Assistant playback settings."""

from __future__ import annotations

from os import R_OK, access
from pathlib import Path  # noqa: TC003 - Pydantic resolves this annotation at runtime
from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime

from dithering import DitheringMethod
from pydantic import BaseModel, Field, field_validator
from settings import SETTINGS

CONTROL_NAMES = frozenset({
    "library_id",
    "captions_enabled",
    "caption_style",
    "caption_background",
    "caption_font",
    "caption_font_size",
    "caption_offset",
    "source",
    "playback_enabled",
    "video_interval",
    "photo_interval",
    "frame_advance",
    "gamma",
    "dithering_method",
    "video_path",
    "album",
    "media_type",
    "always_restart_videos",
})
BUTTON_NAMES = frozenset({"next", "redisplay", "restart_video"})


class PlaybackControls(BaseModel):
    """Only non-secret settings exposed to Home Assistant."""

    source: Literal["local", "immich", "library"]
    playback_enabled: bool
    video_interval: Annotated[float, Field(ge=180, le=86400, allow_inf_nan=False)]
    photo_interval: Annotated[float, Field(ge=180, le=86400, allow_inf_nan=False)]
    frame_advance: Annotated[int, Field(ge=1, le=100000)]
    gamma: Annotated[float, Field(ge=0.1, le=10, allow_inf_nan=False)]
    dithering_method: DitheringMethod = DitheringMethod.FLOYD_STEINBERG
    video_path: Path
    album: UUID
    media_type: Literal["photos", "videos", "both"]
    library_id: str = "none"
    captions_enabled: bool = True
    caption_style: Literal["margin", "overlay"] = "margin"
    caption_background: Literal["light", "dark"] = "light"
    caption_font: Literal["serif", "sans"] = "serif"
    caption_font_size: Annotated[int, Field(ge=16, le=40)] = 26
    caption_offset: Annotated[float, Field(ge=-60, le=60, allow_inf_nan=False)] = 0
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
            captions_enabled=SETTINGS.vsmp_captions_enabled,
            caption_style=SETTINGS.vsmp_caption_style,
            caption_background=SETTINGS.vsmp_caption_background,
            caption_font=SETTINGS.vsmp_caption_font,
            caption_font_size=SETTINGS.vsmp_caption_font_size,
            caption_offset=SETTINGS.vsmp_caption_offset,
            playback_enabled=SETTINGS.vsmp_playback_enabled,
            video_interval=SETTINGS.vsmp_video_frame_delay_seconds,
            photo_interval=SETTINGS.vsmp_photo_frame_delay_seconds,
            frame_advance=SETTINGS.vsmp_video_frame_advance,
            gamma=SETTINGS.vsmp_image_gamma,
            dithering_method=SETTINGS.vsmp_dithering_method,
            video_path=SETTINGS.vsmp_video_path,
            album=SETTINGS.immich_album_id,
            media_type=SETTINGS.immich_media_type,
            always_restart_videos=SETTINGS.always_restart_videos,
        )
