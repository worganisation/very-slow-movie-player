"""Regression coverage for the selectable panel rendering methods."""

# ruff: noqa: PT009, PT027

from __future__ import annotations

import unittest

from dithering import DitheringMethod, dither_image
from PIL import Image


class DitheringTests(unittest.TestCase):
    """Preserve tones and dimensions while exposing deliberate pixel tradeoffs."""

    def test_original_is_pixel_identical_and_input_is_unchanged(self) -> None:
        """The default retains the existing Pillow error-diffusion output."""
        source = Image.linear_gradient("L").resize((80, 48))
        before = source.tobytes()
        expected = source.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
        self.assertEqual(dither_image(source).tobytes(), expected.tobytes())
        self.assertEqual(source.tobytes(), before)

    def test_all_modes_preserve_size_and_solid_endpoints(self) -> None:
        """Even tiny or odd-sized pictures stay valid monochrome frames."""
        for method in DitheringMethod:
            for size in ((800, 480), (17, 13), (1, 1)):
                for tone in (0, 255):
                    with self.subTest(method=method, size=size, tone=tone):
                        result = dither_image(Image.new("L", size, tone), method)
                        self.assertEqual(result.mode, "1")
                        self.assertEqual(result.size, size)
                        self.assertEqual(result.convert("L").getextrema(), (tone, tone))

    def test_ordered_tone_coverage_is_monotonic(self) -> None:
        """Every Bayer cell rank switches exactly once as brightness increases."""
        counts = [
            sum(
                dither_image(Image.new("L", (8, 8), tone), DitheringMethod.ORDERED)
                .convert("L")
                .tobytes()
            )
            // 255
            for tone in range(256)
        ]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(set(counts), set(range(65)))

    def test_paired_modes_use_expected_pixel_dimensions(self) -> None:
        """Rows or columns pair only where the selected method promises it."""
        source = Image.new("L", (32, 32), 96)
        for method, width, height in (
            (DitheringMethod.ORDERED_VERTICAL, 1, 2),
            (DitheringMethod.FLOYD_SQUARE, 2, 2),
            (DitheringMethod.FLOYD_HORIZONTAL, 2, 1),
        ):
            result = dither_image(source, method)
            for y in range(0, 32, height):
                for x in range(0, 32, width):
                    block = result.crop((x, y, x + width, y + height))
                    low, high = block.getextrema()
                    self.assertEqual(low, high)
            self.assertEqual(result.convert("L").getextrema(), (0, 255))

    def test_invalid_method_fails(self) -> None:
        """Do not silently render a misspelled mode as a different method."""
        with self.assertRaises(ValueError):
            dither_image(Image.new("L", (8, 8)), "unknown")  # type: ignore[arg-type]
