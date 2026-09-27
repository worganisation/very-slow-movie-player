"""Display frames from a local video on the E-paper panel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fractions import Fraction
from io import BytesIO
from math import ceil, isfinite
from math import pow as float_pow
from pathlib import Path
from signal import SIG_IGN, SIGTERM, signal
from sys import exception as active_exception
from tempfile import NamedTemporaryFile
from time import monotonic
from typing import TYPE_CHECKING, NoReturn, cast

from caption_render import render_caption
from controls import (
    CommandMailbox,
    PlaybackControls,
    apply_command,
    load_controls,
    save_controls,
)
from immich import Asset, ImmichAlbum
from library_runtime import LibraryRuntime
from mqtt_controls import HAClient
from PIL import Image, ImageOps
from PIL.Image import Dither, Resampling
from utils import EPaperDisplay, const
from utils.logging import logger
from utils.progress import get_progress, load_progress, set_progress

from ffmpeg import input as ffmpeg_input
from ffmpeg import probe

if TYPE_CHECKING:
    from types import FrameType

    from ffmpeg import ProbeInfo, ProbeStream

DISPLAY = EPaperDisplay()


class PanelRefreshError(Exception):
    """A hardware failure that should stop playback for systemd recovery."""


@logger.catch(reraise=True)
def extract_frame(
    video_path: Path,
    frame: int,
    *,
    fps: float,
    stream_index: int,
    start_time: float = 0,
    extract_output_path: Path = const.EXTRACT_PATH,
) -> Path:
    """Output a frame from the video file to a JPG image.

    Steps:
      - ffmpeg_input: takes a filepath as input and opens the video
        - filename: the name of the file to import
        - ss: the position to seek to
      - filter*:
        - scale: resizes the image
        - force_original_aspect_ratio: set to "decrease", forcing image to be
           downsized if necessary
      - filter*:
        - pad: letterboxes the image
        - -1, -1: x and y coords to place image at within padded area - negative
           defaults to centre

    * These have been replaced by the `format_image` function. Original lines were:
        .filter("scale", DISPLAY.WIDTH, DISPLAY.HEIGHT, force_original_aspect_ratio=1)
        .filter("pad", DISPLAY.WIDTH, DISPLAY.HEIGHT, -1, -1)

    Args:
        video_path (Path): the name of the file to extract the frame from
        frame (int): the number of the frame to extract
        fps (float): the video's frames per second
        stream_index (int): the selected ffprobe stream's absolute index
        start_time (float): the selected video stream's absolute presentation origin
        extract_output_path (Path): the path at which to place the extracted image file

    Returns:
        str: the output path, again provided for ease of use
    """
    logger.info("Extracting frame #{} from `{}`", frame, video_path)

    if (
        video_path.exists()
        and extract_output_path.exists()
        and extract_output_path.samefile(video_path)
    ):
        raise ValueError("Frame output path must differ from the input video")

    extract_output_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove the previous frame before seeking: ffmpeg can exit successfully at EOF
    # without writing a new file.
    extract_output_path.unlink(missing_ok=True)
    with NamedTemporaryFile(
        dir=extract_output_path.parent,
        prefix=f".{extract_output_path.stem}.",
        suffix=".jpg",
        delete=False,
    ) as output:
        temporary = Path(output.name)
    try:
        _ = (
            ffmpeg_input(
                video_path, ss=f"{start_time + frame / fps:.6f}", seek_timestamp=1
            )
            .output(str(temporary), vframes=1, map=f"0:{stream_index}")
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        if temporary.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg produced no frame at position {frame}")
        with Image.open(temporary) as candidate:
            if candidate.format != "JPEG":
                raise ValueError("ffmpeg produced a non-JPEG frame")
            candidate.verify()
        _ = temporary.replace(extract_output_path)
    finally:
        temporary.unlink(missing_ok=True)

    return extract_output_path


@logger.catch(reraise=True)
def format_image(image_path: Path, frame_output_path: Path = const.FRAME_PATH) -> Path:
    """Formats an image for displaying on the EPD.

    Args:
        image_path (Path): the name of the file to format
        frame_output_path (Path): the path at which to place the frame image file

    Returns:
        str: the output path - the user will know this anyway, but it's done for ease
         of use
    """
    logger.debug(
        "Formatting image `{}`, outputting to `{}`",
        image_path,
        frame_output_path,
    )

    with Image.open(image_path) as source:
        oriented = ImageOps.exif_transpose(source)
        with oriented:
            scale_factor = min(
                DISPLAY.WIDTH / oriented.width,
                DISPLAY.HEIGHT / oriented.height,
            )
            resize_width = max(1, round(oriented.width * scale_factor))
            resize_height = max(1, round(oriented.height * scale_factor))
            offset = (
                round((DISPLAY.WIDTH - resize_width) / 2),
                round((DISPLAY.HEIGHT - resize_height) / 2),
            )
            with (
                Image.new("RGB", (DISPLAY.WIDTH, DISPLAY.HEIGHT)) as letterboxed,
                oriented.resize(  # pyright: ignore[reportUnknownMemberType]
                    (resize_width, resize_height), Resampling.LANCZOS
                ) as resized,
            ):
                letterboxed.paste(resized, offset)
                letterboxed.save(frame_output_path)

    return frame_output_path


@logger.catch(reraise=True)
def display_image(
    image_path: Path,
    gamma: float,
    mqtt: HAClient,
    *,
    caption: str = "",
    controls: PlaybackControls | None = None,
) -> None:
    """Display an image on the EPD.

    Args:
        image_path (Path): the path to the file to display on the EPD
        gamma (float): correction applied before dithering
        mqtt (HAClient): persistent MQTT publisher
        caption: Text selected for the exact displayed media timestamp.
        controls: Confirmed caption styling preferences.
    """
    output_path = format_image(image_path)

    logger.info("Displaying `{}`", image_path)

    # Darken midtones before dithering so they remain visible on the E-paper panel.
    with (
        Image.open(output_path) as formatted,
        formatted.convert("L") as grayscale,
        grayscale.point(  # pyright: ignore[reportUnknownMemberType]
            [round(255 * float_pow(value / 255, gamma)) for value in range(256)],
        ) as darkened,
        darkened.convert(mode="1", dither=Dither.FLOYDSTEINBERG) as monochrome,
    ):
        rendered = monochrome
        if controls is not None and caption:
            try:
                rendered = render_caption(
                    darkened,
                    caption,
                    style=controls.caption_style,
                    font=controls.caption_font,
                    font_size=controls.caption_font_size,
                )
            except (ValueError, RuntimeError) as exc:
                mqtt.state("caption_error", str(exc)[:255])
            else:
                mqtt.state("caption_error", "none")
        try:
            buffer = DISPLAY.getbuffer(rendered)
            DISPLAY.display(buffer)
        except Exception as exc:
            raise PanelRefreshError("Panel refresh failed") from exc
        try:
            with BytesIO() as png:
                rendered.save(png, format="PNG")
                mqtt.image(png.getvalue())
        except Exception as exc:  # noqa: BLE001 - reporting must not interrupt playback
            logger.warning(
                "MQTT image update failed after display: {}", type(exc).__name__
            )


def usable_frame_rate(stream: ProbeStream) -> float:
    """Read a positive finite frame rate from ffprobe stream metadata."""
    for rate in (stream.get("avg_frame_rate"), stream.get("r_frame_rate")):
        if rate is None:
            continue
        try:
            fps = float(Fraction(rate))
        except (OverflowError, TypeError, ValueError, ZeroDivisionError):
            continue
        if isfinite(fps) and fps > 0:
            return fps
    raise RuntimeError("No usable video frame rate found in ffmpeg probe")


def usable_frame_count(stream: ProbeStream, info: ProbeInfo, fps: float) -> int:
    """Read or estimate a positive finite frame count."""
    raw_frame_count = stream.get("nb_frames")
    if raw_frame_count is not None and str(raw_frame_count).isdigit():
        frame_count = int(raw_frame_count)
        if frame_count == 0:
            raise RuntimeError("Video stream has zero frames")
        return frame_count

    duration = stream.get("duration")
    # Matroska often reports an absolute end timestamp in DURATION, while its
    # container duration includes other streams and the initial timestamp offset.
    end_tag = stream.get("tags", {}).get("DURATION")
    if duration is None and end_tag is not None:
        try:
            hours, minutes, seconds = end_tag.split(":")
            end_time = float(hours) * 3600 + float(minutes) * 60 + float(seconds)
            duration = str(end_time - float(stream.get("start_time") or 0))
        except (ValueError, OverflowError):
            pass
    if duration is None:
        duration = info.get("format", {}).get("duration")
    if duration is None:
        raise RuntimeError("No video duration found in ffmpeg probe")
    try:
        seconds = float(duration)
    except (OverflowError, TypeError, ValueError) as exc:
        raise RuntimeError("No usable video duration found in ffmpeg probe") from exc
    estimated_frames = seconds * fps
    if not isfinite(seconds) or seconds <= 0 or not isfinite(estimated_frames):
        raise RuntimeError("No usable video duration found in ffmpeg probe")
    frame_count = ceil(estimated_frames)
    if frame_count <= 0:
        raise RuntimeError("Video stream has zero frames")
    return frame_count


def video_metadata(video_path: Path) -> tuple[int, float, int, float]:
    """Get a playable video stream's frame count, rate, and absolute index."""
    probe_data = probe(video_path)
    video_stream = next(
        (
            stream
            for stream in probe_data.get("streams", [])
            if stream.get("codec_type") == "video"
            and stream.get("disposition", {}).get("attached_pic") != 1
        ),
        None,
    )
    if video_stream is None:
        raise RuntimeError("No playable video stream found in ffmpeg probe")
    stream_index = video_stream.get("index")
    if stream_index is None or stream_index < 0:
        raise RuntimeError("No usable video stream index found in ffmpeg probe")

    fps = usable_frame_rate(video_stream)
    start_time = float(str(video_stream.get("start_time") or 0))
    if not isfinite(start_time):
        raise RuntimeError("No usable video timestamp origin found in ffmpeg probe")
    return (
        usable_frame_count(video_stream, probe_data, fps),
        fps,
        stream_index,
        start_time,
    )


