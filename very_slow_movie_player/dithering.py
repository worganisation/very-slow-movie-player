"""Shared monochrome rendering methods for photos and captioned video frames."""

from __future__ import annotations

from enum import StrEnum

from PIL import Image


class DitheringMethod(StrEnum):
    """Stable MQTT options, also accepted as environment configuration values."""

    FLOYD_STEINBERG = "Floyd-Steinberg"
    ORDERED = "Ordered (Bayer 8x8)"
    ORDERED_VERTICAL = "Ordered (1x2 pixels)"
    FLOYD_SQUARE = "Floyd-Steinberg (2x2 pixels)"
    FLOYD_HORIZONTAL = "Floyd-Steinberg (2x1 pixels)"
    THRESHOLD = "Threshold (no dithering)"


# Bayer ranks match the full-resolution and vertically paired panel trials.
_BAYER = (
    (0, 32, 8, 40, 2, 34, 10, 42),
    (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44, 4, 36, 14, 46, 6, 38),
    (60, 28, 52, 20, 62, 30, 54, 22),
    (3, 35, 11, 43, 1, 33, 9, 41),
    (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47, 7, 39, 13, 45, 5, 37),
    (63, 31, 55, 23, 61, 29, 53, 21),
)
_PIXEL_SIZES = {
    DitheringMethod.ORDERED_VERTICAL: (1, 2),
    DitheringMethod.FLOYD_SQUARE: (2, 2),
    DitheringMethod.FLOYD_HORIZONTAL: (2, 1),
}


def _ordered(image: Image.Image) -> Image.Image:
    pixels = bytes(
        255
        if value * 128
        > (2 * _BAYER[(index // image.width) % 8][index % image.width % 8] + 1) * 255
        else 0
        for index, value in enumerate(image.tobytes())
    )
    with Image.frombytes("L", image.size, pixels) as result:
        return result.convert("1", dither=Image.Dither.NONE)


def dither_image(
    image: Image.Image,
    method: DitheringMethod = DitheringMethod.FLOYD_STEINBERG,
) -> Image.Image:
    """Dither a gamma-corrected image without changing its output dimensions.

    Paired modes average before quantization, then enlarge whole binary pixels.
    Caption glyphs must be drawn after this operation to retain native detail.
    """
    method = DitheringMethod(method)
    pixel_width, pixel_height = _PIXEL_SIZES.get(method, (1, 1))
    size = (
        max(1, (image.width + pixel_width - 1) // pixel_width),
        max(1, (image.height + pixel_height - 1) // pixel_height),
    )
    with (
        image.convert("L") as grayscale,
        grayscale.resize(size, Image.Resampling.BOX) as scaled,  # pyright: ignore[reportUnknownMemberType]
    ):
        if method in {DitheringMethod.ORDERED, DitheringMethod.ORDERED_VERTICAL}:
            monochrome = _ordered(scaled)
        else:
            algorithm = (
                Image.Dither.NONE
                if method == DitheringMethod.THRESHOLD
                else Image.Dither.FLOYDSTEINBERG
            )
            monochrome = scaled.convert("1", dither=algorithm)
    with monochrome:
        return monochrome.resize(image.size, Image.Resampling.NEAREST)  # pyright: ignore[reportUnknownMemberType]
