"""Persistent MQTT discovery, confirmed state, and displayed image."""

from __future__ import annotations

from json import dumps
from ssl import CERT_REQUIRED
from threading import Lock
from typing import TYPE_CHECKING

from paho.mqtt.client import MQTT_ERR_SUCCESS, Client
from paho.mqtt.enums import CallbackAPIVersion
from settings import SETTINGS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from controls import CommandMailbox
    from paho.mqtt.client import ConnectFlags, MQTTMessage
    from paho.mqtt.properties import Properties
    from paho.mqtt.reasoncodes import ReasonCode


ENTITY_ICONS = {
    "source": "mdi:video-input-component",
    "media_type": "mdi:file-image",
    "album": "mdi:image-album",
    "library_id": "mdi:movie-open",
    "caption_style": "mdi:format-text-wrapping-wrap",
    "caption_font": "mdi:format-font",
    "import_youtube": "mdi:youtube",
    "import_jellyfin": "mdi:movie-open-plus",
    "video_interval": "mdi:timer-outline",
    "photo_interval": "mdi:timer-outline",
    "frame_advance": "mdi:fast-forward",
    "gamma": "mdi:brightness-6",
    "caption_font_size": "mdi:format-size",
    "caption_offset": "mdi:timer-sync-outline",
    "captions_enabled": "mdi:closed-caption",
    "playback_enabled": "mdi:play-pause",
    "always_restart_videos": "mdi:restart",
    "video_path": "mdi:file-video",
    "next": "mdi:skip-next",
    "redisplay": "mdi:refresh",
    "restart_video": "mdi:restart",
    "current_caption": "mdi:closed-caption-outline",
    "video_timestamp": "mdi:timer-outline",
    "caption_error": "mdi:comment-alert-outline",
    "import_status": "mdi:download-circle-outline",
    "import_error": "mdi:alert-circle-outline",
    "import_progress": "mdi:progress-download",
    "playback_status": "mdi:play-circle-outline",
    "current_media": "mdi:movie-open",
    "video_current_frame": "mdi:filmstrip",
    "video_frame_count": "mdi:filmstrip-box-multiple",
    "last_refresh": "mdi:check-circle-outline",
    "next_refresh": "mdi:clock-outline",
    "last_error": "mdi:alert-circle-outline",
    "displayed_frame": "mdi:image-frame",
}