class PlaybackRuntime:
    """Serialize commands and panel scheduling on the playback thread."""

    def __init__(self) -> None:
        self.controls: PlaybackControls
        self.overridden: set[str]
        self.controls, self.overridden = load_controls()
        self.mailbox: CommandMailbox = CommandMailbox()
        self.mqtt: HAClient = HAClient(self.mailbox)
        self.library: LibraryRuntime = LibraryRuntime(self.mqtt)
        self.last_panel_refresh: float | None = None
        self.current_path: Path | None = None
        self.current_kind: str = "video"
        self.current_media: str = "none"
        self.current_frame: int | None = None
        self.current_frame_count: int | None = None
        self.current_video: tuple[Path, int, float, int, float] | None = None
        self.next_album_refresh: float = 0
        self.selection: tuple[str, Path, str, str, str] = self.selection_key()
        self.buttons: set[str] = set()
        self.mqtt.state("playback_status", "starting")
        self.mqtt.state("current_media", "none")
        self.mqtt.state("video_current_frame", "None")
        self.mqtt.state("video_frame_count", "None")
        self.mqtt.state("last_error", "none")
        self.mqtt.state("last_refresh", "None")
        self.mqtt.state("next_refresh", "None")
        self.library.refresh()
        self.publish_controls()

    def selection_key(self) -> tuple[str, Path, str, str, str]:
        """Track fields that change the active source iterator."""
        value = self.controls
        return (
            value.source,
            value.video_path,
            str(value.album),
            value.media_type,
            value.library_id,
        )

    def publish_controls(self) -> None:
        """Report confirmed settings only after persistence succeeds."""
        values = cast("dict[str, object]", self.controls.model_dump(mode="json"))
        for name, value in values.items():
            if isinstance(value, bool):
                state = "ON" if value else "OFF"
            elif name == "library_id":
                state = self.mqtt.library_label(str(value))
            elif name == "album":
                state = self.mqtt.album_label(str(value))
            else:
                state = str(value)
            self.mqtt.state(name, state)

    def refresh_albums(self) -> None:
        """Fetch selector labels on the playback thread, never in MQTT callbacks."""
        with ImmichAlbum(self.controls.album) as album:
            rows = album.albums()
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.name] = counts.get(row.name, 0) + 1
        labels = {
            str(row.id): (
                f"{row.name[:210]} [{row.id}]" if counts[row.name] > 1 else row.name[:255]
            )
            for row in rows
        }
        selected = str(self.controls.album)
        _ = labels.setdefault(selected, selected)
        self.mqtt.albums(labels)
        self.mqtt.state("album", self.mqtt.album_label(selected))
        self.next_album_refresh = monotonic() + 3600

    def process_commands(self) -> None:
        """Apply queued changes transactionally outside the MQTT network thread."""
        self.library.refresh()
        self.mqtt.state("library_id", self.mqtt.library_label(self.controls.library_id))
        settings, buttons = self.mailbox.drain()
        self.buttons.update(buttons)
        if "restart_video" in self.buttons and self.current_kind == "photo":
            self.buttons.discard("restart_video")
            self.mqtt.state("last_error", "No current video to restart")
        if "redisplay" in self.buttons and self.current_path is None:
            self.buttons.discard("redisplay")
            self.mqtt.state("last_error", "No frame to redisplay")
        for name, payload in settings.items():
            if name in {"import_youtube", "import_jellyfin"}:
                self.library.submit(name, payload)
            else:
                self.apply_setting(name, payload)
        if not settings and not buttons:
            self.maybe_refresh_albums()

    def maybe_refresh_albums(self) -> None:
        """Refresh selector options periodically even while local video plays."""
        if monotonic() >= self.next_album_refresh:
            self.next_album_refresh = monotonic() + 300
            try:
                self.refresh_albums()
            except Exception as exc:  # noqa: BLE001 - other source can keep playing
                self.mqtt.state("last_error", f"album list: {exc}"[:255])

    def apply_setting(self, name: str, payload: str) -> None:
        """Persist one command before publishing its confirmed value."""
        try:
            candidate = apply_command(self.controls, name, payload)
            if name == "library_id" and candidate.library_id != "none":
                _ = video_metadata(self.library.ready_path(candidate.library_id))
            if name == "source" and candidate.source == "library":
                _ = self.library.ready_item(candidate.library_id)
            if name == "video_path":
                _ = video_metadata(candidate.video_path)
            if name == "album":
                self.validate_album(candidate)
            save_controls(candidate, self.overridden | {name})
        except Exception as exc:  # noqa: BLE001 - invalid input cannot replace good state
            self.mqtt.state("last_error", f"{name}: {exc}"[:255])
            return
        self.controls = candidate
        self.overridden.add(name)
        self.publish_controls()
        self.mqtt.state("last_error", "none")
        if self.selection_key() != self.selection:
            self.selection = self.selection_key()
            self.buttons.clear()
            self.mqtt.state("playback_status", "switching source")
        if name == "album":
            self.next_album_refresh = 0

    @staticmethod
    def validate_album(candidate: PlaybackControls) -> None:
        """Accept only albums returned by the configured Immich account."""
        with ImmichAlbum(candidate.album) as album:
            if candidate.album not in {row.id for row in album.albums()}:
                raise ValueError("Immich album is not accessible")

    def mark_displayed(
        self,
        path: Path,
        media: str,
        current_frame: int | None = None,
        frame_count: int | None = None,
        *,
        kind: str = "video",
        video_frame: tuple[Path, int, float, int, float] | None = None,
    ) -> None:
        """Record the panel result and schedule the next normal update."""
        self.last_panel_refresh = monotonic()
        self.current_path = path
        self.current_kind = kind
        self.current_media = media
        self.current_frame = current_frame
        self.current_frame_count = frame_count
        self.current_video = video_frame
        self.mqtt.state(
            "current_media",
            (media if self.controls.source == "library" else Path(media).stem)[:255],
        )
        self.mqtt.state(
            "video_current_frame",
            str(current_frame) if current_frame is not None else "None",
        )
        self.mqtt.state(
            "video_frame_count", str(frame_count) if frame_count is not None else "None"
        )
        self.mqtt.state("last_refresh", datetime.now(UTC).isoformat())
        self.mqtt.state("playback_status", "playing")
        self.mqtt.state("last_error", "none")

    def wait_ready(self, selection: tuple[str, Path, str, str, str]) -> bool:
        """Interrupt waits for commands, but enforce 180 seconds between frames."""
        while True:
            self.process_commands()
            if self.selection != selection:
                return False
            if self.redisplay_if_ready():
                continue
            if (
                self.controls.playback_enabled
                and ("next" in self.buttons or "restart_video" in self.buttons)
            ) and self._minimum_wait() <= 0:
                return True
            if self.controls.playback_enabled and self._normal_wait() <= 0:
                self.mqtt.state("next_refresh", "None")
                return True
            self.wait_for_refresh()

    def wait_for_refresh(self) -> None:
        """Publish the next wake time and wait for commands or the playback clock."""
        remaining = self._minimum_wait() if self.buttons else self._normal_wait()
        if not self.controls.playback_enabled:
            self.mqtt.state("playback_status", "paused")
            self.mqtt.state("next_refresh", "None")
            remaining = 60
        elif self.last_panel_refresh is not None:
            self.mqtt.state(
                "next_refresh",
                (datetime.now(UTC) + timedelta(seconds=max(0, remaining))).isoformat(),
            )
        _ = self.mailbox.wake.wait(timeout=max(0.05, min(remaining, 60)))

    def redisplay_if_ready(self) -> bool:
        """Recompose the last frame when requested and the panel dwell has elapsed."""
        if not (
            self.controls.playback_enabled
            and "redisplay" in self.buttons
            and self.current_path is not None
            and self._minimum_wait() <= 0
        ):
            return False
        self.buttons.discard("redisplay")
        image_path = self.current_path
        caption = ""
        if self.current_video is not None:
            source, frame, fps, stream_index, start_time = self.current_video
            image_path = extract_frame(
                source, frame, fps=fps, stream_index=stream_index, start_time=start_time
            )
            caption = self.caption_text(frame / fps)
        display_image(
            image_path,
            self.controls.gamma,
            self.mqtt,
            caption=caption,
            controls=self.controls,
        )
        self.mark_displayed(
            image_path,
            self.current_media,
            self.current_frame,
            self.current_frame_count,
            kind=self.current_kind,
            video_frame=self.current_video,
        )
        return True

    def caption_text(self, timestamp: float) -> str:
        """Resolve captions only for selected offline library media."""
        if self.controls.source != "library" or not self.controls.captions_enabled:
            self.mqtt.state("caption_error", "none")
            return ""
        return self.library.caption(
            self.controls.library_id, timestamp, self.controls.caption_offset
        )

    def _minimum_wait(self) -> float:
        if self.last_panel_refresh is None:
            return 0
        return max(0.0, 180.0 - (monotonic() - self.last_panel_refresh))

    def _normal_wait(self) -> float:
        if self.last_panel_refresh is None:
            return 0
        interval = (
            self.controls.photo_interval
            if self.current_kind == "photo"
            else self.controls.video_interval
        )
        return max(
            self._minimum_wait(), interval - (monotonic() - self.last_panel_refresh)
        )


