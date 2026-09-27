"""Report the last successfully displayed frame as a Home Assistant MQTT image."""

from __future__ import annotations

from json import dumps
from ssl import create_default_context
from time import monotonic, sleep
from uuid import uuid4

from paho.mqtt.client import MQTT_ERR_SUCCESS, Client
from paho.mqtt.enums import CallbackAPIVersion
from settings import SETTINGS

PUBLISH_DEADLINE_SECONDS = 8.0
CONNECT_TIMEOUT_SECONDS = 3.0


def time_remaining(deadline: float) -> float:
    """Return the remaining MQTT operation budget, or fail promptly."""
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("MQTT image publish timed out")
    return remaining


def publish_displayed_image(png: bytes) -> None:
    """Retain discovery and a raw PNG only after the panel accepted the frame."""
    device_id = SETTINGS.mqtt_device_id
    object_id = f"vsmp_{device_id}_displayed_frame"
    discovery_topic = f"{SETTINGS.mqtt_discovery_prefix}/image/{object_id}/config"
    image_topic = f"{SETTINGS.mqtt_topic_prefix}/{device_id}/displayed_frame"
    config = dumps(
        {
            "name": "Displayed frame",
            "unique_id": object_id,
            "image_topic": image_topic,
            "content_type": "image/png",
            "device": {
                "identifiers": [f"vsmp_{device_id}"],
                "name": SETTINGS.mqtt_device_name,
                "manufacturer": "VSMP",
                "model": "Very Slow Movie Player",
            },
            "origin": {"name": "Very Slow Movie Player"},
        },
        separators=(",", ":"),
    )
    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=f"vsmp-{device_id}-{uuid4().hex[:8]}",
        reconnect_on_failure=False,
    )
    client.connect_timeout = CONNECT_TIMEOUT_SECONDS
    if SETTINGS.mqtt_username is not None and SETTINGS.mqtt_password is not None:
        client.username_pw_set(
            SETTINGS.mqtt_username,
            SETTINGS.mqtt_password.get_secret_value(),
        )
    if SETTINGS.mqtt_tls:
        client.tls_set_context(create_default_context())  # pyright: ignore[reportUnknownMemberType] - Paho leaves SSLContext untyped

    deadline = monotonic() + PUBLISH_DEADLINE_SECONDS
    loop_started = False
    try:
        if (
            client.connect(SETTINGS.mqtt_host, SETTINGS.mqtt_port, keepalive=15)
            != MQTT_ERR_SUCCESS
        ):
            raise ConnectionError("Could not connect to MQTT broker")
        if client.loop_start() != MQTT_ERR_SUCCESS:
            raise ConnectionError("Could not start MQTT network loop")
        loop_started = True
        while not client.is_connected():
            sleep(min(0.05, time_remaining(deadline)))

        for topic, payload in ((discovery_topic, config), (image_topic, png)):
            info = client.publish(topic, payload, qos=1, retain=True)
            if info.rc != MQTT_ERR_SUCCESS:
                raise ConnectionError("Could not queue MQTT image update")
            info.wait_for_publish(timeout=time_remaining(deadline))
            if not info.is_published():
                raise TimeoutError("MQTT image acknowledgement timed out")
    finally:
        try:
            _ = client.disconnect()
        finally:
            if loop_started:
                _ = client.loop_stop()
