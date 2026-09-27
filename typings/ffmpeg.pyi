from pathlib import Path
from typing import TypedDict

class ProbeStream(TypedDict, total=False):
    index: int
    codec_type: str
    disposition: dict[str, int]
    avg_frame_rate: str
    r_frame_rate: str
    nb_frames: str
    duration: str

class ProbeInfo(TypedDict, total=False):
    streams: list[ProbeStream]
    format: dict[str, str]

class Stream:
    def output(self, filename: str, *, vframes: int, map: str) -> Stream: ...  # noqa: A002
    def overwrite_output(self) -> Stream: ...
    def run(
        self,
        *,
        capture_stdout: bool,
        capture_stderr: bool,
    ) -> tuple[bytes, bytes]: ...

def input(filename: Path, *, ss: str) -> Stream: ...  # noqa: A001
def probe(filename: Path) -> ProbeInfo: ...