def play_video(
    runtime: PlaybackRuntime,
    video_path: Path,
    *,
    immich: bool = False,
    media_label: str | None = None,
) -> None:
    """Advance a video with settings and actions applied between panel updates."""
    frame_count, fps, stream_index, start_time = video_metadata(video_path)
    if runtime.controls.always_restart_videos:
        set_progress(video_path, 0, frame_count)
    frame = get_progress(video_path)
    if frame >= frame_count:
        frame = 0
    selection = runtime.selection
    while True:
        if not runtime.wait_ready(selection):
            return
        if "restart_video" in runtime.buttons:
            runtime.buttons.discard("restart_video")
            frame = 0
        if "next" in runtime.buttons:
            runtime.buttons.discard("next")
            if immich:
                return
            if frame >= frame_count:
                frame = 0
        elif frame >= frame_count:
            set_progress(video_path, frame_count, frame_count)
            return
        output = extract_frame(
            video_path, frame, fps=fps, stream_index=stream_index, start_time=start_time
        )
        display_image(
            output,
            runtime.controls.gamma,
            runtime.mqtt,
            caption=runtime.caption_text(frame / fps),
            controls=runtime.controls,
        )
        runtime.mark_displayed(
            output,
            media_label or str(video_path),
            frame + 1,
            frame_count,
            video_frame=(video_path, frame, fps, stream_index, start_time),
        )
        set_progress(video_path, frame, frame_count)
        frame += runtime.controls.frame_advance


