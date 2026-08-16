"""An in-memory fake Immich server on httpx.MockTransport.

Models the subset of Immich v3 API behavior this tool depends on, including the
permission rules:

- ``DELETE /assets`` only touches assets owned by the caller.
- Adding an asset to an album requires album owner/editor rights, and the caller
  must own the asset or be a partner of its owner (Immich's AssetShare permission).
- Metadata search returns the caller's assets plus partner-shared ones when
  partner sharing has inTimeline enabled, excluding trashed/deleted/locked.
- Per-API-key permission scopes (Immich's api_key.permissions): users created
  with ``permissions=[...]`` are restricted to those scopes; a scope of
  ``all`` (or ``permissions=None``, the default) grants everything. The
  route-to-scope map mirrors the real server's @Authenticated decorators.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx

BASE = "http://immich.test"

# (method, path pattern) -> required api-key scope, mirroring Immich's controllers
ROUTE_SCOPES: list[tuple[str, re.Pattern[str], str]] = [
    ("GET", re.compile(r"^/api/users/me$"), "user.read"),
    ("POST", re.compile(r"^/api/search/metadata$"), "asset.read"),
    ("POST", re.compile(r"^/api/search/statistics$"), "asset.statistics"),
    ("GET", re.compile(r"^/api/albums$"), "album.read"),
    ("PUT", re.compile(r"^/api/albums/[^/]+/assets$"), "albumAsset.create"),
    ("POST", re.compile(r"^/api/albums/[^/]+/assets$"), "albumAsset.create"),
    ("DELETE", re.compile(r"^/api/albums/[^/]+/assets$"), "albumAsset.delete"),
    ("PUT", re.compile(r"^/api/albums/[^/]+/users$"), "albumUser.create"),
    ("DELETE", re.compile(r"^/api/albums/[^/]+/user/[^/]+$"), "albumUser.delete"),
    ("DELETE", re.compile(r"^/api/assets$"), "asset.delete"),
    ("POST", re.compile(r"^/api/trash/restore/assets$"), "asset.delete"),
    ("PUT", re.compile(r"^/api/assets/[^/]+$"), "asset.update"),
    ("GET", re.compile(r"^/api/assets/[^/]+/thumbnail$"), "asset.view"),
    ("GET", re.compile(r"^/api/assets/[^/]+$"), "asset.read"),
    ("GET", re.compile(r"^/api/partners$"), "partner.read"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FakeImmich:
    def __init__(self) -> None:
        self.users: dict[str, dict] = {}  # api_key -> user record
        self.assets: dict[str, dict] = {}  # asset_id -> record
        self.albums: dict[str, dict] = {}  # album_id -> record
        self.partners: dict[tuple[str, str], bool] = {}  # (shared_by, shared_with) -> inTimeline
        self.requests: list[tuple[str, str, str]] = []  # (method, path, auth user id or None)

    # -- test setup helpers -------------------------------------------------

    def add_user(self, email: str, name: str = "", permissions: list[str] | None = None) -> tuple[str, str]:
        """Returns (user_id, api_key). ``permissions`` restricts the key to those
        API scopes (None or containing 'all' = unrestricted)."""
        user_id, api_key = str(uuid.uuid4()), str(uuid.uuid4())
        record = {"id": user_id, "email": email, "name": name, "permissions": permissions}
        self.users[api_key] = record
        return user_id, api_key

    def add_api_key(self, user_id: str, permissions: list[str] | None = None) -> str:
        """Mint an additional API key for an existing user."""
        for record in self.users.values():
            if record["id"] == user_id:
                api_key = str(uuid.uuid4())
                self.users[api_key] = {**record, "permissions": permissions}
                return api_key
        raise KeyError(user_id)

    def add_asset(
        self,
        owner_id: str,
        checksum: str,
        *,
        type: str = "IMAGE",
        file_name: str = "photo.jpg",
        created_at: datetime | None = None,
        size_bytes: int = 1000,
        is_favorite: bool = False,
        description: str = "",
        live_photo_video_id: str | None = None,
        visibility: str = "timeline",
    ) -> str:
        asset_id = str(uuid.uuid4())
        self.assets[asset_id] = {
            "id": asset_id,
            "ownerId": owner_id,
            "checksum": checksum,
            "type": type,
            "originalFileName": file_name,
            "originalPath": f"upload/{owner_id}/{asset_id}-{file_name}",
            "fileCreatedAt": (created_at or _utcnow()).isoformat(),
            "isFavorite": is_favorite,
            "description": description,
            "livePhotoVideoId": live_photo_video_id,
            "visibility": visibility,
            "trashed": False,
            "deleted": False,
            "deletedAt": None,
            "fileSizeInByte": size_bytes,
        }
        return asset_id

    def add_album(
        self,
        owner_id: str,
        name: str,
        *,
        asset_ids: list[str] | None = None,
        shared_with: dict[str, str] | None = None,  # user_id -> role ('editor' | 'viewer')
    ) -> str:
        album_id = str(uuid.uuid4())
        self.albums[album_id] = {
            "id": album_id,
            "albumName": name,
            "ownerId": owner_id,
            "asset_ids": list(asset_ids or []),
            "users": dict(shared_with or {}),
        }
        return album_id

    def set_partner(self, shared_by: str, shared_with: str, *, in_timeline: bool = True) -> None:
        self.partners[(shared_by, shared_with)] = in_timeline

    def add_live_photo(
        self, owner_id: str, still_checksum: str, motion_checksum: str, **kwargs
    ) -> tuple[str, str]:
        """Returns (still_id, motion_id); the motion asset is hidden visibility."""
        motion_id = self.add_asset(
            owner_id,
            motion_checksum,
            type="VIDEO",
            file_name="motion.mp4",
            visibility="hidden",
            size_bytes=kwargs.pop("motion_size_bytes", 2000),
        )
        still_id = self.add_asset(owner_id, still_checksum, live_photo_video_id=motion_id, **kwargs)
        return still_id, motion_id

    # -- asset/album access helpers ----------------------------------------

    def is_partner_of(self, maybe_partner: str, owner: str) -> bool:
        return (owner, maybe_partner) in self.partners

    def album_visible_to(self, album: dict, user_id: str) -> bool:
        return album["ownerId"] == user_id or user_id in album["users"]

    def album_editable_by(self, album: dict, user_id: str) -> bool:
        return album["ownerId"] == user_id or album["users"].get(user_id) == "editor"

    def _asset_response(self, asset: dict, with_exif: bool) -> dict:
        payload = {
            "id": asset["id"],
            "ownerId": asset["ownerId"],
            "type": asset["type"],
            "originalFileName": asset["originalFileName"],
            "originalPath": asset["originalPath"],
            "fileCreatedAt": asset["fileCreatedAt"],
            "createdAt": asset["fileCreatedAt"],
            "isFavorite": asset["isFavorite"],
            "livePhotoVideoId": asset["livePhotoVideoId"],
            "checksum": asset["checksum"],
            "trashed": asset["trashed"],
            "deletedAt": asset["deletedAt"],
            "thumbhash": None,
        }
        if with_exif:
            payload["exifInfo"] = {
                "fileSizeInByte": asset["fileSizeInByte"],
                "description": asset["description"],
            }
        return payload

    def _album_response(self, album: dict, auth_user_id: str | None = None) -> dict:
        # v3 shape: NO ownerId — the FIRST albumUsers entry is the owner, the
        # second is the auth user when different, the rest follow
        entries = [(album["ownerId"], "owner")]
        for user_id, role in sorted(album["users"].items()):
            if user_id == album["ownerId"]:
                continue
            entries.append((user_id, role))
        if auth_user_id and auth_user_id != album["ownerId"] and auth_user_id not in album["users"]:
            entries.insert(1, (auth_user_id, "viewer"))
        by_email = {record["id"]: record["email"] for record in self.users.values()}
        return {
            "id": album["id"],
            "albumName": album["albumName"],
            "albumUsers": [
                {
                    "user": {"id": user_id, "email": by_email.get(user_id, "user@example.com"), "name": ""},
                    "role": role,
                }
                for user_id, role in entries
            ],
            "assetCount": len(album["asset_ids"]),
        }

    # -- routing ------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _auth(self, request: httpx.Request) -> dict | None:
        key = request.headers.get("x-api-key")
        return self.users.get(key)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        user = self._auth(request)
        path = request.url.path
        method = request.method
        self.requests.append((method, path, user["id"] if user else None))

        if user is None:
            return httpx.Response(401, json={"message": "Invalid API key"})

        permissions = user.get("permissions")
        if permissions is not None and "all" not in permissions:
            for route_method, pattern, scope in ROUTE_SCOPES:
                if route_method == method and pattern.match(path):
                    if scope not in permissions:
                        return httpx.Response(
                            403, json={"message": f"Missing required permission: {scope}"}
                        )
                    break

        if method == "GET" and path == "/api/users/me":
            return httpx.Response(200, json={k: v for k, v in user.items() if k != "permissions"})

        if method == "POST" and path == "/api/search/metadata":
            return self._search_metadata(user, request)

        if method == "POST" and path == "/api/search/statistics":
            return self._search_statistics(user)

        if method == "GET" and path == "/api/albums":
            return self._albums_list(user, request)

        if method in ("PUT", "POST", "DELETE") and path.startswith("/api/albums/") and path.endswith("/assets"):
            album_id = path.split("/")[3]
            return self._album_assets(user, request, album_id, add=method in ("PUT", "POST"))

        if method == "PUT" and path.startswith("/api/albums/") and path.endswith("/users"):
            return self._album_share_user(user, request, path.split("/")[3])

        if method == "DELETE" and path.startswith("/api/albums/") and "/user/" in path:
            parts = path.split("/")
            return self._album_remove_user(user, parts[3], parts[5])

        if method == "DELETE" and path == "/api/assets":
            return self._delete_assets(user, request)

        if method == "POST" and path == "/api/trash/restore/assets":
            return self._restore_assets(user, request)

        if method == "PUT" and path.startswith("/api/assets/"):
            return self._update_asset(user, request, path.split("/")[3])

        if method == "GET" and path == "/api/partners":
            return self._partners(user, request)

        if method == "GET" and path.startswith("/api/assets/") and path.endswith("/thumbnail"):
            return self._thumbnail(user, path.split("/")[3])

        if method == "GET" and path.startswith("/api/assets/"):
            asset = self.assets.get(path.split("/")[3])
            if asset is None or asset["deleted"]:
                return httpx.Response(404, json={"message": "Not found"})
            if asset["ownerId"] != user["id"] and not self.is_partner_of(user["id"], asset["ownerId"]):
                return httpx.Response(403, json={"message": "Not the owner or partner"})
            return httpx.Response(200, json=self._asset_response(asset, with_exif=True))

        return httpx.Response(404, json={"message": f"No fake route for {method} {path}"})

    def _visible_candidates(self, user: dict) -> list[dict]:
        visible_owners = {user["id"]}
        for (shared_by, shared_with), in_timeline in self.partners.items():
            if shared_with == user["id"] and in_timeline:
                visible_owners.add(shared_by)
        return [
            asset
            for asset in self.assets.values()
            if asset["ownerId"] in visible_owners
            and not asset["trashed"]
            and not asset["deleted"]
            and asset["visibility"] != "locked"
        ]

    def _search_statistics(self, user: dict) -> httpx.Response:
        # like the real server: the true count of assets visible to this user
        return httpx.Response(200, json={"total": len(self._visible_candidates(user))})

    def _search_metadata(self, user: dict, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        page = body.get("page", 1)
        size = body.get("size", 250)
        with_exif = body.get("withExif", False)

        candidates = self._visible_candidates(user)
        candidates.sort(key=lambda a: (a["fileCreatedAt"], a["id"]), reverse=True)

        total = len(candidates)
        start = (page - 1) * size
        items = candidates[start : start + size]
        next_page = str(page + 1) if start + size < total else None
        return httpx.Response(
            200,
            json={
                "albums": {"total": 0, "count": 0, "items": [], "facets": [], "nextPage": None},
                "assets": {
                    "total": len(items),  # like the real server: the page length, not the library size
                    "count": len(items),
                    "items": [self._asset_response(a, with_exif) for a in items],
                    "facets": [],
                    "nextPage": next_page,
                },
            },
        )

    def _albums_list(self, user: dict, request: httpx.Request) -> httpx.Response:
        asset_id = request.url.params.get("assetId")
        albums = [
            self._album_response(album, auth_user_id=user["id"])
            for album in self.albums.values()
            if (asset_id is None or asset_id in album["asset_ids"])
            and self.album_visible_to(album, user["id"])
        ]
        return httpx.Response(200, json=albums)

    def _album_assets(self, user: dict, request: httpx.Request, album_id: str, *, add: bool) -> httpx.Response:
        album = self.albums.get(album_id)
        if album is None:
            return httpx.Response(404, json={"message": "Album not found"})
        if not self.album_editable_by(album, user["id"]):
            return httpx.Response(403, json={"message": "Not an album owner or editor"})

        results = []
        for asset_id in json.loads(request.content).get("ids", []):
            asset = self.assets.get(asset_id)
            if asset is None or asset["deleted"]:
                results.append({"id": asset_id, "success": False, "error": "not_found"})
                continue
            if asset["ownerId"] != user["id"] and not self.is_partner_of(user["id"], asset["ownerId"]):
                results.append({"id": asset_id, "success": False, "error": "no_permission"})
                continue
            if add:
                if asset_id in album["asset_ids"]:
                    results.append({"id": asset_id, "success": False, "error": "duplicate"})
                else:
                    album["asset_ids"].append(asset_id)
                    results.append({"id": asset_id, "success": True})
            else:
                if asset_id in album["asset_ids"]:
                    album["asset_ids"].remove(asset_id)
                    results.append({"id": asset_id, "success": True})
                else:
                    results.append({"id": asset_id, "success": False, "error": "not_found"})
        return httpx.Response(200, json=results)

    def _album_share_user(self, user: dict, request: httpx.Request, album_id: str) -> httpx.Response:
        album = self.albums.get(album_id)
        if album is None:
            return httpx.Response(404, json={"message": "Album not found"})
        if album["ownerId"] != user["id"]:
            return httpx.Response(403, json={"message": "Only the album owner can share the album"})
        for entry in json.loads(request.content).get("albumUsers", []):
            album["users"][entry.get("userId", "")] = entry.get("role", "editor")
        return httpx.Response(200, json=self._album_response(album, auth_user_id=user["id"]))

    def _album_remove_user(self, user: dict, album_id: str, user_id: str) -> httpx.Response:
        album = self.albums.get(album_id)
        if album is None:
            return httpx.Response(404, json={"message": "Album not found"})
        if album["ownerId"] != user["id"]:
            return httpx.Response(403, json={"message": "Only the album owner can manage sharing"})
        if album["users"].pop(user_id, None) is None:
            return httpx.Response(404, json={"message": "User not in album"})
        return httpx.Response(204)

    def _delete_assets(self, user: dict, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        force = body.get("force", False)
        ids = body.get("ids", [])
        # like the real server: requireAccess over ALL ids first — any missing,
        # already-deleted, or foreign-owned id fails the entire request
        for asset_id in ids:
            asset = self.assets.get(asset_id)
            if asset is None or asset["deleted"]:
                return httpx.Response(400, json={"message": f"Asset {asset_id} not found"})
            if asset["ownerId"] != user["id"]:
                return httpx.Response(403, json={"message": "Missing required permission: asset.delete"})
        # then trash them all and answer 204 No Content (no per-id results)
        for asset_id in ids:
            asset = self.assets[asset_id]
            asset["trashed"] = True
            if force:
                asset["deleted"] = True
                asset["deletedAt"] = _utcnow().isoformat()
        return httpx.Response(204)

    def _restore_assets(self, user: dict, request: httpx.Request) -> httpx.Response:
        count = 0
        for asset_id in json.loads(request.content).get("ids", []):
            asset = self.assets.get(asset_id)
            if asset and asset["ownerId"] == user["id"] and asset["trashed"] and not asset["deleted"]:
                asset["trashed"] = False
                asset["deletedAt"] = None
                count += 1
        return httpx.Response(200, json={"count": count})

    def _update_asset(self, user: dict, request: httpx.Request, asset_id: str) -> httpx.Response:
        asset = self.assets.get(asset_id)
        if asset is None or asset["deleted"]:
            return httpx.Response(404, json={"message": "Not found"})
        if asset["ownerId"] != user["id"]:
            return httpx.Response(403, json={"message": "Not the owner"})
        for field in ("isFavorite", "description", "livePhotoVideoId"):
            if field in json.loads(request.content):
                asset[field] = json.loads(request.content)[field]
        return httpx.Response(200, json=self._asset_response(asset, with_exif=True))

    def _partners(self, user: dict, request: httpx.Request) -> httpx.Response:
        direction = request.url.params.get("direction", "shared-by")
        result = []
        for (shared_by, shared_with), in_timeline in self.partners.items():
            other_id, direction_match = (
                (shared_with, shared_by == user["id"])
                if direction == "shared-by"
                else (shared_by, shared_with == user["id"])
            )
            if direction_match:
                other = next((u for u in self.users.values() if u["id"] == other_id), None)
                if other:
                    result.append({**other, "inTimeline": in_timeline})
        return httpx.Response(200, json=result)

    def _thumbnail(self, user: dict, asset_id: str) -> httpx.Response:
        asset = self.assets.get(asset_id)
        if asset is None or asset["deleted"]:
            return httpx.Response(404, json={"message": "Not found"})
        if asset["ownerId"] != user["id"] and not self.is_partner_of(user["id"], asset["ownerId"]):
            return httpx.Response(403, json={"message": "Not the owner or partner"})
        return httpx.Response(200, content=f"thumb:{asset_id}".encode())

    # -- state inspection for assertions ------------------------------------

    def asset(self, asset_id: str) -> dict:
        return self.assets[asset_id]

    def album_asset_ids(self, album_id: str) -> set[str]:
        return set(self.albums[album_id]["asset_ids"])


def make_client(fake: FakeImmich, keys: dict[str, str]):
    """Build an ImmichClient wired to the fake server.

    ``keys`` maps email handles to API keys, e.g.
    ``{"alice@example.com": key_a, "bob@example.com": key_b}``."""
    from immich_dedup.core.api import ImmichClient

    return ImmichClient(BASE, keys, transport=fake.transport())


def days_ago(n: int) -> datetime:
    return _utcnow() - timedelta(days=n)
