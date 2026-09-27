"""Download new videos from a YouTube playlist for playing on the VSMP."""

from __future__ import annotations

import subprocess  # noqa: S404 - invoke the installed CLI without shell expansion
import sys

from settings import SETTINGS
from utils import const


def main() -> None:
    """Download playlist videos, recording only successful video IDs in the archive."""
    playlist_id = SETTINGS.yt_playlist_id
    const.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    _ = subprocess.run(  # noqa: S603 - arguments are passed directly, without a shell
        [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-config",
            "--yes-playlist",
            "--no-abort-on-error",
            "--download-archive",
            str(const.MEDIA_DIR / ".youtube-download-archive.txt"),
            "--format",
            "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "--recode-video",
            "mp4",
            "--output",
            str(const.MEDIA_DIR / "%(title).200B [%(id)s].%(ext)s"),
            f"https://www.youtube.com/playlist?list={playlist_id}",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