def play_immich_asset(runtime: PlaybackRuntime, album: ImmichAlbum, asset: Asset) -> None:
    """Display an eligible album asset while retaining the current panel on errors."""
    if runtime.controls.media_type == "photos" and asset.kind != "IMAGE":
        return
    if runtime.controls.media_type == "videos" and asset.kind != "VIDEO":
        return
    media = album.download(asset)
    media_label = asset.filename
    if asset.kind == "VIDEO":
        play_video(runtime, media, immich=True, media_label=media_label)
        return
    if not runtime.wait_ready(runtime.selection):
        return
    display_image(media, runtime.controls.gamma, runtime.mqtt)
    runtime.mark_displayed(media, media_label, kind="photo")
    if runtime.wait_ready(runtime.selection):
        runtime.buttons.discard("next")


def stop_on_sigterm(_signum: int, _frame: FrameType | None) -> NoReturn:
    """Let Python unwind display cleanup when systemd stops the service."""
    _ = signal(SIGTERM, SIG_IGN)
    raise SystemExit(0)


def clean_up_display(*, enter_sleep: bool, previous_error: BaseException | None) -> None:
    """Release GPIO and SPI without hiding the original playback failure."""
    cleanup_error: BaseException | None = None
    if enter_sleep:
        try:
            DISPLAY.sleep()
        except BaseException as exc:  # noqa: BLE001 - preserve cleanup on SIGTERM
            cleanup_error = exc
    else:
        try:
            DISPLAY.power_off()
        except BaseException as exc:  # noqa: BLE001 - preserve partial-init failure
            cleanup_error = exc
    try:
        DISPLAY.pi.module_exit()
    except BaseException as exc:  # noqa: BLE001 - finish cleanup after SIGTERM
        if cleanup_error is None:
            cleanup_error = exc
        else:
            logger.exception("Additional failure releasing display hardware")
    if cleanup_error is not None:
        if previous_error is not None:
            logger.error("Display cleanup also failed: {}", cleanup_error)
        else:
            raise cleanup_error


