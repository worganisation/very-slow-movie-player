# Caption rendering samples

These 800×480 one-bit PNGs come from a deterministic procedural landscape. They
show silence, a short caption, a longer caption, and the optional dark overlay.
Regenerate them with `uv run python -m utilities.caption_samples`.

The default style reserves a 96 px light margin and fits the entire frame into
the remaining 800×384 area. Its text uses a 26 px serif face when it fits and
shrinks down to 16 px for longer captions. Explicit line breaks stay in place,
with a maximum of two lines. Words are never truncated. An overlong caption
raises `CaptionLayoutError` so the caller can display the frame without captions
instead. The panel integration should pass the gamma-corrected mode `L` frame
to `render_caption` before dithering when a caption is available.
