"""Display frames from a local video on the E-paper panel."""

from __future__ import annotations

from fractions import Fraction
from json import dumps, loads
from math import ceil
from os import getenv
from pathlib import Path
from time import sleep
from typing import TypedDict

from PIL import Image
from PIL.Image import Dither, Resampling
from utils import EPaperDisplay, const
from wg_utilities.decorators import process_exception
from wg_utilities.loggers import get_streaming_logger

from ffmpeg import input as ffmpeg_input  # type: ignore[attr-defined]
from ffmpeg import probe  # type: ignore[attr-defined]

LOGGER = get_streaming_logger(__name__)


DISPLAY = EPaperDisplay()


class ProgressInfo(TypedDict):
    """Model for the progress info objects in the log."""

    current: int
    total: int


@process_exception(logger=LOGGER)
def extract_frame(
    video_path: Path,
    frame: int,
    *,
    fps: float,
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
        extract_output_path (Path): the path at which to place the extracted image file

    Returns:
        str: the output path, again provided for ease of use
    """
    LOGGER.info("Extracting frame #%i from `%s`", frame, video_path)

    (
        ffmpeg_input(video_path, ss=f"{frame / fps:.6f}")
        .output(extract_output_path, vframes=1)
        .overwrite_output()
        .run(capture_stdout=True, capture_stderr=True)
    )

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
def get_progress(video_path: Path, default: int = 0) -> int:
    """Get the number of the most recently played frame from the JSON log file.

    This is so we can resume in the case of an early exit.

    Args:
        video_path (Path): the path to the file being played
        default (int): a default value to return if the file isn't logged

    Returns:
        int: the number of the frame that was played most recently
    """
    log_data: dict[str, ProgressInfo] = loads(const.PROGRESS_LOG.read_text())

    LOGGER.info("Getting progress for `%s`", video_path)

    try:
        return log_data[video_path.as_posix()]["current"]
    except KeyError:
        return default


@process_exception(logger=LOGGER)
def set_progress(
    video_path: Path,
    current_frame: int,
    frame_count: int | None = None,
) -> None:
    """Update the JSON log file, so we can resume if the program is exited.

    Args:
        video_path (Path): the path to the file being played
        current_frame (int): which frame has been played most recently
        frame_count (int): the total number of frames in the video
    """
    log_data = loads(const.PROGRESS_LOG.read_text())

    progress = {video_path.as_posix(): {"current": current_frame}}

    LOGGER.debug("Updating log for `%s` to frame #%i", video_path, current_frame)

    if frame_count:
        progress[video_path.as_posix()]["total"] = frame_count

    log_data.update(progress)

    const.PROGRESS_LOG.write_text(dumps(log_data, indent=2, sort_keys=True))


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

    # Open JPG in PIL and dither the image into a 1 bit bitmap
    pil_im = Image.open(output_path).convert(mode="1", dither=Dither.FLOYDSTEINBERG)

    # display the image
    DISPLAY.display(DISPLAY.getbuffer(pil_im))

    sleep(display_time)


def video_metadata(video_path: Path) -> tuple[int, float]:
    """Get the video frame count and frames per second from ffprobe."""
    probe_data = probe(video_path)
    video_stream = next(
        (
            stream
            for stream in probe_data.get("streams", [])
            if stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        raise RuntimeError("No video stream found in ffmpeg probe")

    fps = 0.0
    for rate in (video_stream.get("avg_frame_rate"), video_stream.get("r_frame_rate")):
        try:
            fps = float(Fraction(rate))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if fps > 0:
            break
    if fps <= 0:
        raise RuntimeError("No usable video frame rate found in ffmpeg probe")

    raw_frame_count = video_stream.get("nb_frames")
    if raw_frame_count and str(raw_frame_count).isdigit():
        frame_count = int(raw_frame_count)
    else:
        duration = video_stream.get("duration") or probe_data.get("format", {}).get(
            "duration",
        )
        if duration is None:
            raise RuntimeError("No video duration found in ffmpeg probe")
        frame_count = ceil(float(duration) * fps)

    return frame_count, fps


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

    frame_count, fps = video_metadata(video_path)
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
        output_path = extract_frame(video_path, frame, fps=fps)

        display_image(output_path)

    set_progress(video_path, frame_count, frame_count)


@process_exception(logger=LOGGER)
def choose_next_video() -> Path | None:
    """Pick which video to play next.

    Either find one that hasn't yet been finished, or one that hasn't even been started.

    Returns:
        str: the name of the video file to start playing
    """
    log_data: dict[str, ProgressInfo] = loads(const.PROGRESS_LOG.read_text())

    LOGGER.info("There are %i videos in the log", len(log_data))

    for log_file_path, video in log_data.items():
        if not Path(log_file_path).is_file():
            LOGGER.debug("`%s` no longer available", log_file_path)
            continue

        if (total := video.get("total", -1)) - (
            current_frame := video.get("current", -1)
        ) > const.INCREMENT:
            LOGGER.info(
                "`%s` has only had %i/%i frames played",
                log_file_path,
                current_frame,
                total,
            )
            return Path(log_file_path)

    for file in const.MEDIA_DIR.iterdir():
        if file.suffix != ".mp4":
            LOGGER.debug("`%s` is not an mp4", file)
            continue

        if file.resolve().as_posix() in log_data:
            LOGGER.debug("`%s` has already been played", file)
            continue

        LOGGER.info("`%s` hasn't been played yet, returning", file)
        return file

    return None


@process_exception(logger=LOGGER)
def main() -> None:
    """Play the local video selected in ``VSMP_VIDEO_PATH``."""
    video_path = getenv("VSMP_VIDEO_PATH")
    if not video_path:
        raise ValueError("VSMP_VIDEO_PATH must point to a local video")

    video = Path(video_path).expanduser()
    if not video.is_file():
        raise FileNotFoundError(video)

    const.PROGRESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    if not const.PROGRESS_LOG.is_file():
        LOGGER.warning("Progress log not found at `%s`", const.PROGRESS_LOG)
        const.PROGRESS_LOG.write_text("{}")

    DISPLAY.init()
    try:
        DISPLAY.clear()
        play_video(video)
    finally:
        DISPLAY.sleep()
        DISPLAY.pi.module_exit()


if __name__ == "__main__":
    main()
