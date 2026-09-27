"""Validated, persistent playback controls and a coalescing command mailbox."""

from __future__ import annotations

from json import dumps, loads
from threading import Event, Lock
from typing import cast
from uuid import UUID

from control_model import BUTTON_NAMES, CONTROL_NAMES, PlaybackControls
from pydantic import ValidationError
from storage import connect, initialize

__all__ = [
    "BUTTON_NAMES",
    "CONTROL_NAMES",
    "CommandMailbox",
    "PlaybackControls",
    "apply_command",
    "load_controls",
    "save_controls",
]


def load_controls() -> tuple[PlaybackControls, set[str]]:
    """Overlay explicit HA overrides on eagerly validated environment defaults."""
    defaults = PlaybackControls.defaults()
    initialize()
    connection = connect()
    try:
        rows = cast(
            "list[tuple[str, str]]",
            connection.execute("SELECT name, value FROM control_overrides").fetchall(),
        )
        overrides = {name: loads(value) for name, value in rows}
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
            rows = cast(
                "list[tuple[str]]",
                connection.execute("SELECT name FROM control_overrides").fetchall(),
            )
            existing = {row[0] for row in rows}
            _ = connection.executemany(
                "DELETE FROM control_overrides WHERE name = ?",
                ((name,) for name in existing - overridden),
            )
            _ = connection.executemany(
                """INSERT INTO control_overrides(name, value) VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET value=excluded.value
                WHERE value IS NOT excluded.value""",
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
