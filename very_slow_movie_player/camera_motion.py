"""Compare camera frames using downsampled luminance."""

from __future__ import annotations

from statistics import median
from typing import TYPE_CHECKING

from PIL.Image import Resampling

if TYPE_CHECKING:
    from PIL.Image import Image

MOTION_CHECK_SECONDS = 5
SAMPLE_SIZE = (64, 36)
PIXEL_DELTA = 24
MINIMUM_CHANGED_PERCENT = 4


def motion_sample(image: Image) -> bytes:
    """Reduce the image to a small grayscale sample for motion detection."""
    with (
        image.convert("L") as gray,
        gray.resize(  # pyright: ignore[reportUnknownMemberType]
            SAMPLE_SIZE, Resampling.BOX
        ) as sample,
    ):
        return sample.tobytes()


def motion_detected(previous: bytes | None, current: bytes) -> bool:
    """Look for local luminance changes while ignoring uniform brightness shifts."""
    if previous is None:
        return False

    deltas = [after - before for before, after in zip(previous, current, strict=True)]
    brightness_shift = median(deltas)
    changed_pixels = sum(abs(delta - brightness_shift) >= PIXEL_DELTA for delta in deltas)
    return changed_pixels * 100 >= len(current) * MINIMUM_CHANGED_PERCENT
