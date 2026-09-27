# Library playback and Home Assistant controls

Import media using the CLI described in [media imports](media-imports.md), or the
MQTT-discovered **Import YouTube URL** / **Import Jellyfin item ID** text controls.
The Jellyfin field accepts `item_id`, `item_id/media_source_id`, or `item_id/media_source_id/audio_language`
to select a version and an audio language. Multilingual files require an explicit
audio language.
Imports run in a cancellable child process; only one HA request runs at a time. Retained
commands are ignored, and import requests are never replayed on restart.
Import fields allow a blank state and announce it on startup, MQTT reconnect,
and Home Assistant birth. Accepted requests clear the field; blank submissions
are still rejected by the importer rather than starting a download.
Stopping VSMP terminates the import process group with a bounded wait; interrupted
imports remain retryable. Accepted URLs are cleared from retained MQTT text state.

Choose a completed title in **Library video**, then set **Source** to `library`.
Library choices show titles plus a stable identity suffix, so duplicate titles stay
distinct. Only files that are ready and present locally are selectable. Playback,
frame progress, and caption selection subsequently work without either source
server. The selected ID and caption controls persist in the existing SQLite state.
CLI imports appear in HA on the next playback control poll (within 60 seconds).

**Captions enabled**, **Caption style**, **Caption background**, **Caption font**, **Caption font size**,
and **Caption timing offset** are confirmed-state controls. Positive offsets delay
captions. The cue is selected at the extracted frame's media timestamp, not the
wall clock; silence remains blank. Changes apply on the next refresh; use Redisplay
current frame to preview them while preserving the minimum panel refresh interval.
The retained MQTT image is the exact bitmap sent successfully to the panel.
**Current caption** contains the verbatim displayed text, including line breaks,
and is blank for silence, disabled captions, or a caption rendering failure.
**Video timestamp** reports the displayed frame's media position as `HH:MM:SS`
and is blank for photos. Both update only after a successful panel refresh.

The initial style is 26px serif text in a 96px light margin. The full photo fits
above the margin without cropping. Background is independently selectable for
both margin and overlay: `light` uses dark text, `dark` uses light text. Photo
gamma is applied before scaling/dithering and caption glyphs stay crisp. Text wraps
to at most two lines, shrinking only to 16px. An oversized caption or missing system
font leaves the photo visible and reports **Caption error**, rather than truncating
words or preventing video playback. Install `fonts-dejavu-core` on Raspberry Pi OS.
See [native monochrome samples](caption-samples/README.md).

New environment defaults are `VSMP_LIBRARY_PATH` (absolute; default
`~/vsmp-library`), `VSMP_CAPTION_LANGUAGE=en`, `VSMP_ASR_BACKEND=disabled`,
`VSMP_CAPTIONS_ENABLED=true`, `VSMP_CAPTION_STYLE=margin`,
`VSMP_CAPTION_BACKGROUND=light`,
`VSMP_CAPTION_FONT=serif`, `VSMP_CAPTION_FONT_SIZE=26`, and
`VSMP_CAPTION_OFFSET=0`. Jellyfin URL, API key and user ID must be configured together.
All configured values are validated at startup, including in other source modes.

ASR is an optional locked dependency; install it with `uv sync --extra asr`. The lab GPU worker currently
has no transcription API; this version does not assume one exists. Enabling `local`
requires faster-whisper and its model dependencies on the importing machine.
Use `local:small` (the default model) or another supported model name. It can
be expensive on a Pi. Importing without usable captions still produces offline video
and reports the caption failure separately. No model runs during frame refreshes.
