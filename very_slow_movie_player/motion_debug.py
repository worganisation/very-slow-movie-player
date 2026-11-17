"""Record motion-triggered clips from the Raspberry Pi camera."""

from __future__ import annotations

import subprocess  # noqa: S404 - fixed camera and video commands without a shell
from collections import deque
from datetime import UTC, datetime
from io import BytesIO
from itertools import takewhile
from logging import INFO, basicConfig, getLogger
from os import environ, read
from pathlib import Path
from shutil import copyfileobj, which
from signal import SIGTERM, signal
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING

from camera_motion import MOTION_CHECK_SECONDS, motion_detected, motion_sample
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from types import FrameType

WIDTH = 4608
HEIGHT = 2592
FPS = 14.35
PRE_ROLL_SECONDS = 120
QUIET_SECONDS = 600
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
    """Keep the most recent frames in a temporary disk buffer."""

    def __init__(self, output: Path, pre_roll: Path, ffmpeg: str) -> None:
        self.output: Path = output
        self.pre_roll: Path = pre_roll
        self.ffmpeg: str = ffmpeg
        self.buffer: deque[tuple[float, Path]] = deque()
        self.frame_number: int = 0
        self.clip: tuple[subprocess.Popen[bytes], Path] | None = None
        self.last_motion: float = 0.0

    def ingest(self, frame: bytes, timestamp: float, *, moving: bool) -> None:
        """Write the trigger frame once, and extend the event for each detection."""
        while self.buffer and self.buffer[0][0] < timestamp - PRE_ROLL_SECONDS:
            _, expired = self.buffer.popleft()
            expired.unlink()

        if moving:
            self.last_motion = timestamp
            if self.clip is None:
                self._start_clip()

        if self.clip is not None:
            encoder, _ = self.clip
            if encoder.stdin is None:
                raise RuntimeError("FFmpeg input pipe is unavailable")

            _ = encoder.stdin.write(frame)
            if timestamp - self.last_motion >= QUIET_SECONDS:
                self.finish()

        frame_path = self.pre_roll / f"{self.frame_number}.jpg"
        _ = frame_path.write_bytes(frame)
        self.buffer.append((timestamp, frame_path))
        self.frame_number += 1

    def _start_clip(self) -> None:
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
        self.clip = (encoder, path)
        LOGGER.info("Motion detected, recording %s", path.name)

        if encoder.stdin is None:
            raise RuntimeError("FFmpeg input pipe is unavailable")

        for _, frame_path in self.buffer:
            with frame_path.open("rb") as buffered:
                copyfileobj(buffered, encoder.stdin)

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


def monitor(frames: Iterable[bytes], recorder: EventRecorder) -> None:
    """Sample motion on the playback detector's cadence and record every frame."""
    previous: bytes | None = None
    next_check = 0.0
    for frame in frames:
        timestamp = monotonic()
        moving = False
        if timestamp >= next_check:
            with Image.open(BytesIO(frame)) as image:
                _ = image.draft("L", (WIDTH // 8, HEIGHT // 8))
                current = motion_sample(image)

            moving = motion_detected(previous, current)
            previous = current
            next_check = timestamp + MOTION_CHECK_SECONDS

        recorder.ingest(frame, timestamp, moving=moving)


def main() -> None:
    """Run the camera until systemd stops the debug service."""
    basicConfig(level=INFO, format="%(message)s")
    camera = which("rpicam-vid") or which("libcamera-vid")
    ffmpeg = which("ffmpeg")
    if camera is None or ffmpeg is None:
        raise RuntimeError(
            "Motion debug requires rpicam-vid (or libcamera-vid) and ffmpeg"
        )

    output = Path(environ["STATE_DIRECTORY"])
    with TemporaryDirectory(dir=output, prefix=".pre-roll-") as pre_roll:
        capture_motion(camera, EventRecorder(output, Path(pre_roll), ffmpeg))


def capture_motion(camera: str, recorder: EventRecorder) -> None:
    """Stream full-sensor MJPEG from the camera until the service stops."""
    with subprocess.Popen(  # noqa: S603 - trusted Pi camera CLI and fixed arguments
        [
            camera,
            "--nopreview",
            "--timeout",
            "0",
            "--codec",
            "mjpeg",
            "--mode",
            f"{WIDTH}:{HEIGHT}:10",
            "--width",
            str(WIDTH),
            "--height",
            str(HEIGHT),
            "--framerate",
            str(FPS),
            "--quality",
            "80",
            "--output",
            "-",
        ],
        stdout=subprocess.PIPE,
    ) as capture:
        stopping = False

        def stop_capture(_signum: int, _frame: FrameType | None) -> None:
            nonlocal stopping
            stopping = True
            capture.terminate()

        _ = signal(SIGTERM, stop_capture)
        try:
            if capture.stdout is None:
                raise RuntimeError("Camera output pipe is unavailable")

            stream = capture.stdout.fileno()
            LOGGER.info("Motion debug is buffering video")
            chunks = iter(lambda: read(stream, 256 * 1024), b"")
            monitor(takewhile(lambda _: not stopping, jpeg_frames(chunks)), recorder)
            if not stopping:
                raise RuntimeError("Camera stream ended unexpectedly")
        finally:
            capture.terminate()
            recorder.finish()


if __name__ == "__main__":
    main()