class HAClient:
    """Keep MQTT connected throughout waits and reconnect after outages."""

    def __init__(self, mailbox: CommandMailbox) -> None:
        self.mailbox: CommandMailbox = mailbox
        self.root: str = f"{SETTINGS.mqtt_topic_prefix}/{SETTINGS.mqtt_device_id}"
        self.discovery: str = SETTINGS.mqtt_discovery_prefix
        self.device_id: str = SETTINGS.mqtt_device_id
        self.client: Client = Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=f"vsmp-{self.device_id}",
            reconnect_on_failure=True,
        )
        self.client.connect_timeout = 3.0
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.will_set(f"{self.root}/availability", "offline", qos=1, retain=True)
        if SETTINGS.mqtt_username is not None and SETTINGS.mqtt_password is not None:
            self.client.username_pw_set(
                SETTINGS.mqtt_username, SETTINGS.mqtt_password.get_secret_value()
            )
        if SETTINGS.mqtt_tls:
            self.client.tls_set(cert_reqs=CERT_REQUIRED)  # pyright: ignore[reportUnknownMemberType]
        self._lock: Lock = Lock()
        self._stopping: bool = False
        self._states: dict[str, str] = {"import_youtube": "", "import_jellyfin": ""}
        self._albums: dict[str, str] = {
            str(SETTINGS.immich_album_id): str(SETTINGS.immich_album_id)
        }
        self._library: dict[str, str] = {"none": "No library selection"}
        self._image: bytes | None = None

    def start(self) -> None:
        """Start the Paho network thread without waiting on the broker."""
        _ = self.client.connect_async(
            SETTINGS.mqtt_host, SETTINGS.mqtt_port, keepalive=30
        )
        if self.client.loop_start() != MQTT_ERR_SUCCESS:
            raise ConnectionError("Could not start MQTT network loop")

    def stop(self) -> None:
        """Mark controls unavailable on graceful shutdown."""
        acknowledged = False
        with self._lock:
            self._stopping = True
            info = (
                self.client.publish(
                    f"{self.root}/availability", "offline", qos=1, retain=True
                )
                if self.client.is_connected()
                else None
            )
        if info is not None:
            try:
                info.wait_for_publish(timeout=3)
                acknowledged = info.is_published()
            except (RuntimeError, ValueError):
                pass
        if acknowledged:
            _ = self.client.disconnect()
        # If the offline update was not acknowledged, closing the socket on
        # process exit lets the broker publish the offline last will instead.
        _ = self.client.loop_stop()

    def _publish(self, suffix: str, payload: str | bytes, *, retain: bool) -> None:
        if self.client.is_connected():
            _ = self.client.publish(
                f"{self.root}/{suffix}", payload, qos=1, retain=retain
            )

    def state(self, name: str, value: str) -> None:
        """Cache confirmed state for reconnect and Home Assistant birth."""
        with self._lock:
            self._states[name] = value
            self._publish(f"state/{name}", value, retain=True)

    def image(self, png: bytes) -> None:
        """Retain the image only after a successful panel operation."""
        with self._lock:
            self._image = png
            self._publish("displayed_frame", png, retain=True)

    def albums(self, labels: Mapping[str, str]) -> None:
        """Replace friendly names while keeping immutable album IDs internally."""
        with self._lock:
            self._albums = dict(labels)
        self._discovery()

    def album_label(self, album_id: str) -> str:
        """Find the current friendly label for a stable ID."""
        with self._lock:
            return self._albums.get(album_id, album_id)

    def library(self, labels: Mapping[str, str]) -> None:
        """Publish ready offline media using stable IDs and readable labels."""
        with self._lock:
            updated = {"none": "No library selection", **labels}
            if updated == self._library:
                return
            self._library = updated
        self._discovery()

    def library_label(self, media_id: str) -> str:
        """Return a friendly label without changing the persisted identity."""
        with self._lock:
            return self._library.get(media_id, media_id)

    def _entity(self, domain: str, name: str, label: str, **extra: object) -> None:
        object_id = f"vsmp_{self.device_id}_{name}"
        config = {
            "name": label,
            "icon": ENTITY_ICONS[name],
            "unique_id": object_id,
            "device": {
                "identifiers": [f"vsmp_{self.device_id}"],
                "name": SETTINGS.mqtt_device_name,
                "manufacturer": "VSMP",
                "model": "Very Slow Movie Player",
            },
            "origin": {"name": "Very Slow Movie Player"},
            **extra,
        }
        if domain != "image":
            config["availability_topic"] = f"{self.root}/availability"
        if self.client.is_connected():
            _ = self.client.publish(
                f"{self.discovery}/{domain}/{object_id}/config",
                dumps(config, separators=(",", ":")),
                qos=1,
                retain=True,
            )

    def _discovery(self) -> None:
        common = {"optimistic": False, "retain": False}
        for name, label, options in (
            ("source", "Source", ["local", "immich", "library"]),
            ("media_type", "Immich media type", ["photos", "videos", "both"]),
            ("caption_style", "Caption style", ["margin", "overlay"]),
            ("caption_font", "Caption font", ["serif", "sans"]),
        ):
            self._entity(
                "select",
                name,
                label,
                options=options,
                command_topic=f"{self.root}/command/{name}",
                state_topic=f"{self.root}/state/{name}",
                **common,
            )
        with self._lock:
            options = list(self._albums.values())
        self._entity(
            "select",
            "album",
            "Immich album",
            options=options,
            command_topic=f"{self.root}/command/album",
            state_topic=f"{self.root}/state/album",
            **common,
        )
        with self._lock:
            library_options = list(self._library.values())
        self._entity(
            "select",
            "library_id",
            "Library video",
            options=library_options,
            command_topic=f"{self.root}/command/library_id",
            state_topic=f"{self.root}/state/library_id",
            **common,
        )
        for name, label in (
            ("import_youtube", "Import YouTube URL"),
            ("import_jellyfin", "Import Jellyfin item ID"),
        ):
            self._entity(
                "text",
                name,
                label,
                mode="text",
                min=0,
                max=255,
                command_topic=f"{self.root}/command/{name}",
                state_topic=f"{self.root}/state/{name}",
                **common,
            )
        for name, label, low, high, step in (
            ("video_interval", "Video refresh interval", 180, 86400, 1),
            ("photo_interval", "Photo refresh interval", 180, 86400, 1),
            ("frame_advance", "Video frame advance", 1, 100000, 1),
            ("gamma", "Image gamma", 0.1, 10, 0.1),
            ("caption_font_size", "Caption font size", 16, 40, 1),
            ("caption_offset", "Caption timing offset", -60, 60, 0.1),
        ):
            self._entity(
                "number",
                name,
                label,
                min=low,
                max=high,
                step=step,
                command_topic=f"{self.root}/command/{name}",
                state_topic=f"{self.root}/state/{name}",
                **common,
            )
        for name, label in (
            ("captions_enabled", "Captions enabled"),
            ("playback_enabled", "Playback enabled"),
            ("always_restart_videos", "Always restart videos"),
        ):
            self._entity(
                "switch",
                name,
                label,
                command_topic=f"{self.root}/command/{name}",
                state_topic=f"{self.root}/state/{name}",
                **common,
            )
        self._entity(
            "text",
            "video_path",
            "Local video",
            mode="text",
            min=1,
            max=255,
            command_topic=f"{self.root}/command/video_path",
            state_topic=f"{self.root}/state/video_path",
            **common,
        )
        for name, label in (
            ("next", "Next"),
            ("redisplay", "Redisplay current frame"),
            ("restart_video", "Restart current video"),
        ):
            self._entity(
                "button",
                name,
                label,
                command_topic=f"{self.root}/command/{name}",
                payload_press="PRESS",
                retain=False,
            )
        for name, label in (
            ("current_caption", "Current caption"),
            ("video_timestamp", "Video timestamp"),
            ("caption_error", "Caption error"),
            ("import_status", "Media import status"),
            ("import_error", "Media import error"),
            ("import_progress", "Media import progress"),
            ("playback_status", "Playback status"),
            ("current_media", "Current media"),
            ("video_current_frame", "Video current frame"),
            ("video_frame_count", "Video frame count"),
            ("last_refresh", "Last successful refresh"),
            ("next_refresh", "Next scheduled refresh"),
            ("last_error", "Last error"),
        ):
            extra = self._sensor_attributes(name)
            self._entity(
                "sensor", name, label, state_topic=f"{self.root}/state/{name}", **extra
            )
        # Remove the retained discovery entry for the superseded combined sensor.
        if self.client.is_connected():
            _ = self.client.publish(
                f"{self.discovery}/sensor/vsmp_{self.device_id}_video_position/config",
                b"",
                qos=1,
                retain=True,
            )
        self._entity(
            "image",
            "displayed_frame",
            "Displayed frame",
            image_topic=f"{self.root}/displayed_frame",
            content_type="image/png",
        )

    @staticmethod
    def _sensor_attributes(name: str) -> dict[str, object]:
        if name == "import_progress":
            return {"unit_of_measurement": "%", "state_class": "measurement"}
        if name in {"video_current_frame", "video_frame_count"}:
            return {"state_class": "measurement"}
        if name in {"last_refresh", "next_refresh"}:
            return {"device_class": "timestamp"}
        return {}

    def _on_connect(
        self,
        _client: Client,
        _userdata: object,
        _flags: ConnectFlags,
        reason_code: ReasonCode,
        _properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            return
        # Commands are live actions: QoS 0 avoids broker redelivery on reconnect.
        _ = self.client.subscribe(f"{self.root}/command/+", qos=0)
        _ = self.client.subscribe(f"{self.discovery}/status", qos=1)
        self._announce()

    def _announce(self) -> None:
        with self._lock:
            if self._stopping:
                return
        self._discovery()
        with self._lock:
            if self._stopping:
                return
            for name, value in self._states.items():
                self._publish(f"state/{name}", value, retain=True)
            if self._image is not None:
                self._publish("displayed_frame", self._image, retain=True)
            self._publish("availability", "online", retain=True)

    def _on_message(
        self, _client: Client, _userdata: object, message: MQTTMessage
    ) -> None:
        if message.topic == f"{self.discovery}/status":
            if message.payload == b"online":
                self._announce()
            return
        prefix = f"{self.root}/command/"
        if not message.topic.startswith(prefix):
            return
        name = message.topic[len(prefix) :]
        try:
            payload = message.payload.decode("utf-8")
        except UnicodeDecodeError:
            payload = "<invalid UTF-8>"
        if name == "album":
            with self._lock:
                payload = next(
                    (key for key, label in self._albums.items() if label == payload),
                    payload,
                )
        if name == "library_id":
            with self._lock:
                payload = next(
                    (key for key, label in self._library.items() if label == payload),
                    payload,
                )
        self.mailbox.submit(name, payload, retained=message.retain)
