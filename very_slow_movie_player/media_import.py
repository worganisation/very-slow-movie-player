"""Import offline videos and timed captions from YouTube or Jellyfin."""

# ruff: noqa: S404, S603, TRY301, PLR0914 - fixed commands and import cleanup.

from __future__ import annotations

import json
import subprocess
import sys
from argparse import ArgumentParser
from dataclasses import asdict
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import httpx2
from captions import CaptionCue, CaptionTrack, normalize_subtitles, transcribe_media

if TYPE_CHECKING:
    from pathlib import Path

    from library import LibraryItem, MediaLibrary

_TEXT_CODECS = {"ass", "ssa", "srt", "subrip", "vtt", "webvtt"}


class ImportService:
    """Import original selected media and independent offline caption tracks."""

    def __init__(
        self,
        library: MediaLibrary,
        *,
        jellyfin_url: str | None = None,
        jellyfin_api_key: str | None = None,
        jellyfin_user_id: str | None = None,
        caption_language: str = "en",
        asr_backend: str = "disabled",
        asr_endpoint: str | None = None,
    ) -> None:
        self.library: MediaLibrary = library
        self.jellyfin_url: str | None = jellyfin_url.rstrip("/") if jellyfin_url else None
        self.jellyfin_api_key: str | None = jellyfin_api_key
        self.jellyfin_user_id: str | None = jellyfin_user_id
        self.caption_language: str = caption_language.casefold()
        self.asr_backend: str = asr_backend
        self.asr_endpoint: str | None = asr_endpoint

    def import_youtube(self, url: str) -> LibraryItem:
        """Import one video from a YouTube URL, preferring human captions."""
        with self.library.import_lock():
            return self._import_youtube_locked(url)

    def _import_youtube_locked(self, url: str) -> LibraryItem:
        host = (urlsplit(url).hostname or "").casefold()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
            raise ValueError("A YouTube video URL is required")
        info = self._yt_json(url)
        video_id = str(info.get("id") or "")
        if not video_id or info.get("_type") in {"playlist", "multi_video"}:
            raise ValueError("URL must identify one video")
        title = str(info.get("title") or video_id)
        # A progressive source retains its source timestamps; no video conversion occurs.
        version = json.dumps(
            [
                "progressive-original",
                info.get("format_id"),
                info.get("duration"),
                info.get("upload_date"),
                self.caption_language,
                self.asr_backend,
            ],
            separators=(",", ":"),
        )
        item = self.library.begin("youtube", video_id, version, title)
        if item.status == "ready":
            return item
        stage = self.library.staging_dir(item.id)
        try:
            self._yt_download(url, stage)
            videos = [
                path
                for path in stage.iterdir()
                if path.is_file()
                and path.name.startswith("video.")
                and ".part" not in path.name
            ]
            if len(videos) != 1:
                raise ValueError("YouTube download did not produce one video")
            video = videos[0]
            self.library.set_progress(item.id, 0.85)
            captions, caption_error = self._youtube_captions(url, info, stage, video)
            return self.library.promote(item.id, video, captions, caption_error)
        except Exception as exc:
            self.library.fail(item.id, self._safe_error(exc))
            raise

    def search_jellyfin(self, query: str) -> list[dict[str, object]]:
        """Search readable films and episodes for the configured Jellyfin user."""
        if not query.strip():
            return []
        result = self._jf_get(
            "/Items",
            params={
                "userId": self._jf_user(),
                "searchTerm": query,
                "includeItemTypes": "Movie,Episode",
                "recursive": "true",
                "fields": "MediaSources",
                "limit": "50",
            },
        )
        items = result.get("Items", [])
        if not isinstance(items, list):
            raise TypeError("Invalid Jellyfin search response")
        entries = [
            cast("dict[str, object]", entry)
            for entry in cast("list[object]", items)
            if isinstance(entry, dict)
        ]
        return [
            entry
            for entry in entries
            if entry.get("Type") in {"Movie", "Episode"}
            and entry.get("LocationType") != "Virtual"
        ]

    def jellyfin_versions(self, item_id: str) -> list[dict[str, object]]:
        """List media versions and audio tracks before explicit selection."""
        item = self._jf_item(item_id)
        sources = item.get("MediaSources", [])
        if not isinstance(sources, list):
            raise TypeError("Jellyfin item has invalid media sources")
        entries = [
            cast("dict[str, object]", source)
            for source in cast("list[object]", sources)
            if isinstance(source, dict)
        ]
        return [source for source in entries if source.get("Id")]

    def import_jellyfin(
        self,
        item_id: str,
        media_source_id: str | None = None,
        *,
        audio_language: str | None = None,
    ) -> LibraryItem:
        """Import a selected Jellyfin media version with a selected audio language."""
        with self.library.import_lock():
            return self._import_jellyfin_locked(item_id, media_source_id, audio_language)

    def _import_jellyfin_locked(
        self,
        item_id: str,
        media_source_id: str | None,
        audio_language: str | None,
    ) -> LibraryItem:
        metadata = self._jf_item(item_id)
        if metadata.get("Type") not in {"Movie", "Episode"}:
            raise ValueError("Only Jellyfin films and episodes can be imported")
        sources = self.jellyfin_versions(item_id)
        if media_source_id is None and len(sources) != 1:
            raise ValueError("Specify media_source_id for an item with multiple versions")
        source = (
            next((entry for entry in sources if entry.get("Id") == media_source_id), None)
            if media_source_id
            else sources[0]
            if sources
            else None
        )
        if source is None:
            raise ValueError("Jellyfin media source was not found")
        source_id = str(source["Id"])
        streams = source.get("MediaStreams", [])
        if not isinstance(streams, list):
            raise TypeError("Invalid Jellyfin media streams")
        typed_streams = cast("list[object]", streams)
        dictionaries = [
            cast("dict[str, object]", stream)
            for stream in typed_streams
            if isinstance(stream, dict)
        ]
        audio = [stream for stream in dictionaries if stream.get("Type") == "Audio"]
        selected_audio = self._choose_audio(audio, audio_language)
        version = json.dumps(
            [
                self.jellyfin_url,
                source_id,
                source.get("ETag"),
                source.get("Size"),
                source.get("RunTimeTicks"),
                selected_audio.get("Index") if selected_audio else None,
                self.caption_language,
                self.asr_backend,
            ],
            separators=(",", ":"),
        )
        title = self._jellyfin_title(metadata, item_id)
        item = self.library.begin("jellyfin", item_id, version, title)
        if item.status == "ready":
            return item
        stage = self.library.staging_dir(item.id)
        try:
            container = str(source.get("Container") or "mkv").split(",")[0]
            extension = container if container.isalnum() else "mkv"
            video = stage / f"video.{extension}"
            self._jf_download(item_id, source_id, video)
            self.library.set_progress(item.id, 0.85)
            captions, caption_error = self._jellyfin_captions(
                item_id, source_id, typed_streams, video, stage, selected_audio
            )
            return self.library.promote(item.id, video, captions, caption_error)
        except Exception as exc:
            self.library.fail(item.id, self._safe_error(exc))
            raise

    @staticmethod
    def _choose_audio(
        streams: list[dict[str, object]],
        language: str | None,
    ) -> dict[str, object] | None:
        if not streams:
            return None
        languages = {str(s.get("Language") or "und").casefold() for s in streams}
        if language is None and len(languages) > 1:
            raise ValueError("Specify audio_language for a multilingual media version")
        selected = language.casefold() if language else next(iter(languages))
        matches = [
            stream
            for stream in streams
            if str(stream.get("Language") or "und").casefold() == selected
        ]
        if not matches:
            raise ValueError(f"Audio language {selected} is unavailable")
        return next((stream for stream in matches if stream.get("IsDefault")), matches[0])

    @staticmethod
    def _jellyfin_title(metadata: dict[str, object], item_id: str) -> str:
        """Give episodes enough context to distinguish them in the picker."""
        name = str(metadata.get("Name") or item_id)
        if metadata.get("Type") == "Episode":
            series = str(metadata.get("SeriesName") or "Series")
            season = metadata.get("ParentIndexNumber")
            episode = metadata.get("IndexNumber")
            if isinstance(season, int) and isinstance(episode, int):
                return f"{series} S{season:02d}E{episode:02d} — {name}"
            return f"{series} — {name}"
        year = metadata.get("ProductionYear")
        return f"{name} ({year})" if isinstance(year, int) else name

    def _jellyfin_captions(
        self,
        item_id: str,
        source_id: str,
        streams: list[object],
        video: Path,
        stage: Path,
        audio: dict[str, object] | None,
    ) -> tuple[Path | None, str | None]:
        dictionaries = [
            cast("dict[str, object]", s) for s in streams if isinstance(s, dict)
        ]
        candidates = [
            s
            for s in dictionaries
            if s.get("Type") == "Subtitle"
            and not s.get("IsForced")
            and str(s.get("Codec") or "").casefold() in _TEXT_CODECS
            and self._language_matches(str(s.get("Language") or ""))
        ]
        candidates.sort(
            key=lambda s: (
                str(s.get("Language") or "").casefold() != self.caption_language,
                not bool(s.get("IsDefault")),
                bool(s.get("IsHearingImpaired")),
            )
        )
        errors: list[str] = []
        for candidate in candidates:
            codec = str(candidate.get("Codec") or "").casefold()
            try:
                if candidate.get("DeliveryUrl"):
                    text = self._jf_text(str(candidate["DeliveryUrl"]))
                    fmt = (
                        "srt"
                        if codec == "subrip" or "srt" in str(candidate["DeliveryUrl"])
                        else codec
                    )
                elif candidate.get("IsExternal"):
                    index = int(str(candidate["Index"]))
                    text = self._jf_text(
                        f"/Videos/{item_id}/{source_id}/Subtitles/{index}/0/Stream.srt"
                    )
                    fmt = "srt"
                else:
                    index = int(str(candidate["Index"]))
                    result = subprocess.run(
                        [
                            "/usr/bin/env",
                            "ffmpeg",
                            "-v",
                            "error",
                            "-copyts",
                            "-i",
                            str(video),
                            "-map",
                            f"0:{index}",
                            "-f",
                            "srt",
                            "-",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=180,
                    )
                    text, fmt = result.stdout, "srt"
                cues = normalize_subtitles(text, fmt)
                if not candidate.get("DeliveryUrl") and not candidate.get("IsExternal"):
                    cues = self._relative_cues(cues, self._video_origin(video))
                if cues:
                    return self._save_track(
                        stage,
                        CaptionTrack(
                            cues=cues,
                            language=self.caption_language,
                            source="jellyfin-human",
                        ),
                    ), None
            except (
                ValueError,
                KeyError,
                subprocess.SubprocessError,
                httpx2.HTTPError,
            ) as exc:
                errors.append(self._safe_error(exc))
        return self._asr_or_missing(video, stage, audio, errors)

    def _youtube_captions(
        self,
        url: str,
        info: dict[str, object],
        stage: Path,
        video: Path,
    ) -> tuple[Path | None, str | None]:
        errors: list[str] = []
        for field, source in (
            ("subtitles", "youtube-human"),
            ("automatic_captions", "youtube-auto"),
        ):
            tracks = info.get(field)
            if not isinstance(tracks, dict):
                continue
            languages = [
                str(key)
                for key in cast("dict[object, object]", tracks)
                if self._language_matches(str(key))
                and "forced" not in str(key).casefold()
            ]
            languages.sort(key=lambda value: value.casefold() != self.caption_language)
            language = languages[0] if languages else None
            if language is None:
                continue
            entries = cast("dict[str, object]", tracks).get(language)
            if not isinstance(entries, list) or not entries:
                continue
            try:
                attempt = stage / field
                attempt.mkdir(exist_ok=True)
                command = [
                    sys.executable,
                    "-m",
                    "yt_dlp",
                    "--no-config",
                    "--no-playlist",
                    "--skip-download",
                    "--sub-langs",
                    language,
                    "--sub-format",
                    "vtt/srt",
                    "--output",
                    str(attempt / "subtitle.%(ext)s"),
                    "--write-subs" if field == "subtitles" else "--write-auto-subs",
                    url,
                ]
                _ = subprocess.run(
                    command, capture_output=True, text=True, check=True, timeout=300
                )
                for path in attempt.glob("subtitle.*"):
                    if path.suffix.casefold() not in {".vtt", ".srt"}:
                        continue
                    cues = normalize_subtitles(
                        path.read_text(encoding="utf-8"),
                        path.suffix.lstrip("."),
                        rolling=source == "youtube-auto",
                    )
                    if cues:
                        return self._save_track(
                            stage,
                            CaptionTrack(
                                cues=cues, language=self.caption_language, source=source
                            ),
                        ), None
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                errors.append(self._safe_error(exc))
        return self._asr_or_missing(
            video,
            stage,
            None,
            errors,
            spoken_language=str(info.get("language") or ""),
        )

    def _asr_or_missing(
        self,
        video: Path,
        stage: Path,
        audio: dict[str, object] | None,
        errors: list[str],
        *,
        spoken_language: str = "",
    ) -> tuple[Path | None, str | None]:
        if self.asr_backend == "disabled":
            return None, "; ".join(
                errors
            ) if errors else "No complete text captions available"
        language = str(audio.get("Language") or "") if audio else spoken_language
        if (
            language
            and language.casefold() != "und"
            and not self._language_matches(language)
        ):
            message = f"Audio language {language} does not match requested {self.caption_language};"
            errors.append(message + " ASR cannot translate")
            return None, "; ".join(errors)
        try:
            # The original container is passed to ASR, preserving source media timestamps.
            # An explicit audio stream is selected only if a temporary extraction is needed.
            media = video
            if audio is not None and isinstance(audio.get("Index"), int):
                media = stage / "selected-audio.wav"
                _ = subprocess.run(
                    [
                        "/usr/bin/env",
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(video),
                        "-map",
                        f"0:{audio['Index']}",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        str(media),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=3600,
                )
            track = transcribe_media(
                media,
                stage / "asr-cache.json",
                backend=self.asr_backend,
                language=self.caption_language,
                endpoint=self.asr_endpoint,
            )
            if not track.cues:
                raise ValueError("Speech recognition produced no captions")
            if audio is not None and isinstance(audio.get("Index"), int):
                offset = self._stream_origin(
                    video, int(str(audio["Index"]))
                ) - self._video_origin(video)
                track = track.model_copy(
                    update={"cues": self._relative_cues(track.cues, -offset)}
                )
                media.unlink(missing_ok=True)
            return self._save_track(stage, track), None
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(self._safe_error(exc))
            return None, "; ".join(errors)

    @staticmethod
    def _save_track(stage: Path, track: CaptionTrack) -> Path:
        target = stage / "captions.json"
        _ = target.write_text(track.model_dump_json(), encoding="utf-8")
        return target

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """Avoid recording URLs, tokens, and command arguments in the catalogue."""
        if isinstance(exc, (httpx2.HTTPError, subprocess.SubprocessError)):
            return f"{type(exc).__name__}: source request or media command failed"
        return str(exc)

    def _language_matches(self, language: str) -> bool:
        aliases = {
            "en": "eng",
            "eng": "en",
            "fr": "fra",
            "fra": "fr",
            "de": "deu",
            "deu": "de",
            "es": "spa",
            "spa": "es",
        }
        wanted = self.caption_language.split("-")[0]
        actual = language.casefold().split("-")[0]
        return actual in {wanted, aliases.get(wanted, wanted)}

    @staticmethod
    def _video_origin(video: Path) -> float:
        """Read the first video stream PTS in seconds."""
        result = subprocess.run(
            [
                "/usr/bin/env",
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=start_time",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        data = cast("dict[str, object]", json.loads(result.stdout))
        streams = data.get("streams")
        if not isinstance(streams, list) or not streams:
            return 0.0
        first = cast("list[object]", streams)[0]
        return float(str(cast("dict[str, object]", first).get("start_time") or 0))

    @staticmethod
    def _stream_origin(video: Path, index: int) -> float:
        """Read an audio stream's absolute presentation origin."""
        result = subprocess.run(
            [
                "/usr/bin/env",
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=index,start_time",
                "-of",
                "json",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        data = cast("dict[str, object]", json.loads(result.stdout))
        raw = data.get("streams")
        streams = [
            cast("dict[str, object]", entry)
            for entry in cast("list[object]", raw or [])
            if isinstance(entry, dict)
        ]
        match = next((s for s in streams if s.get("index") == index), None)
        return float(str(match.get("start_time") or 0)) if match else 0.0

    @staticmethod
    def _relative_cues(cues: list[CaptionCue], origin: float) -> list[CaptionCue]:
        """Map absolute container timestamps to playback time from frame zero."""
        result: list[CaptionCue] = []
        for cue in cues:
            start, end = max(0.0, cue.start - origin), cue.end - origin
            if end > start:
                result.append(cue.model_copy(update={"start": start, "end": end}))
        return result

    @staticmethod
    def _yt_json(url: str) -> dict[str, object]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--no-config",
                "--no-playlist",
                "--format",
                "best[ext=mp4]/best",
                "--dump-single-json",
                url,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        data = cast("object", json.loads(result.stdout))
        if not isinstance(data, dict):
            raise TypeError("Invalid YouTube metadata")
        return cast("dict[str, object]", data)

    @staticmethod
    def _yt_download(url: str, stage: Path) -> None:
        _ = subprocess.run(
            [
                sys.executable,
                "-m",
                "yt_dlp",
                "--no-config",
                "--no-playlist",
                "--format",
                "best[ext=mp4]/best",
                "--output",
                str(stage / "video.%(ext)s"),
                url,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=7200,
        )

    def _jf_user(self) -> str:
        if (
            not self.jellyfin_url
            or not self.jellyfin_api_key
            or not self.jellyfin_user_id
        ):
            raise ValueError("Jellyfin URL, API key, and user ID are required")
        return self.jellyfin_user_id

    def _jf_client(self) -> httpx2.Client:
        _ = self._jf_user()
        return httpx2.Client(
            headers={"X-Emby-Token": self.jellyfin_api_key or ""},
            timeout=httpx2.Timeout(60.0, read=300.0),
            follow_redirects=False,
        )

    def _jf_get(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> dict[str, object]:
        with self._jf_client() as client:
            response = client.get(f"{self.jellyfin_url}{path}", params=params)
            _ = response.raise_for_status()
            data = cast("object", response.json())
            if not isinstance(data, dict):
                raise TypeError("Invalid Jellyfin response")
            return cast("dict[str, object]", data)

    def _jf_item(self, item_id: str) -> dict[str, object]:
        if not item_id or not all(ch.isalnum() or ch == "-" for ch in item_id):
            raise ValueError("Invalid Jellyfin item ID")
        return self._jf_get(f"/Users/{self._jf_user()}/Items/{item_id}")

    def _jf_download(self, item_id: str, source_id: str, destination: Path) -> None:
        with (
            self._jf_client() as client,
            client.stream(
                "GET",
                f"{self.jellyfin_url}/Videos/{item_id}/stream",
                params={"static": "true", "mediaSourceId": source_id},
            ) as response,
        ):
            _ = response.raise_for_status()
            if "text/html" in response.headers.get("content-type", ""):
                raise ValueError("Jellyfin returned an HTML page instead of media")
            with destination.open("wb") as target:
                size = 0
                for chunk in response.iter_bytes():
                    _ = target.write(chunk)
                    size += len(chunk)
            expected = response.headers.get("content-length")
            if expected is not None and size != int(expected):
                raise ValueError("Jellyfin download ended before its declared size")

    def _jf_text(self, delivery_url: str) -> str:
        parsed = urlsplit(delivery_url)
        base_path = urlsplit(self.jellyfin_url or "").path.rstrip("/")
        path = parsed.path
        if base_path and path.startswith(base_path + "/"):
            path = path[len(base_path) :]
        if parsed.scheme or parsed.netloc or not path.startswith("/Videos/"):
            raise ValueError("Invalid Jellyfin subtitle delivery URL")
        with self._jf_client() as client:
            response = client.get(f"{self.jellyfin_url}{path}")
            _ = response.raise_for_status()
            return response.text


def main(argv: list[str] | None = None) -> int:
    """Run one explicit import or catalogue query from the command line."""
    parser = ArgumentParser(description="Manage offline VSMP media imports")
    commands = parser.add_subparsers(dest="command", required=True)
    _ = commands.add_parser("list", help="List local media and import status")
    search = commands.add_parser("search", help="Search Jellyfin films and episodes")
    _ = search.add_argument("query")
    versions = commands.add_parser(
        "versions", help="List Jellyfin versions and audio tracks"
    )
    _ = versions.add_argument("item_id")
    youtube = commands.add_parser("youtube", help="Import one YouTube video")
    _ = youtube.add_argument("url")
    jellyfin = commands.add_parser("jellyfin", help="Import one Jellyfin version")
    _ = jellyfin.add_argument("item_id")
    _ = jellyfin.add_argument("--media-source-id")
    _ = jellyfin.add_argument("--audio-language")
    arguments = cast("dict[str, object]", vars(parser.parse_args(argv)))
    command = cast("str", arguments["command"])

    from library import MediaLibrary  # noqa: PLC0415 - CLI owns startup
    from settings import SETTINGS  # noqa: PLC0415 - avoid eager config for module users
    from storage import DATABASE, initialize  # noqa: PLC0415 - CLI owns startup

    initialize(DATABASE)
    library = MediaLibrary(DATABASE, SETTINGS.vsmp_library_path)
    library.initialize()
    service = ImportService(
        library,
        jellyfin_url=str(SETTINGS.jellyfin_url) if SETTINGS.jellyfin_url else None,
        jellyfin_api_key=SETTINGS.jellyfin_api_key.get_secret_value()
        if SETTINGS.jellyfin_api_key
        else None,
        jellyfin_user_id=SETTINGS.jellyfin_user_id,
        caption_language=SETTINGS.vsmp_caption_language,
        asr_backend=SETTINGS.vsmp_asr_backend,
    )
    if command == "list":
        result: object = [asdict(item) for item in library.list_items()]
    elif command == "search":
        result = service.search_jellyfin(cast("str", arguments["query"]))
    elif command == "versions":
        result = service.jellyfin_versions(cast("str", arguments["item_id"]))
    elif command == "youtube":
        result = asdict(service.import_youtube(cast("str", arguments["url"])))
    else:
        result = asdict(
            service.import_jellyfin(
                cast("str", arguments["item_id"]),
                cast("str | None", arguments["media_source_id"]),
                audio_language=cast("str | None", arguments["audio_language"]),
            )
        )
    _ = sys.stdout.write(json.dumps(result, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
