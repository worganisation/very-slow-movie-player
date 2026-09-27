"""Read an Immich album for playback on the e-paper display."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Annotated, Self, cast
from uuid import UUID  # noqa: TC003 - Pydantic resolves Asset.id at runtime

from httpx2 import Client, Timeout
from pydantic import BaseModel, Field, ValidationError
from settings import SETTINGS
from utils import const

if TYPE_CHECKING:
    from collections.abc import Iterator

MAX_SUFFIX_LENGTH = 10
BAD_REQUEST = 400


class Asset(BaseModel):
    """The small subset of Immich asset metadata needed for playback."""

    id: UUID
    kind: Annotated[str, Field(alias="type")]
    filename: Annotated[str, Field(alias="originalFileName")]


class SearchAssetPage(BaseModel):
    """The paginated asset section of Immich's metadata search response."""

    items: list[Asset]
    next_page: Annotated[str | None, Field(alias="nextPage")] = None
    next_cursor: Annotated[str | None, Field(alias="nextCursor")] = None


class SearchResponse(BaseModel):
    """Immich metadata search response."""

    assets: SearchAssetPage


class AlbumInfo(BaseModel):
    """Fields needed for a friendly, stable Home Assistant album selector."""

    id: UUID
    name: Annotated[str, Field(alias="albumName")]


class ImmichAlbum:
    """A read-only Immich API client with a bounded-memory download path."""

    def __init__(self, album_id: UUID | None = None) -> None:
        url = str(SETTINGS.immich_url).rstrip("/")
        self.album_id: UUID = album_id or SETTINGS.immich_album_id
        self.client: Client = Client(
            base_url=f"{url if url.endswith('/api') else url + '/api'}/",
            headers={"x-api-key": SETTINGS.immich_api_key.get_secret_value()},
            timeout=Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )

    def __enter__(self) -> Self:
        """Use the album client as a context manager."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close connections when playback stops."""
        self.client.close()

    def albums(self) -> list[AlbumInfo]:
        """List accessible albums via Immich's album read endpoint."""
        response = self.client.get("albums")
        _ = response.raise_for_status()
        raw = cast("object", response.json())
        if not isinstance(raw, list):
            raise TypeError("Immich returned an invalid album list")
        try:
            return [AlbumInfo.model_validate(item) for item in cast("list[object]", raw)]
        except ValidationError as exc:
            raise ValueError("Immich returned an invalid album list") from exc

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
                content_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].lower()
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
