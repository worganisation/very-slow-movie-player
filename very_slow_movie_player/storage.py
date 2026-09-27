"""Versioned SQLite state shared by playback and Home Assistant controls."""

from __future__ import annotations

import sqlite3
from json import dumps, loads
from pathlib import Path  # noqa: TC003 - Path defaults are evaluated at runtime
from typing import cast

from utils.const import MEDIA_DIR, PROGRESS_LOG

DATABASE = MEDIA_DIR / "state.sqlite3"
LEGACY_CONTROLS = MEDIA_DIR / "ha_controls.json"
SCHEMA_VERSION = 1


def connect(path: Path = DATABASE) -> sqlite3.Connection:
    """Return a short-lived connection owned by the calling thread.

    Every caller closes its own connection. Transactions end before MQTT, network,
    or display work; no connection is shared with the MQTT callback thread.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def initialize(
    path: Path = DATABASE,
    *,
    progress_file: Path = PROGRESS_LOG,
    controls_file: Path = LEGACY_CONTROLS,
) -> None:
    """Create schema and import legacy JSON once in a single transaction.

    A malformed legacy file aborts and rolls back the entire import. Existing
    database rows are never overwritten by old JSON on subsequent starts.
    """
    from controls import CONTROL_NAMES, PlaybackControls  # noqa: PLC0415
    from utils import progress as progress_module  # noqa: PLC0415

    connection = connect(path)
    try:
        with connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, SCHEMA_VERSION}:
                raise RuntimeError(f"Unsupported VSMP database schema version: {version}")
            if version == SCHEMA_VERSION:
                return
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            if version != 0:
                raise RuntimeError(f"Unsupported VSMP database schema version: {version}")
            connection.execute(
                "CREATE TABLE progress (media_path TEXT PRIMARY KEY, "
                "current INTEGER NOT NULL CHECK(current >= 0), "
                "total INTEGER CHECK(total >= 0))"
            )
            connection.execute(
                "CREATE TABLE control_overrides (name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            if progress_file.exists():
                progress = progress_module.validate_progress(
                    cast("object", loads(progress_file.read_text(encoding="utf-8")))
                )
                connection.executemany(
                    "INSERT INTO progress(media_path, current, total) VALUES (?, ?, ?)",
                    (
                        (media, entry["current"], entry.get("total"))
                        for media, entry in progress.items()
                    ),
                )
            if controls_file.exists():
                raw = cast("object", loads(controls_file.read_text(encoding="utf-8")))
                if not isinstance(raw, dict) or not all(
                    isinstance(key, str) and key in CONTROL_NAMES for key in raw
                ):
                    raise ValueError("Invalid legacy HA controls")
                overrides = cast("dict[str, object]", raw)
                validated = PlaybackControls.model_validate({
                    **PlaybackControls.defaults().model_dump(),
                    **overrides,
                })
                values = validated.model_dump(mode="json")
                connection.executemany(
                    "INSERT INTO control_overrides(name, value) VALUES (?, ?)",
                    ((key, dumps(values[key])) for key in overrides),
                )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        connection.close()
