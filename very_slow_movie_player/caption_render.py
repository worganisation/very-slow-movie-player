"""Render optional captions on native-resolution monochrome movie frames."""

from __future__ import annotations

from typing import Literal

from PIL import Image, ImageDraw, ImageFont

_SIZE = (800, 480)
_MARGIN_HEIGHT = 96
_MIN_FONT_SIZE = 16
_MAX_LINES = 2
_FONT_CANDIDATES = {
    "serif": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "DejaVuSerif.ttf",
    ),
    "sans": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "DejaVuSans.ttf",
    ),
}


class CaptionLayoutError(ValueError):
    """The full caption cannot fit in the available caption area."""


def _load_font(family: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in _FONT_CANDIDATES[family]:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    raise RuntimeError(f"No {family} TrueType font is installed for caption rendering")


def _wrap_words(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str] | None:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if font.getlength(word) > max_width:
            return None
        proposed = f"{current} {word}" if current else word
        if font.getlength(proposed) <= max_width:
            current = proposed
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _wrap_preserving_breaks(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str] | None:
    """Wrap within cue lines while retaining explicit line boundaries."""
    lines: list[str] = []
    for cue_line in text.splitlines():
        normalized = " ".join(cue_line.split())
        if not normalized:
            continue
        wrapped = _wrap_words(normalized, font, max_width)
        if wrapped is None:
            return None
        lines.extend(wrapped)
    return lines


def _layout(
    text: str,
    family: str,
    requested_size: int,
    width: int,
    height: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    for size in range(requested_size, _MIN_FONT_SIZE - 1, -1):
        font = _load_font(family, size)
        lines = _wrap_preserving_breaks(text, font, width)
        if lines is None:
            continue
        line_height = round(size * 1.2)
        if len(lines) <= _MAX_LINES and len(lines) * line_height <= height:
            return font, lines, line_height
    raise CaptionLayoutError(
        "Caption does not fit in two lines at 16 px; shorten it or use another layout"
    )


def render_caption(
    image: Image.Image,
    text: str,
    *,
    style: Literal["margin", "overlay"] = "margin",
    background: Literal["light", "dark"] = "light",
    font: Literal["serif", "sans"] = "serif",
    font_size: int = 26,
) -> Image.Image:
    """Return an 800x480 one-bit frame with the full caption visible.

    Blank captions leave a copy of the frame unchanged. Margin captions shrink
    the entire frame to fit above a band; overlay captions retain the
    original framing and draw a caption band across its lower edge.
    Oversized captions produce ``CaptionLayoutError``.

    Args:
        image: Native-resolution mode ``L`` gamma-corrected or mode ``1`` frame.
        text: Caption text; explicit line breaks are preserved.
        style: ``margin`` or ``overlay``.
        background: ``light`` or ``dark`` band with contrasting text.
        font: ``serif`` or ``sans`` system TrueType font.
        font_size: Preferred caption size in pixels.

    Raises:
        ValueError: Input or options are invalid.
    """
    if image.size != _SIZE or image.mode not in {"1", "L"}:
        raise ValueError("Caption input must be an 800x480 mode 1 or L image")
    if style not in {"margin", "overlay"}:
        raise ValueError(f"Unsupported caption style: {style}")
    if background not in {"light", "dark"}:
        raise ValueError(f"Unsupported caption background: {background}")
    if font not in _FONT_CANDIDATES:
        raise ValueError(f"Unsupported caption font: {font}")
    if font_size < _MIN_FONT_SIZE:
        raise ValueError("Caption font_size must be at least 16 px")
    if not text.strip():
        return image.copy()

    caption_font, lines, line_height = _layout(
        text, font, font_size, _SIZE[0] - 48, _MARGIN_HEIGHT - 16
    )
    if style == "margin":
        result = Image.new("1", _SIZE, color=1)
        photo_height = _SIZE[1] - _MARGIN_HEIGHT
        photo_width = round(image.width * photo_height / image.height)
        with (
            image.convert("L") as grayscale,
            grayscale.resize(  # pyright: ignore[reportUnknownMemberType]
                (photo_width, photo_height), Image.Resampling.LANCZOS
            ) as scaled,
            scaled.convert("1", dither=Image.Dither.FLOYDSTEINBERG) as photo,
        ):
            result.paste(photo, ((_SIZE[0] - photo_width) // 2, 0))
    else:
        result = image.convert("1", dither=Image.Dither.FLOYDSTEINBERG)

    draw = ImageDraw.Draw(result)
    paper = 1 if background == "light" else 0
    ink = 1 - paper
    draw.rectangle((0, _SIZE[1] - _MARGIN_HEIGHT, _SIZE[0], _SIZE[1]), fill=paper)
    block_top = (
        _SIZE[1] - _MARGIN_HEIGHT + (_MARGIN_HEIGHT - len(lines) * line_height) // 2
    )
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=caption_font)
        x = (_SIZE[0] - (bbox[2] - bbox[0])) // 2 - bbox[0]
        y = block_top + index * line_height - bbox[1]
        draw.text((x, y), line, font=caption_font, fill=ink)
    return result
