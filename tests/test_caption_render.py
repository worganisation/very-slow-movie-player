"""Behavioral tests for caption rendering."""

# ruff: noqa: PT009, PT027

from __future__ import annotations

import unittest

from PIL import Image, ImageChops

from very_slow_movie_player.caption_render import CaptionLayoutError, render_caption


class CaptionRenderTests(unittest.TestCase):
    """Check native output, fitting, and failure behavior."""

    def setUp(self) -> None:
        """Create an asymmetric test frame with visible edge content."""
        self.frame = Image.new("1", (800, 480), color=1)
        for y in range(480):
            self.frame.putpixel((0, y), 0)
            self.frame.putpixel((799, y), 0)

    def test_silence_does_not_change_frame(self) -> None:
        """Blank text must not change pixels or return the mutable input."""
        result = render_caption(self.frame, " \n ")
        self.assertIsNot(result, self.frame)
        self.assertEqual(result.tobytes(), self.frame.tobytes())

    def test_grayscale_silence_keeps_grayscale(self) -> None:
        """Silence does not dither a gamma-corrected source."""
        grayscale = Image.new("L", (800, 480), color=128)
        result = render_caption(grayscale, "")
        self.assertEqual(result.mode, "L")
        self.assertEqual(result.tobytes(), grayscale.tobytes())

    def test_margin_keeps_full_image_and_draws_caption(self) -> None:
        """The complete input fits above the white caption margin."""
        result = render_caption(self.frame, "The whole world seemed to stand still.")
        self.assertEqual(result.mode, "1")
        self.assertEqual(result.size, (800, 480))
        self.assertEqual(result.getpixel((80, 0)), 0)
        self.assertEqual(result.getpixel((719, 383)), 0)
        self.assertEqual(result.getpixel((400, 400)), 1)
        self.assertIn(0, result.crop((150, 385, 650, 480)).get_flattened_data())

    def test_long_caption_fits_without_truncation(self) -> None:
        """A longer line wraps and remains in the caption area."""
        text = "Every person in the room had waited for this one extraordinary moment."
        result = render_caption(self.frame, text, font="sans")
        self.assertIn(0, result.crop((20, 384, 780, 480)).get_flattened_data())

    def test_explicit_newline_keeps_two_cues_separate(self) -> None:
        """Concurrent speaker cues occupy separate caption rows."""
        result = render_caption(self.frame, "First speaker.\nSecond speaker.")
        self.assertIn(0, result.crop((20, 390, 780, 430)).get_flattened_data())
        self.assertIn(0, result.crop((20, 430, 780, 475)).get_flattened_data())

    def test_grayscale_resizes_before_dithering(self) -> None:
        """The photo portion follows grayscale resize then one dither."""
        grayscale = Image.new("L", (800, 480), color=128)
        result = render_caption(grayscale, "A caption.")
        expected = grayscale.resize((640, 384), Image.Resampling.LANCZOS).convert(
            "1", dither=Image.Dither.FLOYDSTEINBERG
        )
        self.assertEqual(result.mode, "1")
        self.assertEqual(result.crop((80, 0, 720, 384)).tobytes(), expected.tobytes())

    def test_overlay_preserves_photo_above_band(self) -> None:
        """Overlay style only changes the lower band."""
        result = render_caption(self.frame, "A short caption.", style="overlay")
        self.assertEqual(
            result.crop((0, 0, 800, 384)).tobytes(),
            self.frame.crop((0, 0, 800, 384)).tobytes(),
        )
        self.assertIn(1, result.crop((20, 384, 780, 480)).get_flattened_data())

    def test_impossible_caption_raises(self) -> None:
        """The renderer must never silently cut off words."""
        with self.assertRaisesRegex(CaptionLayoutError, "does not fit"):
            render_caption(self.frame, "W" * 100)

    def test_background_colors_margins_and_band_without_changing_photo(self) -> None:
        """Background colors surround the preserved photo and contrast with glyphs."""
        for style in ("margin", "overlay"):
            light = render_caption(self.frame, "Hello!", style=style, background="light")
            dark = render_caption(self.frame, "Hello!", style=style, background="dark")
            photo_box = (80, 0, 720, 384) if style == "margin" else (0, 0, 800, 384)
            self.assertEqual(
                light.crop(photo_box).tobytes(), dark.crop(photo_box).tobytes()
            )
            if style == "margin":
                for margin in ((0, 0, 80, 384), (720, 0, 800, 384)):
                    self.assertEqual(light.crop(margin).getextrema(), (1, 1))
                    self.assertEqual(dark.crop(margin).getextrema(), (0, 0))
            band = dark.crop((0, 384, 800, 480))
            self.assertEqual(band.getpixel((0, 0)), 0)
            self.assertIn(1, band.get_flattened_data())
            self.assertEqual(
                ImageChops.invert(light.crop((0, 384, 800, 480)).convert("L")).tobytes(),
                band.convert("L").tobytes(),
            )
            self.assertEqual(
                render_caption(self.frame, "", style=style, background="dark").tobytes(),
                self.frame.tobytes(),
            )

    def test_more_than_two_cue_lines_raises(self) -> None:
        """Extra speaker lines are not silently omitted."""
        with self.assertRaises(CaptionLayoutError):
            render_caption(self.frame, "One.\nTwo.\nThree.")

    def test_invalid_input_raises(self) -> None:
        """Prevent accidentally bypassing the native panel format."""
        image = Image.new("RGB", (800, 480))
        with self.assertRaisesRegex(ValueError, "800x480 mode 1 or L"):
            render_caption(image, "Hello")


if __name__ == "__main__":
    unittest.main()
