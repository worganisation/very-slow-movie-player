"""Generate offline 800x480 caption examples for visual review."""  # noqa: INP001

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from very_slow_movie_player.caption_render import render_caption

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "caption-samples"


def sample_frame() -> Image.Image:
    """Make a deterministic grayscale landscape without external media."""
    grayscale = Image.new("L", (800, 480), color=255)
    draw = ImageDraw.Draw(grayscale)
    for y in range(480):
        shade = 205 - round(y * 0.2)
        draw.line((0, y, 799, y), fill=shade)
    draw.ellipse((505, 30, 700, 225), fill=241)
    draw.polygon(
        [
            (0, 305),
            (150, 160),
            (310, 320),
            (465, 185),
            (690, 350),
            (800, 230),
            (800, 480),
            (0, 480),
        ],
        fill=75,
    )
    draw.polygon(
        [(0, 400), (210, 270), (400, 385), (600, 300), (800, 410), (800, 480), (0, 480)],
        fill=35,
    )
    return grayscale


def main() -> None:
    """Save short, long, silent, and alternate-style native PNGs."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    frame = sample_frame()
    cases = {
        "silence.png": frame.convert("1", dither=Image.Dither.FLOYDSTEINBERG),
        "short-margin.png": render_caption(
            frame, "For a moment, the whole world seemed to stand still."
        ),
        "long-margin.png": render_caption(
            frame,
            "Every person in the room had waited for this one extraordinary moment, and now it was finally here.",
        ),
        "short-overlay.png": render_caption(
            frame,
            "For a moment, the whole world seemed to stand still.",
            style="overlay",
            font="sans",
        ),
    }
    for name, image in cases.items():
        image.save(OUTPUT / name)


if __name__ == "__main__":
    main()