def play_immich_source(runtime: PlaybackRuntime) -> None:
    """Iterate selected album assets, interrupting when selection changes."""
    selected = runtime.selection
    with ImmichAlbum(runtime.controls.album) as album:
        try:
            runtime.refresh_albums()
        except Exception as exc:  # noqa: BLE001 - playback still uses stable ID
            runtime.mqtt.state("last_error", f"album list: {exc}"[:255])
        seen = False
        for asset in album.assets():
            runtime.process_commands()
            if runtime.selection != selected:
                return
            media_type = runtime.controls.media_type
            if (media_type == "photos" and asset.kind != "IMAGE") or (
                media_type == "videos" and asset.kind != "VIDEO"
            ):
                continue
            seen = True
            play_asset_with_retry(runtime, album, asset, selected)
        if not seen and runtime.selection == selected:
            runtime.mqtt.state("playback_status", "waiting for media")
            _ = runtime.mailbox.wake.wait(60)


def play_asset_with_retry(
    runtime: PlaybackRuntime,
    album: ImmichAlbum,
    asset: Asset,
    selection: tuple[str, Path, str, str, str],
) -> None:
    """Allow Next to skip a failing Immich asset without clearing the panel."""
    while runtime.selection == selection:
        try:
            play_immich_asset(runtime, album, asset)
        except PanelRefreshError:
            raise
        except Exception as exc:  # noqa: BLE001 - media/network failures are recoverable
            logger.warning("Immich asset failed; retaining current frame: {}", exc)
            runtime.mqtt.state("last_error", str(exc)[:255])
            runtime.mqtt.state("playback_status", "source error")
            runtime.mqtt.state("next_refresh", "None")
            _ = runtime.mailbox.wake.wait(60)
            runtime.process_commands()
            if "next" in runtime.buttons:
                runtime.buttons.discard("next")
                return
        else:
            return


