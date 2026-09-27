"""Coordinate background imports without blocking the panel owner or MQTT thread."""

from __future__ import annotations

import os
import signal
import subprocess  # noqa: S404 - isolated importer process is cancellable
import sys
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from captions import CaptionTrack, caption_at
from library import MediaLibrary
from pydantic import TypeAdapter
from settings import SETTINGS
from storage import DATABASE

if TYPE_CHECKING:
    from library import LibraryItem
    from mqtt_controls import HAClient


MAX_IMPORT_REQUEST_LENGTH = 2048
JELLYFIN_REQUEST_FIELDS = 3


class LibraryRuntime:
    """Keep a bounded import worker and reload offline captions only when needed."""

    def __init__(self, mqtt: HAClient) -> None:
        self.mqtt: HAClient = mqtt
        self.library: MediaLibrary = MediaLibrary(DATABASE, SETTINGS.vsmp_library_path)
        self.library.initialize()
        self.pending: subprocess.Popen[bytes] | None = None
        self.caption_cache: dict[Path, CaptionTrack] = {}
        self.mqtt.state("caption_error", "none")
        self.mqtt.state("import_progress", "0")
        self.mqtt.state("import_status", "idle")
        self.mqtt.state("import_error", "none")

    def submit(self, name: str, payload: str) -> None:
        """Accept one import at a time; requests are never persisted as controls."""
        if self.pending is not None:
            self.mqtt.state("import_error", "An import is already running")
            return
        if not payload.strip() or len(payload) > MAX_IMPORT_REQUEST_LENGTH:
            self.mqtt.state("import_error", "Invalid import request")
            return
        command = [sys.executable, str(Path(__file__).with_name("media_import.py"))]
        if name == "import_youtube":
            command.extend(["youtube", payload.strip()])
        else:
            fields = payload.strip().split("/")
            if len(fields) > JELLYFIN_REQUEST_FIELDS or not all(fields):
                self.mqtt.state(
                    "import_error", "Use item_id[/media_source_id[/audio_language]]"
                )
                return
            command.extend(["jellyfin", fields[0]])
            if len(fields) > 1:
                command.extend(["--media-source-id", fields[1]])
            if len(fields) == JELLYFIN_REQUEST_FIELDS:
                command.extend(["--audio-language", fields[2]])
        try:
            self.pending = subprocess.Popen(  # noqa: S603 - fixed module, separate arguments
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            self.mqtt.state("import_error", "Could not start import worker")
            return
        self.mqtt.state("import_progress", "0")
        self.mqtt.state("import_status", "importing")
        self.mqtt.state("import_error", "none")
        self.mqtt.state(name, "")

    def refresh(self) -> None:
        """Publish ready titles and completed outcomes on the playback thread."""
        if self.pending is not None and self.pending.poll() is not None:
            try:
                output, _ = self.pending.communicate(timeout=1)
                record = TypeAdapter(dict[str, object]).validate_json(output)
                result = self.ready_item(str(record.get("id", "")))
            except Exception:  # noqa: BLE001 - preserve playback after an import failure
                self.mqtt.state("import_progress", "0")
                self.mqtt.state("import_status", "failed")
                self.mqtt.state(
                    "import_error", "Import failed; inspect library status for details"
                )
            else:
                self.mqtt.state("import_progress", "100")
                self.mqtt.state("import_status", f"ready: {result.title}"[:255])
                self.mqtt.state("import_error", result.caption_error or "none")
            self.pending = None
        importing = [
            item for item in self.library.list_items() if item.status == "importing"
        ]
        if importing:
            self.mqtt.state("import_progress", str(round(importing[0].progress * 100)))
        rows = self.library.list_ready()
        self.mqtt.library({row.id: f"{row.title[:200]} [{row.id[:12]}]" for row in rows})

    def ready_item(self, media_id: str) -> LibraryItem:
        """Reject missing or incomplete media before changing playback."""
        item = self.library.get(media_id)
        if (
            item.status != "ready"
            or item.video_path is None
            or not item.video_path.is_file()
        ):
            raise ValueError("Library media is not ready")
        return item

    def ready_path(self, media_id: str) -> Path:
        """Return the validated local file for ready media."""
        item = self.ready_item(media_id)
        if item.video_path is None:
            raise ValueError("Ready media has no video file")
        return item.video_path

    def caption(self, media_id: str, timestamp: float, offset: float) -> str:
        """Select a cached cue for the displayed frame, leaving silence blank."""
        try:
            item = self.library.get(media_id)
            path = item.captions_path
            if path is None:
                self.mqtt.state("caption_error", item.caption_error or "none")
                return ""
            if path not in self.caption_cache:
                self.caption_cache.clear()
                self.caption_cache[path] = CaptionTrack.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            text = caption_at(self.caption_cache[path].cues, timestamp, offset)
        except (OSError, ValueError, KeyError):
            self.mqtt.state(
                "caption_error", "Offline caption track is missing or invalid"
            )
            return ""
        self.mqtt.state("caption_error", "none")
        return text

    def close(self) -> None:
        """Stop the importer process group with a bounded wait on service shutdown."""
        pending = self.pending
        if pending is None:
            return
        if pending.poll() is None:
            with suppress(ProcessLookupError):
                os.killpg(pending.pid, signal.SIGTERM)
            try:
                _ = pending.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(pending.pid, signal.SIGKILL)
                _ = pending.wait(timeout=3)
        if pending.stdout is not None:
            pending.stdout.close()
        self.pending = None
