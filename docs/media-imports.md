# Offline media imports

Call `storage.initialize(database)` before `MediaLibrary(database, root).initialize()`.
The library keeps import records in independent SQLite tables, leaving the
playback and control schema version unchanged. Use `list_ready()` for playback;
`list_items()` also shows importing and failed records. A ready item's
`video_path` is a local original media file. Its optional `captions_path` is a
serialized `CaptionTrack` JSON file. `caption_status` and `caption_error` report
missing or failed captions without hiding a playable video.

`ImportService(library, *, jellyfin_url, jellyfin_api_key, jellyfin_user_id,
caption_language="en", asr_backend="disabled")` provides:

- `import_youtube(url)` for a single YouTube video.
- `search_jellyfin(query)` for films and episodes readable by the configured user.
- `jellyfin_versions(item_id)` to inspect media source IDs and audio streams.
- `import_jellyfin(item_id, media_source_id=None, *, audio_language=None)`.

The command line uses the same eagerly validated `SETTINGS`, the shared SQLite
database, and `VSMP_LIBRARY_PATH`. For example:

```sh
python very_slow_movie_player/media_import.py list
python very_slow_movie_player/media_import.py search "The Film"
python very_slow_movie_player/media_import.py versions ITEM_ID
python very_slow_movie_player/media_import.py youtube 'https://www.youtube.com/watch?v=VIDEO_ID'
python very_slow_movie_player/media_import.py jellyfin ITEM_ID --media-source-id SOURCE_ID --audio-language eng
```

Commands print JSON; `list` includes failures and caption status. `search` and
`versions` expose the IDs needed to make a deliberate Jellyfin choice.

An import requires an explicit media source when Jellyfin lists several versions,
and an explicit audio language when a version contains several languages. A
failed import remains listed and may be retried. Import operations take one
advisory lock across processes; a second concurrent import fails with
`BlockingIOError`. Startup recovery leaves an active import alone and makes
interrupted imports retryable. A completed directory rename interrupted before
the database update is recovered on retry.

Downloads go into a private staging directory. `ffprobe` checks for a readable
video stream and positive duration before an atomic rename makes the directory
visible to playback. Jellyfin direct streaming uses the requested media source,
and the original media is saved without transcoding. YouTube chooses a single
progressive format and does not recode it. Source, version, audio selection and
caption language are part of the stable import identity.

Caption selection prefers a complete human text track in the chosen language,
then YouTube automatic captions, then configured ASR. Forced only and bitmap
subtitle streams are skipped. Embedded subtitle PTS are mapped to the first
video frame; an extracted audio track's start offset is applied to ASR cues.
With `asr_backend="disabled"`, a video can still be ready with
`caption_status="failed"` and a reason in `caption_error`. Local ASR requires
the optional `faster-whisper` runtime and model files available on the importing
machine. Imports save files locally, so playback no longer depends on the source
server or network.

This import layer does not fetch a user's entire Jellyfin library or a YouTube
playlist. It imports one selected title at a time. Source access and subtitles
depend on the Jellyfin user's permissions and the source's available tracks.