def play_selected_source(runtime: PlaybackRuntime) -> None:
    """Keep source failures from clearing the last successful frame."""
    try:
        if runtime.controls.source == "local":
            play_video(runtime, runtime.controls.video_path)
        elif runtime.controls.source == "library":
            item = runtime.library.ready_item(runtime.controls.library_id)
            play_video(
                runtime, runtime.library.ready_path(item.id), media_label=item.title
            )
        else:
            play_immich_source(runtime)
    except PanelRefreshError:
        raise
    except Exception as exc:  # noqa: BLE001 - source errors retain the last panel image
        logger.warning("Media source failed; retaining current frame: {}", exc)
        runtime.mqtt.state("last_error", str(exc)[:255])
        runtime.mqtt.state("playback_status", "source error")
        runtime.mqtt.state("next_refresh", "None")
        _ = runtime.mailbox.wake.wait(60)


@logger.catch(reraise=True)
def main() -> None:
    """Run a single hardware owner with a persistent, asynchronous MQTT client."""
    runtime = PlaybackRuntime()
    _ = load_progress()
    previous_sigterm_handler = signal(SIGTERM, stop_on_sigterm)
    display_initialized = False
    mqtt_started = False
    try:
        _ = DISPLAY.init()
        display_initialized = True
        DISPLAY.clear()  # Startup clear is separate from scheduled frame refreshes.
        try:
            runtime.mqtt.start()
            mqtt_started = True
        except (ConnectionError, OSError) as exc:
            logger.warning("MQTT startup failed; playback continues: {}", exc)
        while True:
            runtime.process_commands()
            play_selected_source(runtime)
    finally:
        previous_error = active_exception()
        _ = signal(SIGTERM, SIG_IGN)
        try:
            try:
                runtime.library.close()
            except Exception as exc:  # noqa: BLE001 - always finish hardware cleanup
                logger.warning("Import worker shutdown failed: {}", type(exc).__name__)
            if mqtt_started:
                runtime.mqtt.stop()
            if DISPLAY.pi.module_initialized:
                clean_up_display(
                    enter_sleep=display_initialized,
                    previous_error=previous_error,
                )
        finally:
            _ = signal(SIGTERM, previous_sigterm_handler)


if __name__ == "__main__":
    main()
