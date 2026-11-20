"""Build MQTT connections for the player and recorder."""

from __future__ import annotations

from ssl import CERT_REQUIRED
from typing import TYPE_CHECKING

from paho.mqtt.client import Client
from paho.mqtt.enums import CallbackAPIVersion

if TYPE_CHECKING:
    from settings import Settings


def mqtt_client(settings: Settings, client_id: str) -> Client:
    """Apply the shared connection timeout, authentication and TLS settings."""
    client = Client(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id=client_id,
        reconnect_on_failure=True,
    )
    client.connect_timeout = 3.0
    if settings.mqtt_username is not None and settings.mqtt_password is not None:
        client.username_pw_set(
            settings.mqtt_username, settings.mqtt_password.get_secret_value()
        )
    if settings.mqtt_tls:
        client.tls_set(cert_reqs=CERT_REQUIRED)  # pyright: ignore[reportUnknownMemberType]

    return client
