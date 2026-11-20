"""Browse and download previews of VSMPPi motion clips from a Mac."""  # noqa: INP001

from __future__ import annotations

import argparse
import curses
import re
import subprocess  # noqa: S404
import sys
from contextlib import suppress
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from shlex import quote
from tempfile import NamedTemporaryFile

# ruff: noqa: T201

CLIP_NAME = re.compile(r"motion-(\d{8}T\d{6}\.\d{6}Z)\.mkv")
CLIP_TIMESTAMP = "%Y%m%dT%H%M%S.%fZ"
CLIP_DIR = "/mnt/vsmp-video/motion-debug"
CHECK_STORAGE = (
    f'if [ "$(findmnt -n -T {CLIP_DIR} -o UUID)" != 6AFF-5964 ]; '
    "then echo 'VSMPPi recording SSD is unavailable.' >&2; exit 1; fi; "
    f"cd {CLIP_DIR} || exit 1; "
)
MIN_TERMINAL_HEIGHT = 7
MIN_TERMINAL_WIDTH = 38
PREVIEW_DIR = Path.home() / "Documents" / "VSMP Videos"
SSH_OPTIONS = (
    "BatchMode=yes",
    "StrictHostKeyChecking=yes",
    "UpdateHostKeys=no",
    "ConnectTimeout=8",
    "ConnectionAttempts=1",
    "ControlMaster=no",
    "ControlPath=none",
    "ForwardAgent=no",
    "ClearAllForwardings=yes",
    "PermitLocalCommand=no",
)


def _list_clips(ssh: list[str]) -> list[str]:
    result = subprocess.run(  # noqa: S603
        [*ssh, CHECK_STORAGE + r"""find . -maxdepth 1 -type f -printf '%f\n'"""],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    return sorted(
        (name for name in result.stdout.splitlines() if CLIP_NAME.fullmatch(name)),
        reverse=True,
    )


def _clip_command(name: str) -> str:
    if not CLIP_NAME.fullmatch(name):
        raise ValueError(f"Invalid clip name: {name}")

    return CHECK_STORAGE + f'clip={quote(name)}; [ -f "$clip" ] && [ ! -L "$clip" ] && '


def _show_progress(lines: subprocess.Popen[str]) -> None:
    if lines.stderr is None:
        raise RuntimeError("Preview progress stream is unavailable")

    duration = 0.0
    rendered = 0.0
    speed = 0.0

    for line in lines.stderr:
        key, _, value = line.strip().partition("=")
        match key:
            case "duration" if value not in {"", "N/A"}:
                duration = float(value)
            case "out_time_us" if value != "N/A":
                rendered = int(value) / 1_000_000
            case "speed" if value.endswith("x"):
                speed = float(value.removesuffix("x"))
            case "progress":
                progress = f"{rendered:.0f}s processed"
                if duration > 0:
                    progress = f"{min(rendered / duration, 1):.0%}"
                    if speed > 0:
                        eta = max(0, round((duration - rendered) / speed))
                        progress += f"  ETA {eta // 60}m {eta % 60:02d}s"

                print(f"\rPreview: {progress:<23}", end="", flush=True)
            case _ if not re.fullmatch(r"[\w]+", key):
                print(f"\n{line.rstrip()}")

    print()


def _download(ssh: list[str], name: str) -> Path:
    destination = PREVIEW_DIR / f"{Path(name).stem}-preview.mp4"
    if destination.is_file():
        return destination

    command = (
        _clip_command(name)
        + """{ duration=$(ffprobe -v error -show_entries format=duration """
        """-of default=noprint_wrappers=1:nokey=1 "$clip"); """
        r"""printf 'duration=%s\n' "$duration" >&2; """
        """exec ffmpeg -hide_banner -loglevel error -nostdin -nostats -progress pipe:2 """
        """-lowres 2 -i "$clip" -map 0:v:0 -vf fps=5,scale=640:-2 -an """
        """-c:v libx264 -preset ultrafast -crf 28 -maxrate 500k -bufsize 1M """
        """-pix_fmt yuv420p -movflags +frag_keyframe+empty_moov+default_base_moof """
        """-f mp4 pipe:1; }"""
    )

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=PREVIEW_DIR, suffix=".part", delete=False) as output:
        temporary_path = Path(output.name)

    try:
        print(f"Creating preview of {name}…")
        with (
            temporary_path.open("wb") as output,
            subprocess.Popen(  # noqa: S603
                [*ssh, command], stdout=output, stderr=subprocess.PIPE, text=True
            ) as preview,
        ):
            _show_progress(preview)
            if preview.wait():
                raise RuntimeError("Could not create the preview")

        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)

    return destination


