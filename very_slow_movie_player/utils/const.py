"""Constants."""

from __future__ import annotations

from pathlib import Path
from tempfile import gettempdir
from typing import Final

FRAME_DELAY: Final = 120

REPO_PATH: Final = Path(__file__).parents[1]

MEDIA_DIR: Final = REPO_PATH / ".media"
TMP_DIR = Path(gettempdir())


EXTRACT_PATH: Final = TMP_DIR / "vsmp_extract.jpg"
FRAME_PATH: Final = TMP_DIR / "vsmp_frame.jpg"

PROGRESS_LOG: Final = MEDIA_DIR / "progress_log.json"
"""JSON file containing record of frames displayed per movie."""

INCREMENT = 12
"""The number of frames to skip between each displayed frame."""

YDL_OPTS: Final = {
    "format": "bestvideo[height<=480]/best[height<=480]",
    "outtmpl": f"{MEDIA_DIR}/%(title)s.%(ext)s",
    "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
}
