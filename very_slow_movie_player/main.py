"""Currently just displays photos from an album on Google Photos."""

from __future__ import annotations

from json import dumps, loads
from os import getenv
from pathlib import Path
from time import sleep
from typing import TypedDict

from PIL import Image
from PIL.Image import Dither, Resampling
from utils import EPaperDisplay, const
from wg_utilities.clients.google_photos import GooglePhotosClient, MediaType
from wg_utilities.decorators import process_exception
from wg_utilities.loggers import get_streaming_logger

from ffmpeg import input as ffmpeg_input  # type: ignore[attr-defined]
from ffmpeg import probe  # type: ignore[attr-defined]

LOGGER = get_streaming_logger(__name__)


GOOGLE = GooglePhotosClient(
    client_id=getenv("GOOGLE_CLIENT_ID"),
    client_secret=getenv("GOOGLE_CLIENT_SECRET"),
    headless_auth_link_callback=LOGGER.info,
    scopes=[
        "https://www.googleapis.com/auth/photoslibrary",
        "https://www.googleapis.com/auth/photoslibrary.sharing",
    ],
)


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
        extract_output_path (Path): the path at which to place the extracted image file

    Returns:
        str: the output path, again provided for ease of use
    """
    LOGGER.info("Extracting frame #%i from `%s`", frame, video_path)

    (
        ffmpeg_input(video_path, ss=f"{frame * 41.666666}ms")
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

    progress = {video_path: {"current": current_frame}}

    LOGGER.debug("Updating log for `%s` to frame #%i", video_path, current_frame)

    if frame_count:
        progress[video_path]["total"] = frame_count

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


@process_exception(logger=LOGGER)
def play_video(video_path: Path) -> None:
    """Play a video file on the E-Paper display.

    Args:
        video_path (str): the path to the file to play

    Raises:
        FileNotFoundError: if the video path doesn't exist
        RuntimeError: if the video file is un-usable for some reason
    """
    LOGGER.info("Input video is `%s`", video_path.as_posix())

    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    # Check how many frames are in the movie
    probe_streams = probe(video_path).get("streams")

    if not probe_streams:
        raise RuntimeError("No streams found in ffmpeg probe")

    frame_count = int(
        probe_streams[0].get("nb_frames") or 24 * float(probe_streams[0]["duration"]),
    )

    LOGGER.info("There are %d frames in this video", frame_count)

    if getenv("ALWAYS_RESTART_VIDEOS", "true").lower() == "true":
        LOGGER.debug("Resetting progress log for `%s`", video_path)
        set_progress(video_path, 0, frame_count)

    current_frame = get_progress(
        video_path,
        2000 if frame_count >= 10000 else 0,  # ruff: ignore[magic-value-comparison]
    )

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
        output_path = extract_frame(video_path, frame)

        display_image(output_path)


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
    """Loop through all videos.

    Loop through the movie directory and then the VSMP Google Photos album.
    """
    if not const.PROGRESS_LOG.is_file():
        LOGGER.warning("Progress log not found at `%s`", const.PROGRESS_LOG)
        const.PROGRESS_LOG.write_text("{}")

    # Initialise and clear the screen
    DISPLAY.init()
    DISPLAY.clear()
    _ = """
     while next_video := choose_next_video():
         try:
             play_video(next_video)
         except Exception as exc:
             # raise
             LOGGER.exception(
                 "Unable to play video: `%s - %s`", type(exc).__name__, exc.__str__()
             )
    """
    media_items = set(GOOGLE.get_album_by_name("Very Slow Movie Player").media_items)

    for item in media_items:
        item.download(
            const.MEDIA_DIR,
            width_override=DISPLAY.WIDTH,
            height_override=DISPLAY.HEIGHT,
        )

        if item.media_type == MediaType.VIDEO:
            play_video(item.local_path)
        elif item.media_type == MediaType.IMAGE:
            display_image(item.local_path, 300)

    DISPLAY.sleep()
    DISPLAY.pi.module_exit()


if __name__ == "__main__":
    main()