def _clip_time(name: str) -> str:
    timestamp = name.removeprefix("motion-").removesuffix(".mkv")
    started = datetime.strptime(timestamp, CLIP_TIMESTAMP).replace(tzinfo=UTC)
    return started.astimezone().strftime("%d %b %Y %H:%M:%S %Z")


def _show_clips(
    screen: curses.window, clips: list[str], selected: int, status: str
) -> bool:
    height, width = screen.getmaxyx()
    screen.erase()
    if height < MIN_TERMINAL_HEIGHT or width < MIN_TERMINAL_WIDTH:
        screen.addnstr(0, 0, "Resize terminal (q quits)", width - 1)
        return False

    visible_rows = height - 6
    first = max(0, min(selected - visible_rows // 2, len(clips) - visible_rows))
    screen.addnstr(0, 0, f"Completed recordings ({len(clips)})", width - 1, curses.A_BOLD)
    if not clips:
        screen.addnstr(2, 0, "No completed recordings on the SSD.", width - 1)

    for row, name in enumerate(clips[first : first + visible_rows], start=2):
        index = first + row - 2
        marker, style = (
            (">", curses.A_REVERSE) if index == selected else (" ", curses.A_NORMAL)
        )
        screen.addnstr(
            row, 0, f"{marker} {index + 1:>3}  {_clip_time(name)}", width - 1, style
        )

    screen.addnstr(height - 3, 0, status, width - 1)
    screen.addnstr(height - 2, 0, "↑↓ Select   Enter Play   d Download", width - 1)
    screen.addnstr(height - 1, 0, "x Delete    r Refresh    q Quit", width - 1)
    return True


def _confirm_delete(screen: curses.window) -> bool:
    height, width = screen.getmaxyx()
    screen.move(height - 1, 0)
    screen.clrtoeol()
    screen.addnstr(height - 1, 0, "Delete original from SSD? [y/N]", width - 1)
    return screen.get_wch() == "y"


def _select_clip(
    screen: curses.window, clips: list[str], selected: int, status: str
) -> tuple[str, int]:
    with suppress(curses.error):
        curses.curs_set(0)

    while True:
        can_select = _show_clips(screen, clips, selected, status) and bool(clips)
        match screen.get_wch():
            case curses.KEY_UP if can_select:
                selected = max(0, selected - 1)
            case curses.KEY_DOWN if can_select:
                selected = min(len(clips) - 1, selected + 1)
            case "\n" | "\r" | curses.KEY_ENTER if can_select:
                return "play", selected
            case "d" if can_select:
                return "download", selected
            case "x" if can_select:
                if _confirm_delete(screen):
                    return "delete", selected

                status = "Deletion cancelled."
            case "r":
                return "refresh", selected
            case "q":
                return "quit", selected


def _browse(ssh: list[str]) -> None:
    clips = _list_clips(ssh)
    selected = 0
    status = ""

    while True:
        action, selected = curses.wrapper(_select_clip, clips, selected, status)
        try:
            match action:
                case "quit":
                    return
                case "refresh":
                    current = clips[selected] if clips else None
                    clips = _list_clips(ssh)
                    selected = clips.index(current) if current in clips else 0
                    status = "List refreshed."
                case "delete":
                    name = clips[selected]
                    subprocess.run(  # noqa: S603
                        [*ssh, _clip_command(name) + 'rm -- "$clip"'], check=True
                    )
                    clips.pop(selected)
                    selected = max(0, min(selected, len(clips) - 1))
                    status = f"Deleted {name} from the SSD."
                case "play" | "download":
                    path = _download(ssh, clips[selected])
                    status = f"Saved to {path}"
                    if action == "play":
                        subprocess.run(["/usr/bin/open", str(path)], check=True)  # noqa: S603
        except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
            status = f"Could not complete the action: {error}"


def main() -> None:
    """Run the interactive motion clip browser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", type=ip_address, help="Override VSMPPi's IP address")
    args = parser.parse_args()
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.error("Run the clip browser in an interactive terminal.")

    ssh = ["/usr/bin/ssh", "-T"]
    for option in SSH_OPTIONS:
        ssh.extend(("-o", option))

    if args.address is not None:
        ssh.extend(("-o", f"HostName={args.address}", "-o", "HostKeyAlias=vsmppi.local"))

    try:
        _browse([*ssh, "vsmppi"])
    except KeyboardInterrupt:
        print()
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Could not list recordings on VSMPPi: {error}\n")


if __name__ == "__main__":
    main()
