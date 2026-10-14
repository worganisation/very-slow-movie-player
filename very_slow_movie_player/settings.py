"""Validate all runtime configuration before media or display startup."""

from __future__ import annotations

from ipaddress import ip_address
from pathlib import Path
from re import ASCII, fullmatch
from typing import Annotated, ClassVar, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID  # noqa: TC003 - Pydantic needs this when building its schema

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_DNS_NAME_LENGTH = 253


class Settings(BaseSettings):
    """Runtime values shared by playback, the display, and playlist downloads."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    vsmp_source: Literal["local", "immich", "library"] = "local"
    vsmp_video_path: Path
    immich_url: AnyHttpUrl
    immich_api_key: SecretStr
    immich_album_id: UUID
    yt_playlist_id: Annotated[str, Field(min_length=1)]
    mqtt_host: Annotated[str, Field(min_length=1)]
    mqtt_port: Annotated[int, Field(ge=1, le=65535)] = 1883
    mqtt_tls: bool = False
    mqtt_username: str | None = None
    mqtt_password: SecretStr | None = None
    mqtt_topic_prefix: str = "vsmp"
    mqtt_discovery_prefix: str = "homeassistant"
    mqtt_device_id: str = "vsmp_pi"
    mqtt_device_name: str = "Very Slow Movie Player"
    vsmp_image_gamma: Annotated[float, Field(ge=0.1, le=10, allow_inf_nan=False)] = 1.7
    vsmp_video_frame_delay_seconds: Annotated[
        float, Field(ge=180, le=86400, allow_inf_nan=False)
    ] = 180.0
    vsmp_photo_frame_delay_seconds: Annotated[
        float, Field(ge=180, le=86400, allow_inf_nan=False)
    ] = 300.0
    vsmp_video_frame_advance: Annotated[int, Field(ge=1, le=100000)] = 12
    immich_media_type: Literal["photos", "videos", "both"] = "both"
    vsmp_playback_enabled: bool = True
    always_restart_videos: bool = False
    vsmp_allow_mock_hardware: bool = False
    vsmp_library_path: Path = Path.home() / "vsmp-library"
    jellyfin_url: AnyHttpUrl | None = None
    jellyfin_api_key: SecretStr | None = None
    jellyfin_user_id: str | None = None
    vsmp_caption_language: Annotated[
        str, Field(pattern=r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]+)*$")
    ] = "en"
    vsmp_asr_backend: Annotated[
        str, Field(pattern=r"^(disabled|local(?::[a-zA-Z0-9][a-zA-Z0-9._/-]*)?)$")
    ] = "disabled"
    vsmp_captions_enabled: bool = True
    vsmp_caption_style: Literal["margin", "overlay"] = "margin"
    vsmp_caption_background: Literal["light", "dark"] = "light"
    vsmp_caption_font: Literal["serif", "sans"] = "serif"
    vsmp_caption_font_size: Annotated[int, Field(ge=16, le=40)] = 26
    vsmp_caption_offset: Annotated[float, Field(ge=-60, le=60, allow_inf_nan=False)] = 0

    @model_validator(mode="after")
    def validate_import_settings(self) -> Self:
        """Validate import configuration even when another source is selected."""
        values = (self.jellyfin_url, self.jellyfin_api_key, self.jellyfin_user_id)
        if any(value is not None for value in values) and not all(
            value is not None for value in values
        ):
            raise ValueError(
                "Jellyfin URL, API key and user ID must be configured together"
            )
        if self.jellyfin_url is not None:
            _ = self.validate_immich_url(self.jellyfin_url)
        if self.jellyfin_api_key is not None:
            _ = self.validate_immich_api_key(self.jellyfin_api_key)
        if self.jellyfin_user_id is not None and not self.jellyfin_user_id.strip():
            raise ValueError("JELLYFIN_USER_ID must not be blank")
        if not self.vsmp_library_path.is_absolute():
            raise ValueError("VSMP_LIBRARY_PATH must be absolute")
        return self

    @field_validator("vsmp_source", mode="before")
    @classmethod
    def normalize_source(cls, value: object) -> object:
        """Retain the previous case-insensitive source selector."""
        _ = cls
        return value.casefold() if isinstance(value, str) else value

    @field_validator("vsmp_video_path")
    @classmethod
    def validate_video_path(cls, value: Path) -> Path:
        """Require an existing, absolute local media path in all modes."""
        _ = cls
        path = value.expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError("VSMP_VIDEO_PATH must name an existing absolute file")
        return path

    @field_validator("immich_url")
    @classmethod
    def validate_immich_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        """Reject URL components that would redirect API requests or expose credentials."""
        _ = cls
        parsed = urlsplit(str(value))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "IMMICH_URL must not include credentials, query, or fragment"
            )
        return value

    @field_validator("immich_api_key")
    @classmethod
    def validate_immich_api_key(cls, value: SecretStr) -> SecretStr:
        """Require a nonblank key without including it in error messages."""
        _ = cls
        if not value.get_secret_value().strip():
            raise ValueError("IMMICH_API_KEY must not be blank")
        return value

    @field_validator("yt_playlist_id")
    @classmethod
    def validate_playlist_id(cls, value: str) -> str:
        """Reject empty or whitespace-only playlist IDs."""
        _ = cls
        if not value.strip():
            raise ValueError("YT_PLAYLIST_ID must not be blank")
        return value.strip()

    @field_validator("mqtt_host")
    @classmethod
    def validate_mqtt_host(cls, value: str) -> str:
        """Accept a bare broker hostname or IP address, without URL syntax."""
        _ = cls
        host = value.strip()
        try:
            _ = ip_address(host)
        except ValueError:
            labels = host.split(".")
            valid_name = len(host) <= MAX_DNS_NAME_LENGTH and all(
                fullmatch(r"\w(?:[\w-]{0,61}\w)?", label, flags=ASCII) is not None
                for label in labels
            )
            if not valid_name:
                raise ValueError(
                    "MQTT_HOST must be a bare hostname or IP address"
                ) from None
        return host

    @field_validator("mqtt_topic_prefix", "mqtt_discovery_prefix")
    @classmethod
    def validate_mqtt_topic_prefix(cls, value: str) -> str:
        """Reject empty levels and MQTT wildcards in configured topic roots."""
        _ = cls
        if fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", value) is None:
            raise ValueError("MQTT topic prefixes need nonempty plain-text levels")
        return value

    @field_validator("mqtt_device_id")
    @classmethod
    def validate_mqtt_device_id(cls, value: str) -> str:
        """Keep the discovery object ID stable and valid."""
        _ = cls
        if fullmatch(r"[A-Za-z0-9_-]+", value) is None:
            raise ValueError("MQTT_DEVICE_ID must use letters, digits, _ or -")
        return value

    @field_validator("mqtt_device_name")
    @classmethod
    def validate_mqtt_device_name(cls, value: str) -> str:
        """Use a readable device name in Home Assistant."""
        _ = cls
        if not value.strip():
            raise ValueError("MQTT_DEVICE_NAME must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def validate_mqtt_credentials(self) -> Self:
        """Require broker username and password together when authenticating."""
        if self.mqtt_username is not None and not self.mqtt_username.strip():
            raise ValueError("MQTT_USERNAME must not be blank")
        if (
            self.mqtt_password is not None
            and not self.mqtt_password.get_secret_value().strip()
        ):
            raise ValueError("MQTT_PASSWORD must not be blank")
        if (self.mqtt_username is None) != (self.mqtt_password is None):
            raise ValueError("MQTT_USERNAME and MQTT_PASSWORD must be set together")
        return self


SETTINGS = Settings()  # pyright: ignore[reportCallIssue] - values come from the environment
