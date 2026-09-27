"""Read an Immich album for playback on the e-paper display."""

from __future__ import annotations

from os import getenv
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Self, cast
from urllib.parse import urlsplit
from uuid import UUID

from httpx import Client, Timeout
from pydantic import BaseModel, Field, ValidationError
from utils import const  # pyright: ignore[reportImplicitRelativeImport]

if TYPE_CHECKING:
    from collections.abc import Iterator

MAX_SUFFIX_LENGTH = 10
BAD_REQUEST = 400


class Asset(BaseModel):
    """The small subset of Immich asset metadata needed for playback."""

    id: UUID
    kind: str = Field(alias="type")
    filename: str = Field(alias="originalFileName")


class SearchAssetPage(BaseModel):
    """The paginated asset section of Immich's metadata search response."""

    items: list[Asset]
    next_page: str | None = Field(default=None, alias="nextPage")
    next_cursor: str | None = Field(default=None, alias="nextCursor")


class SearchResponse(BaseModel):
    """Immich metadata search response."""

    assets: SearchAssetPage


class ImmichAlbum:
    """A read-only Immich API client with a bounded-memory download path."""

    def __init__(self) -> None:
        url = getenv("IMMICH_URL", "").strip().rstrip("/")
        key = getenv("IMMICH_API_KEY", "")
        album_id = getenv("IMMICH_ALBUM_ID", "")
        parsed = urlsplit(url)
        invalid_authority = not parsed.netloc or parsed.username or parsed.password
        invalid_suffix = parsed.query or parsed.fragment
        if parsed.scheme not in {"http", "https"} or invalid_authority or invalid_suffix:
            raise ValueError(
                "IMMICH_URL must be an HTTP(S) server URL without credentials or query",
            )
        if not key:
            raise ValueError("IMMICH_API_KEY is required for Immich playback")
        try:
            self.album_id: UUID = UUID(album_id)
        except ValueError as exc:
            raise ValueError("IMMICH_ALBUM_ID must be a UUID") from exc
        self.client: Client = Client(
            base_url=f"{url if parsed.path.endswith('/api') else url + '/api'}/",
            headers={"x-api-key": key},
            timeout=Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )

    def __enter__(self) -> Self:
        """Use the album client as a context manager."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close connections when playback stops."""
        self.client.close()

    def assets(self) -> Iterator[Asset]:  # noqa: C901, PLR0912
        """Page through album assets; v3 removed assets from album detail."""
        page = 1
        cursor: str | None = None
        modern = False
        seen: set[UUID] = set()
        while True:
            body: dict[str, object] = {"size": 250}
            if modern:
                body["filter"] = {"albumIds": {"any": [str(self.album_id)]}}
                if cursor:
                    body["cursor"] = cursor
            else:
                body.update(albumIds=[str(self.album_id)], page=page)
            response = self.client.post("search/metadata", json=body)
            if response.status_code == BAD_REQUEST and not modern and page == 1:
                modern = True
                continue
            _ = response.raise_for_status()
            try:
                data = SearchResponse.model_validate_json(response.content).assets
            except ValidationError as exc:
                raise ValueError(
                    "Immich returned an invalid asset search response",
                ) from exc
            for asset in data.items:
                if asset.kind not in {"IMAGE", "VIDEO"}:
                    raise ValueError("Immich returned an unsupported asset type")
                if asset.id in seen:
                    raise ValueError("Immich returned repeated album assets")
                seen.add(asset.id)
                yield asset
            if modern:
                next_cursor = data.next_cursor
                if not next_cursor:
                    return
                if next_cursor == cursor or not data.items:
                    raise ValueError("Immich returned an invalid search cursor")
                cursor = next_cursor
            else:
                next_page = data.next_page
                if not next_page:
                    return
                if not data.items:
                    raise ValueError("Immich returned an invalid next page")
                page += 1

    def download(self, asset: Asset) -> Path:
        """Cache a preview image or original video under its immutable asset ID."""
        suffix = ".jpg" if asset.kind == "IMAGE" else Path(asset.filename).suffix.lower()
        if asset.kind == "VIDEO" and (
            not suffix or len(suffix) > MAX_SUFFIX_LENGTH or not suffix[1:].isalnum()
        ):
            suffix = ".video"
        target = const.MEDIA_DIR / "immich" / f"{asset.id}{suffix}"
        if target.is_file() and target.stat().st_size > 0:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        endpoint = (
            f"assets/{asset.id}/thumbnail"
            if asset.kind == "IMAGE"
            else f"assets/{asset.id}/original"
        )
        params = {"size": "preview"} if asset.kind == "IMAGE" else None
        temporary: Path | None = None
        try:
            with self.client.stream("GET", endpoint, params=params) as response:
                _ = response.raise_for_status()
                content_type: str = (
                    cast("str", response.headers.get("content-type", ""))
                    .split(";", 1)[0]
                    .lower()
                )
                if asset.kind == "IMAGE" and not content_type.startswith("image/"):
                    raise ValueError("Immich returned a non-image preview")
                if (
                    asset.kind == "VIDEO"
                    and content_type
                    not in {"application/octet-stream", "application/force-download"}
                    and not content_type.startswith("video/")
                ):
                    raise ValueError("Immich returned a non-video download")
                with NamedTemporaryFile(
                    dir=target.parent,
                    prefix=f".{asset.id}.",
                    suffix=".tmp",
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    size = 0
                    for chunk in response.iter_bytes(64 * 1024):
                        _ = output.write(chunk)
                        size += len(chunk)
                if not size:
                    raise ValueError("Immich returned an empty media file")
                _ = Path(temporary).replace(target)
                temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return target
