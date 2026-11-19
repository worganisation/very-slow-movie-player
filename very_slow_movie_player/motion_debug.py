"""Record motion-triggered clips from the Raspberry Pi camera."""

from __future__ import annotations

import subprocess  # noqa: S404 - fixed camera and video commands without a shell
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import partial
from io import BytesIO
from logging import INFO, basicConfig, getLogger
from os import environ, read
from pathlib import Path
from select import select
from shutil import which
from signal import SIGTERM, signal
from threading import Event
from time import monotonic
from typing import TYPE_CHECKING

from camera_motion import MOTION_CHECK_SECONDS, motion_detected, motion_sample
from mqtt_connection import mqtt_client
from paho.mqtt.client import MQTT_ERR_SUCCESS
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from types import FrameType

    from paho.mqtt.client import Client

WIDTH = 4608
HEIGHT = 2592
FPS = 14.35
MONITOR_WIDTH = 640
MONITOR_HEIGHT = 360
MONITOR_FPS = 5
MONITOR_CHECK_SECONDS = 1
QUIET_SECONDS = 300
JPEG_END = b"\xff\xd9"
LOGGER = getLogger(__name__)


def jpeg_frames(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Split the camera encoder's concatenated JPEG stream into whole frames."""
    pending = bytearray()
    scan_from = 0
    for chunk in chunks:
        pending.extend(chunk)
        while (end := pending.find(JPEG_END, scan_from)) >= 0:
            end += len(JPEG_END)
            if not pending.startswith(b"\xff\xd8"):
                raise ValueError("Unexpected data in camera MJPEG stream")

            yield bytes(pending[:end])
            del pending[:end]
            scan_from = 0

        scan_from = max(0, len(pending) - len(JPEG_END) + 1)
        if len(pending) > WIDTH * HEIGHT * 3:
            raise ValueError("Camera MJPEG stream did not produce a complete frame")


class EventRecorder:
    """Write a full-resolution clip after the motion trigger."""

    def __init__(
        self, output: Path, ffmpeg: str, on_clip_saved: Callable[[Path], None]
    ) -> None:
        self.output: Path = output
        self.ffmpeg: str = ffmpeg
        self.on_clip_saved: Callable[[Path], None] = on_clip_saved
        self.clip: tuple[subprocess.Popen[bytes], Path] | None = None

    def record(self, frames: Iterable[bytes], triggered_at: float) -> None:
        """Write frames until the scene has been quiet for five minutes."""
        last_motion = triggered_at
        try:
            for frame, timestamp, moving in motion_frames(frames, MOTION_CHECK_SECONDS):
                if self.clip is None:
                    self.clip = self._start_clip()
                    LOGGER.info(
                        "First recording frame %.2fs after detection",
                        timestamp - triggered_at,
                    )

                encoder, _ = self.clip
                if encoder.stdin is None:
                    raise RuntimeError("FFmpeg input pipe is unavailable")

                _ = encoder.stdin.write(frame)
                if moving:
                    last_motion = timestamp
                if timestamp - last_motion >= QUIET_SECONDS:
                    return
        finally:
            self.finish()

    def _start_clip(self) -> tuple[subprocess.Popen[bytes], Path]:
        """Open a clip and write the two minutes preceding the trigger."""
        event_time = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.output / f"motion-{event_time}.mkv"
        encoder = subprocess.Popen(  # noqa: S603 - trusted ffmpeg and fixed arguments
            [
                self.ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-n",
                "-f",
                "mjpeg",
                "-framerate",
                str(FPS),
                "-i",
                "pipe:0",
                "-c:v",
                "copy",
                "-f",
                "matroska",
                str(path.with_suffix(".partial")),
            ],
            stdin=subprocess.PIPE,
        )
        LOGGER.info("Motion detected, recording %s", path.name)
        return encoder, path

    def finish(self) -> None:
        """Close the container and expose the completed clip under its final name."""
        if self.clip is None:
            return

        encoder, path = self.clip
        self.clip = None
        try:
            if encoder.stdin is not None:
                encoder.stdin.close()
        finally:
            try:
                status = encoder.wait(timeout=15)
            except subprocess.TimeoutExpired:
                encoder.kill()
                _ = encoder.wait()
                raise

        if status:
            raise RuntimeError(f"FFmpeg exited with status {status} writing {path.name}")

        _ = path.with_suffix(".partial").rename(path)
        LOGGER.info("Saved %s", path)
        self.on_clip_saved(path)


def motion_frames(
    frames: Iterable[bytes], check_seconds: float
) -> Iterator[tuple[bytes, float, bool]]:
    """Pass through camera frames and compare samples at the requested interval."""
    previous: bytes | None = None
    next_check = 0.0
    for frame in frames:
        timestamp = monotonic()
        moving = False
        if timestamp >= next_check:
            with Image.open(BytesIO(frame)) as image:
                _ = image.draft("L", (image.width // 8, image.height // 8))
                current = motion_sample(image)

            moving = motion_detected(previous, current)
            previous = current
            next_check = timestamp + check_seconds

        yield frame, timestamp, moving


def publish_clip_saved(client: Client, topic: str, path: Path) -> None:
    """Publish the finalized clip's filename using the existing MQTT broker."""
    if client.is_connected():
        receipt = client.publish(topic, path.name, qos=1, retain=False)
        if receipt.rc == MQTT_ERR_SUCCESS:
            try:
                receipt.wait_for_publish(timeout=2)
                if receipt.is_published():
                    return
            except (RuntimeError, ValueError) as exc:
                LOGGER.debug("MQTT publish failed: %s", exc)

    LOGGER.warning("Could not notify Home Assistant about saved clip %s", path.name)


def main() -> None:
    """Run the camera until systemd stops the debug service."""
    from settings import SETTINGS  # noqa: PLC0415 - load service settings at entry

    basicConfig(level=INFO, format="%(message)s")
    camera = which("rpicam-vid") or which("libcamera-vid")
    ffmpeg = which("ffmpeg")
    if camera is None or ffmpeg is None:
        raise RuntimeError(
            "Motion debug requires rpicam-vid (or libcamera-vid) and ffmpeg"
        )

    output = Path(environ["STATE_DIRECTORY"])
    client = mqtt_client(SETTINGS, f"vsmp-{SETTINGS.mqtt_device_id}-motion-debug")
    topic = (
        f"{SETTINGS.mqtt_topic_prefix}/{SETTINGS.mqtt_device_id}/event/motion_clip_saved"
    )
    _ = client.connect_async(SETTINGS.mqtt_host, SETTINGS.mqtt_port, keepalive=30)
    if client.loop_start() != MQTT_ERR_SUCCESS:
        LOGGER.warning("Could not start MQTT network loop")

    try:
        notify = partial(publish_clip_saved, client, topic)
        capture_motion(camera, EventRecorder(output, ffmpeg, notify))
    finally:
        _ = client.disconnect()
        _ = client.loop_stop()


def camera_chunks(stream: int, stopping: Event) -> Iterator[bytes]:
    """Read the camera pipe while allowing a service stop to interrupt the wait."""
    while not stopping.is_set():
        ready, _, _ = select([stream], [], [], 0.25)
        if ready:
            if not (chunk := read(stream, 256 * 1024)):
                if not stopping.is_set():
                    raise RuntimeError("Camera stream ended unexpectedly")

                return

            yield chunk


@contextmanager
def camera_frames(
    camera: str, stopping: Event, *, recording: bool
) -> Iterator[Iterator[bytes]]:
    """Open one camera mode and release the camera before switching modes."""
    if recording:
        width, height, fps = WIDTH, HEIGHT, FPS
        mode = f"{WIDTH}:{HEIGHT}:10"
    else:
        width, height, fps = MONITOR_WIDTH, MONITOR_HEIGHT, MONITOR_FPS
        mode = "2304:1296:10"  # Binned sensor mode with full field of view.

    with subprocess.Popen(  # noqa: S603 - trusted Pi camera CLI and fixed arguments
        [
            camera,
            "--nopreview",
            "--rotation",
            "180",
            "--timeout",
            "0",
            "--codec",
            "mjpeg",
            "--mode",
            mode,
            "--width",
            str(width),
            "--height",
            str(height),
            "--framerate",
            str(fps),
            "--quality",
            "80",
            "--output",
            "-",
        ],
        stdout=subprocess.PIPE,
    ) as capture:
        try:
            if capture.stdout is None:
                raise RuntimeError("Camera output pipe is unavailable")

            yield jpeg_frames(camera_chunks(capture.stdout.fileno(), stopping))
        finally:
            capture.terminate()
            try:
                _ = capture.wait(timeout=0.75)
            except subprocess.TimeoutExpired:
                capture.kill()
                _ = capture.wait()


def capture_motion(camera: str, recorder: EventRecorder) -> None:
    """Watch at low resolution and switch to full resolution only for clips."""
    stopping = Event()

    def stop_capture(_signum: int, _frame: FrameType | None) -> None:
        stopping.set()

    previous_handler = signal(SIGTERM, stop_capture)
    try:
        while not stopping.is_set():
            LOGGER.info("Watching for motion at %sx%s", MONITOR_WIDTH, MONITOR_HEIGHT)
            with camera_frames(camera, stopping, recording=False) as frames:
                triggered_at = next(
                    (
                        timestamp
                        for _, timestamp, moving in motion_frames(
                            frames, MONITOR_CHECK_SECONDS
                        )
                        if moving
                    ),
                    None,
                )

            if triggered_at is None or stopping.is_set():
                return

            with camera_frames(camera, stopping, recording=True) as frames:
                recorder.record(frames, triggered_at)
    finally:
        _ = signal(SIGTERM, previous_handler)


if __name__ == "__main__":
    main()
