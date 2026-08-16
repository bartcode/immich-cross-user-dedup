"""JSON (de)serialization for ScanResult, so scans survive restarts.

The web UI persists the last scan to reports/dedup_scan.json and reloads it on
startup — see web/state.py. Unknown fields are ignored on load for forward
compatibility with older payloads.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from immich_dedup.core.models import (
    AlbumRef,
    AssetInfo,
    DuplicateGroup,
    ScanResult,
    ScanStats,
    SkippedGroup,
    User,
    UserStats,
)

VERSION = 1


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _user(user: User) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "name": user.name}


def _album(album: AlbumRef) -> dict[str, Any]:
    return {"id": album.id, "name": album.name, "owner_id": album.owner_id}


def _asset(asset: AssetInfo) -> dict[str, Any]:
    return {
        "id": asset.id,
        "owner_id": asset.owner_id,
        "owner_email": asset.owner_email,
        "checksum": asset.checksum,
        "type": asset.type,
        "original_file_name": asset.original_file_name,
        "file_created_at": _iso(asset.file_created_at),
        "file_size_bytes": asset.file_size_bytes,
        "description": asset.description,
        "is_favorite": asset.is_favorite,
        "live_photo_video_id": asset.live_photo_video_id,
        "albums": [_album(album) for album in asset.albums],
    }


def _group(group: DuplicateGroup) -> dict[str, Any]:
    return {
        "checksum": group.checksum,
        "keeper": _asset(group.keeper),
        "losers": [_asset(loser) for loser in group.losers],
        "live_photo": dict(group.live_photo),
        "motion_ids": {key: list(value) for key, value in group.motion_ids.items()},
        "loser_reclaimable": dict(group.loser_reclaimable),
        "reclaimable_bytes": group.reclaimable_bytes,
    }


def _stats(stats: ScanStats) -> dict[str, Any]:
    return {
        "primary_assets": stats.primary_assets,
        "group_count": stats.group_count,
        "skipped_no_primary": stats.skipped_no_primary,
        "reclaimable_assets": stats.reclaimable_assets,
        "reclaimable_bytes": stats.reclaimable_bytes,
        "affected_albums": stats.affected_albums,
        "live_photo_aligned": stats.live_photo_aligned,
        "live_photo_keeper_lacks_motion": stats.live_photo_keeper_lacks_motion,
        "live_photo_loser_lacks_motion": stats.live_photo_loser_lacks_motion,
        "per_user": {email: vars(user_stats) for email, user_stats in stats.per_user.items()},
    }


def scan_to_payload(result: ScanResult, *, immich_url: str = "") -> dict[str, Any]:
    """Render a ScanResult as a JSON-safe payload wrapped in metadata."""
    return {
        "version": VERSION,
        "saved_at": datetime.now().astimezone().isoformat(),
        "immich_url": immich_url,
        "primary": _user(result.primary),
        "secondaries": [_user(user) for user in result.secondaries],
        "users": {user_id: _user(user) for user_id, user in result.users.items()},
        "groups": [_group(group) for group in result.groups],
        "skipped": [
            {
                "checksum": skipped.checksum,
                "owner_emails": list(skipped.owner_emails),
                "asset_ids": list(skipped.asset_ids),
            }
            for skipped in result.skipped
        ],
        "stats": _stats(result.stats),
        "motion_ids": sorted(result.motion_ids),
        "excluded": sorted(result.excluded),
    }


def _load_user(data: dict[str, Any]) -> User:
    return User(id=data["id"], email=data["email"], name=data.get("name", ""))


def _load_asset(data: dict[str, Any]) -> AssetInfo:
    return AssetInfo(
        id=data["id"],
        owner_id=data["owner_id"],
        owner_email=data.get("owner_email", ""),
        checksum=data.get("checksum", ""),
        type=data.get("type", "IMAGE"),
        original_file_name=data.get("original_file_name", ""),
        file_created_at=_parse_dt(data.get("file_created_at")),
        file_size_bytes=int(data.get("file_size_bytes", 0)),
        description=data.get("description", ""),
        is_favorite=bool(data.get("is_favorite", False)),
        live_photo_video_id=data.get("live_photo_video_id"),
        albums=[
            AlbumRef(id=album["id"], name=album.get("name", ""), owner_id=album.get("owner_id", ""))
            for album in data.get("albums", [])
        ],
    )


def _load_group(data: dict[str, Any]) -> DuplicateGroup:
    return DuplicateGroup(
        checksum=data["checksum"],
        keeper=_load_asset(data["keeper"]),
        losers=[_load_asset(loser) for loser in data.get("losers", [])],
        live_photo=dict(data.get("live_photo", {})),
        motion_ids={key: list(value) for key, value in data.get("motion_ids", {}).items()},
        loser_reclaimable=dict(data.get("loser_reclaimable", {})),
        reclaimable_bytes=int(data.get("reclaimable_bytes", 0)),
    )


def _load_stats(data: dict[str, Any]) -> ScanStats:
    per_user = {
        email: UserStats(
            assets=int(values.get("assets", 0)),
            trashed_files=int(values.get("trashed_files", 0)),
            trashed_bytes=int(values.get("trashed_bytes", 0)),
        )
        for email, values in data.get("per_user", {}).items()
    }
    return ScanStats(
        primary_assets=int(data.get("primary_assets", 0)),
        group_count=int(data.get("group_count", 0)),
        skipped_no_primary=int(data.get("skipped_no_primary", 0)),
        reclaimable_assets=int(data.get("reclaimable_assets", 0)),
        reclaimable_bytes=int(data.get("reclaimable_bytes", 0)),
        affected_albums=int(data.get("affected_albums", 0)),
        live_photo_aligned=int(data.get("live_photo_aligned", 0)),
        live_photo_keeper_lacks_motion=int(data.get("live_photo_keeper_lacks_motion", 0)),
        live_photo_loser_lacks_motion=int(data.get("live_photo_loser_lacks_motion", 0)),
        per_user=per_user,
    )


def payload_to_scan(payload: dict[str, Any]) -> ScanResult:
    """Rebuild a ScanResult from scan_to_payload output."""
    return ScanResult(
        primary=_load_user(payload["primary"]),
        secondaries=[_load_user(user) for user in payload.get("secondaries", [])],
        users={user_id: _load_user(user) for user_id, user in payload.get("users", {}).items()},
        groups=[_load_group(group) for group in payload.get("groups", [])],
        skipped=[
            SkippedGroup(
                checksum=skipped["checksum"],
                owner_emails=list(skipped.get("owner_emails", [])),
                asset_ids=list(skipped.get("asset_ids", [])),
            )
            for skipped in payload.get("skipped", [])
        ],
        stats=_load_stats(payload.get("stats", {})),
        motion_ids=set(payload.get("motion_ids", [])),
        excluded=set(payload.get("excluded", [])),
    )


def save_scan(path: Path, result: ScanResult, *, immich_url: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(scan_to_payload(result, immich_url=immich_url), indent=1))


def load_scan(path: Path) -> ScanResult | None:
    """Load a persisted scan; returns None when absent or unreadable."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        if payload.get("version") != VERSION:
            return None
        return payload_to_scan(payload)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
