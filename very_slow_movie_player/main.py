"""Display frames from a local video on the E-paper panel."""

from __future__ import annotations

from contextlib import ExitStack
from fractions import Fraction
from itertools import chain
from math import ceil, isfinite
from math import pow as float_pow
from os import getenv
from pathlib import Path
from signal import SIG_IGN, SIGTERM, signal
from sys import exception as active_exception
from tempfile import NamedTemporaryFile
from time import sleep
from typing import TYPE_CHECKING, NoReturn

from immich import Asset, ImmichAlbum
from PIL import Image
from PIL.Image import Dither, Resampling
from utils import EPaperDisplay, const
from utils.progress import get_progress, load_progress, set_progress
from wg_utilities.decorators import process_exception
from wg_utilities.loggers import get_streaming_logger

from ffmpeg import input as ffmpeg_input
from ffmpeg import probe

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import FrameType

    from ffmpeg import ProbeInfo, ProbeStream

LOGGER = get_streaming_logger(__name__)


DISPLAY = EPaperDisplay()


@process_exception(logger=LOGGER)
def extract_frame(
    video_path: Path,
    frame: int,
    *,
    fps: float,
    stream_index: int,
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
        extract_output_path (Path): the path at which to place the extracted image file

    Returns:
        str: the output path, again provided for ease of use
    """
    LOGGER.info("Extracting frame #%i from `%s`", frame, video_path)

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
            ffmpeg_input(video_path, ss=f"{frame / fps:.6f}")
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


@process_exception(logger=LOGGER)
def format_image(image_path: Path, frame_output_path: Path = const.FRAME_PATH) -> Path:
    """Formats an image for displaying on the EPD.

    Args:
        image_path (Path): the name of the file to format
        frame_output_path (Path): the path at which to place the frame image file

    Returns:
        str: the output path - the user will know this anyway, but it's done for ease
         of use
    """
    LOGGER.debug(
        "Formatting image `%s`, outputting to `%s`",
        image_path,
        frame_output_path,
    )

    pil_im = Image.open(image_path)

    scale_factor = min(DISPLAY.WIDTH / pil_im.size[0], DISPLAY.HEIGHT / pil_im.size[1])

    resize_width = round(pil_im.size[0] * scale_factor)
    resize_height = round(pil_im.size[1] * scale_factor)

    letterboxed = Image.new("RGB", (DISPLAY.WIDTH, DISPLAY.HEIGHT))
    offset = (
        round((DISPLAY.WIDTH - resize_width) / 2),
        round((DISPLAY.HEIGHT - resize_height) / 2),
    )

    letterboxed.paste(
        pil_im.resize((resize_width, resize_height), Resampling.LANCZOS),
        offset,
    )

    letterboxed.save(frame_output_path)

    return frame_output_path


@process_exception(logger=LOGGER)
def display_image(
    image_path: Path = const.FRAME_PATH,
    display_time: float = const.FRAME_DELAY,
) -> None:
    """Display an image on the EPD.

    Args:
        image_path (Path): the path to the file to display on the EPD
        display_time (Union([int, float])): the number of seconds to display the
         image for
    """
    output_path = format_image(image_path)

    LOGGER.info("Displaying `%s` for %s seconds", image_path, display_time)

    # Darken midtones before dithering so they remain visible on the E-paper panel.
    gamma = float(getenv("VSMP_IMAGE_GAMMA", "1.7"))
    if gamma <= 0:
        raise ValueError("VSMP_IMAGE_GAMMA must be positive")
    grayscale = Image.open(output_path).convert("L")
    darkened = grayscale.point(
        [round(255 * float_pow(value / 255, gamma)) for value in range(256)],
    )
    pil_im = darkened.convert(mode="1", dither=Dither.FLOYDSTEINBERG)

    # display the image
    DISPLAY.display(DISPLAY.getbuffer(pil_im))

    sleep(display_time)


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

    duration = stream.get("duration") or info.get("format", {}).get("duration")
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


def video_metadata(video_path: Path) -> tuple[int, float, int]:
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
    return usable_frame_count(video_stream, probe_data, fps), fps, stream_index


@process_exception(logger=LOGGER)
def play_video(video_path: Path) -> None:
    """Play a video file on the E-Paper display.

    Args:
        video_path (Path): the path to the file to play

    Raises:
        FileNotFoundError: if the video path doesn't exist
    """
    LOGGER.info("Input video is `%s`", video_path.as_posix())

    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    frame_count, fps, stream_index = video_metadata(video_path)
    LOGGER.info("There are %d frames in this video", frame_count)

    if getenv("ALWAYS_RESTART_VIDEOS", "false").lower() == "true":
        LOGGER.debug("Resetting progress log for `%s`", video_path)
        set_progress(video_path, 0, frame_count)

    current_frame = get_progress(video_path)
    if current_frame >= frame_count:
        current_frame = 0

    hrs, secs = divmod(
        (((frame_count - current_frame) / const.INCREMENT) * const.FRAME_DELAY),
        3600,
    )
    mins, secs = divmod(secs, 60)

    LOGGER.info(
        "It's going to take %ih%im%is to play this video",
        hrs,
        mins,
        secs,
    )

    for frame in range(current_frame, frame_count, const.INCREMENT):
        set_progress(video_path, frame, frame_count)

        # Use ffmpeg to extract a frame from the movie, crop it,
        # letterbox it and output it as a JPG
        output_path = extract_frame(video_path, frame, fps=fps, stream_index=stream_index)

        display_image(output_path)

    set_progress(video_path, frame_count, frame_count)


def play_immich_asset(album: ImmichAlbum, asset: Asset) -> None:
    """Display one album asset using the existing image and video paths."""
    media = album.download(asset)
    if asset.kind == "VIDEO":
        play_video(media)
    else:
        display_image(media, 300)


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
    try:
        DISPLAY.pi.module_exit()
    except BaseException as exc:
        if cleanup_error is None:
            cleanup_error = exc
        else:
            LOGGER.exception("Additional failure releasing display hardware")
    if cleanup_error is not None:
        if previous_error is not None:
            LOGGER.error("Display cleanup also failed: %s", cleanup_error)
        else:
            raise cleanup_error


@process_exception(logger=LOGGER)
def main() -> None:  # noqa: PLR0912 - source preparation and hardware lifecycle
    """Play a configured local video, or assets from an Immich album."""
    source = getenv("VSMP_SOURCE", "local").casefold()
    if source not in {"local", "immich"}:
        raise ValueError("VSMP_SOURCE must be 'local' or 'immich'")

    with ExitStack() as stack:
        video: Path | None = None
        assets: Iterator[Asset] | None = None
        first_asset: Asset | None = None
        album = stack.enter_context(ImmichAlbum()) if source == "immich" else None
        if album is None:
            video_path = getenv("VSMP_VIDEO_PATH")
            if not video_path:
                raise ValueError("VSMP_VIDEO_PATH must point to a local video")
            video = Path(video_path).expanduser()
            if not video.is_file():
                raise FileNotFoundError(video)
        else:
            assets = iter(album.assets())
            first_asset = next(assets, None)
            if first_asset is None:
                LOGGER.warning("The configured Immich album contains no assets")
                sleep(300)
                return

        _ = load_progress()

        previous_sigterm_handler = signal(SIGTERM, stop_on_sigterm)
        display_initialized = False
        try:
            _ = DISPLAY.init()
            display_initialized = True
            DISPLAY.clear()
            if video is not None:
                play_video(video)
            elif album is not None and first_asset is not None and assets is not None:
                for asset in chain((first_asset,), assets):
                    play_immich_asset(album, asset)
            else:
                raise RuntimeError("No playback source was prepared")
        finally:
            previous_error = active_exception()
            _ = signal(SIGTERM, SIG_IGN)
            try:
                if DISPLAY.pi.module_initialized:
                    clean_up_display(
                        enter_sleep=display_initialized,
                        previous_error=previous_error,
                    )
            finally:
                _ = signal(SIGTERM, previous_sigterm_handler)


if __name__ == "__main__":
    main()
