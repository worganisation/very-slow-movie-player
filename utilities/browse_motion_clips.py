"""Browse and download previews of VSMPPi motion clips from a Mac."""  # noqa: INP001

from __future__ import annotations

import argparse
import re
import subprocess  # noqa: S404
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


def _show_clips(clips: list[str]) -> None:
    print("\nCompleted recordings (newest first)")
    if not clips:
        print("No completed recordings on the SSD.")

    for index, name in enumerate(clips, start=1):
        timestamp = name.removeprefix("motion-").removesuffix(".mkv")
        started = datetime.strptime(timestamp, CLIP_TIMESTAMP).replace(tzinfo=UTC)
        local_time = started.astimezone().strftime("%d %b %Y %H:%M:%S %Z")
        print(f"  {index:>3}  {local_time}")


def _delete(ssh: list[str], name: str) -> bool:
    print(f"Permanently delete the original {name} from the SSD?")
    if input("Type delete to confirm: ") != "delete":
        return False

    subprocess.run([*ssh, _clip_command(name) + 'rm -- "$clip"'], check=True)  # noqa: S603
    print(f"Deleted {name} from the SSD.")
    return True


def _act_on_clip(ssh: list[str], clips: list[str], action: str, number: str) -> None:
    index = int(number) - 1
    if not 0 <= index < len(clips):
        print("Choose a clip number from the list.")
        return

    name = clips[index]
    try:
        if action == "x":
            if _delete(ssh, name):
                clips.pop(index)
            return

        path = _download(ssh, name)
        print(f"Saved to {path}")
        if action == "p":
            subprocess.run(["/usr/bin/open", str(path)], check=True)  # noqa: S603
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Could not complete the action: {error}")


def _browse(ssh: list[str]) -> None:
    clips = _list_clips(ssh)
    while True:
        _show_clips(clips)
        choice = input(
            "\nPlay [p 1], download [d 1], delete [x 1], refresh [r], quit [q]: "
        )
        match choice.lower().split():
            case ["q"]:
                return
            case ["r"]:
                clips = _list_clips(ssh)
            case [action, number] if action in {"p", "d", "x"} and number.isdecimal():
                _act_on_clip(ssh, clips, action, number)
            case _:
                print("Choose an action from the menu.")


def main() -> None:
    """Run the interactive motion clip browser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", type=ip_address, help="Override VSMPPi's IP address")
    args = parser.parse_args()

    ssh = ["/usr/bin/ssh", "-T"]
    for option in SSH_OPTIONS:
        ssh.extend(("-o", option))

    if args.address is not None:
        ssh.extend(("-o", f"HostName={args.address}", "-o", "HostKeyAlias=vsmppi.local"))

    try:
        _browse([*ssh, "vsmppi"])
    except (EOFError, KeyboardInterrupt):
        print()
    except (OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Could not list recordings on VSMPPi: {error}\n")


if __name__ == "__main__":
    main()
