"""Validate all runtime configuration before media or display startup."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, ClassVar, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime values shared by playback, the display, and playlist downloads."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    vsmp_source: Literal["local", "immich"] = "local"
    vsmp_video_path: Path
    immich_url: AnyHttpUrl
    immich_api_key: SecretStr
    immich_album_id: UUID
    yt_playlist_id: Annotated[str, Field(min_length=1)]
    vsmp_image_gamma: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 1.7
    always_restart_videos: bool = False
    vsmp_allow_mock_hardware: bool = False

    @field_validator("vsmp_source", mode="before")
    @classmethod
    def normalize_source(_cls, value: object) -> object:
        """Retain the previous case-insensitive source selector."""
        return value.casefold() if isinstance(value, str) else value

    @field_validator("vsmp_video_path")
    @classmethod
    def validate_video_path(_cls, value: Path) -> Path:
        """Require an existing, absolute local media path in all modes."""
        path = value.expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError("VSMP_VIDEO_PATH must name an existing absolute file")
        return path

    @field_validator("immich_url")
    @classmethod
    def validate_immich_url(_cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Reject URL components that would redirect API requests or expose credentials."""
        parsed = urlsplit(str(value))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("IMMICH_URL must not include credentials, query, or fragment")
        return value

    @field_validator("immich_api_key")
    @classmethod
    def validate_immich_api_key(_cls, value: SecretStr) -> SecretStr:
        """Require a nonblank key without including it in error messages."""
        if not value.get_secret_value().strip():
            raise ValueError("IMMICH_API_KEY must not be blank")
        return value

    @field_validator("yt_playlist_id")
    @classmethod
    def validate_playlist_id(_cls, value: str) -> str:
        """Reject empty or whitespace-only playlist IDs."""
        if not value.strip():
            raise ValueError("YT_PLAYLIST_ID must not be blank")
        return value.strip()


SETTINGS = Settings()  # pyright: ignore[reportCallIssue] - values come from the environment
