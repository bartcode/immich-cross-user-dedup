"""HTTP client for the public Immich API, keyed by user email handle.

One httpx client per API key; every request goes out with the ``x-api-key``
header of the user it is made for. Endpoints used (Immich v3 API):

- GET    /api/users/me
- POST   /api/search/metadata          (paginated asset listing, withExif)
- GET    /api/albums[?assetId=...]     (list albums / albums containing an asset)
- PUT    /api/albums/{id}/assets       (add assets to album; POST on older servers)
- DELETE /api/albums/{id}/assets       (remove assets from album)
- PUT    /api/albums/{id}/users        (share album with a user, e.g. as editor)
- DELETE /api/albums/{id}/user/{uid}   (revoke an album share)
- DELETE /api/assets                   (trash / hard-delete; {ids, force})
- POST   /api/trash/restore/assets     (restore from trash)
- PUT    /api/assets/{id}              (favorite / description / livePhotoVideoId)
- GET    /api/assets/{id}              (single asset lookup)
- GET    /api/partners?direction=...
- GET    /api/assets/{id}/thumbnail?size=...
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

PAGE_SIZE = 1000  # API maximum for search/metadata
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE = 0.5  # seconds; doubles per retry


class ImmichApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ImmichAuthError(ImmichApiError):
    pass


class ImmichClient:
    """Holds one httpx client per user, addressed by email handle."""

    def __init__(
        self,
        base_url: str,
        keys: dict[str, str],
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ):
        if not keys:
            raise ValueError("at least one user key is required")
        self._clients: dict[str, httpx.Client] = {
            handle: httpx.Client(
                base_url=base_url,
                headers={"x-api-key": key},
                timeout=timeout,
                transport=transport,
            )
            for handle, key in keys.items()
        }

    @property
    def handles(self) -> list[str]:
        return list(self._clients)

    def close(self) -> None:
        for client in self._clients.values():
            client.close()

    def __enter__(self) -> ImmichClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level ---------------------------------------------------------

    def _request(
        self,
        handle: str,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        client = self._clients[handle]
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
            try:
                response = client.request(method, path, json=json, params=params)
            except httpx.TransportError as error:
                last_error = error
                continue
            if response.status_code in RETRYABLE_STATUS:
                last_error = ImmichApiError(
                    f"{method} {path} -> {response.status_code} (retryable)", response.status_code
                )
                continue
            if response.status_code == 401:
                raise ImmichAuthError(
                    f"{method} {path} -> 401 Unauthorized: the API key for {handle} is invalid."
                )
            if response.status_code >= 400:
                raise ImmichApiError(
                    f"{method} {path} -> {response.status_code}: {response.text[:500]}",
                    response.status_code,
                )
            return response
        raise ImmichApiError(f"{method} {path} failed after {MAX_RETRIES + 1} attempts: {last_error}")

    def _request_json(
        self,
        handle: str,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self._request(handle, method, path, json=json, params=params).json()

    # -- endpoints ---------------------------------------------------------

    def get_me(self, handle: str) -> dict[str, Any]:
        return dict(self._request_json(handle, "GET", "/api/users/me"))

    def iter_assets(
        self, handle: str, *, with_exif: bool = True, progress: Callable[[int, int | None], None] | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every asset visible to this user's search, one page at a time.

        NOTE: results may include partner-shared assets (Immich includes them when
        partner sharing has "show in timeline" enabled). Callers must filter by
        ``ownerId`` — see match.scan().
        """
        page = 1
        fetched = 0
        while True:
            body = {"page": page, "size": PAGE_SIZE}
            if with_exif:
                body["withExif"] = True
            payload = self._request_json(handle, "POST", "/api/search/metadata", json=body)
            assets = payload.get("assets", {})
            items = assets.get("items", [])
            for item in items:
                fetched += 1
                if progress:
                    progress(fetched, assets.get("total"))
                yield item
            next_page = assets.get("nextPage")
            if not next_page:
                return
            page = int(next_page)

    def get_albums_for_asset(self, handle: str, asset_id: str) -> list[dict[str, Any]]:
        payload = self._request_json(handle, "GET", "/api/albums", params={"assetId": asset_id})
        return list(payload) if isinstance(payload, list) else []

    def list_albums(self, handle: str) -> list[dict[str, Any]]:
        payload = self._request_json(handle, "GET", "/api/albums")
        return list(payload) if isinstance(payload, list) else []

    def count_assets(self, handle: str) -> int:
        """Single-page search probe: returns the total asset count for this user."""
        payload = self._request_json(
            handle, "POST", "/api/search/metadata", json={"page": 1, "size": 1, "withExif": False}
        )
        return int(payload.get("assets", {}).get("total") or 0)

    def add_album_assets(self, handle: str, album_id: str, asset_ids: list[str]) -> list[dict[str, Any]]:
        """Returns one BulkIdResponseDto per asset id: {id, success, error?}.

        Current Immich uses PUT for this route; older servers (v1/v2) used POST,
        so we fall back on 404/405."""
        body = {"ids": asset_ids}
        try:
            return list(self._request_json(handle, "PUT", f"/api/albums/{album_id}/assets", json=body))
        except ImmichApiError as error:
            if error.status_code in (404, 405):
                return list(self._request_json(handle, "POST", f"/api/albums/{album_id}/assets", json=body))
            raise

    def remove_album_assets(self, handle: str, album_id: str, asset_ids: list[str]) -> list[dict[str, Any]]:
        return list(
            self._request_json(
                handle, "DELETE", f"/api/albums/{album_id}/assets", json={"ids": asset_ids}
            )
        )

    def share_album_with_user(
        self, handle: str, album_id: str, user_id: str, *, role: str = "editor"
    ) -> dict[str, Any]:
        """Share an album with a user (album owner's key; albumUser.create scope)."""
        return dict(
            self._request_json(
                handle,
                "PUT",
                f"/api/albums/{album_id}/users",
                json={"albumUsers": [{"userId": user_id, "role": role}]},
            )
        )

    def remove_album_user(self, handle: str, album_id: str, user_id: str) -> None:
        """Revoke an album share (album owner's key; albumUser.delete scope)."""
        self._request(handle, "DELETE", f"/api/albums/{album_id}/user/{user_id}")

    def trash_assets(self, handle: str, asset_ids: list[str], *, force: bool = False) -> list[dict[str, Any]]:
        return list(
            self._request_json(
                handle, "DELETE", "/api/assets", json={"ids": asset_ids, "force": force}
            )
        )

    def restore_assets(self, handle: str, asset_ids: list[str]) -> dict[str, Any]:
        return dict(
            self._request_json(handle, "POST", "/api/trash/restore/assets", json={"ids": asset_ids})
        )

    def update_asset(self, handle: str, asset_id: str, **fields: Any) -> dict[str, Any]:
        return dict(self._request_json(handle, "PUT", f"/api/assets/{asset_id}", json=fields))

    def get_asset(self, handle: str, asset_id: str) -> dict[str, Any]:
        return dict(self._request_json(handle, "GET", f"/api/assets/{asset_id}"))

    def get_partners(self, handle: str) -> dict[str, list[dict[str, Any]]]:
        by = self._request_json(handle, "GET", "/api/partners", params={"direction": "shared-by"})
        with_ = self._request_json(handle, "GET", "/api/partners", params={"direction": "shared-with"})
        return {"shared-by": list(by), "shared-with": list(with_)}

    def get_thumbnail(self, handle: str, asset_id: str, *, size: str = "preview") -> bytes:
        return self._request(
            handle, "GET", f"/api/assets/{asset_id}/thumbnail", params={"size": size}
        ).content
