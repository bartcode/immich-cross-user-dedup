"""Scan both users' libraries and build cross-user duplicate pairs."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

from immich_dedup.core.api import ImmichClient
from immich_dedup.core.models import (
    PRIMARY,
    SECONDARY,
    AlbumRef,
    AssetInfo,
    DuplicatePair,
    LivePhotoCase,
    ScanResult,
    ScanStats,
    User,
)

ProgressFn = Callable[[str, int, int | None], None]

# Fuzzy near-duplicate heuristics (report-only).
FUZZY_TIME_TOLERANCE = 2.0  # seconds between fileCreatedAt values
FUZZY_SIZE_TOLERANCE = 0.01  # relative difference of exif file sizes


def parse_asset(item: dict[str, Any], role: str, user_id: str) -> AssetInfo:
    exif = item.get("exifInfo") or {}
    created = item.get("fileCreatedAt")
    return AssetInfo(
        id=item["id"],
        owner_role=role,
        owner_id=item["ownerId"],
        checksum=item.get("checksum") or "",
        type=item.get("type", "IMAGE"),
        original_file_name=item.get("originalFileName", ""),
        file_created_at=_parse_datetime(created),
        file_size_bytes=int(exif.get("fileSizeInByte") or 0),
        description=exif.get("description") or "",
        is_favorite=bool(item.get("isFavorite", False)),
        live_photo_video_id=item.get("livePhotoVideoId"),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _user_assets(client: ImmichClient, user: User, progress: ProgressFn | None) -> list[AssetInfo]:
    role = user.role
    assets: list[AssetInfo] = []
    seen = 0

    def on_page(count: int, total: int | None) -> None:
        if progress:
            progress(f"fetch-{role}", count, total)

    for item in client.iter_assets(role, with_exif=True, progress=on_page):
        # Partner-shared assets appear in search results when "show in timeline"
        # is enabled; only assets actually owned by this user belong in the scan.
        if item.get("ownerId") != user.id:
            continue
        assets.append(parse_asset(item, role, user.id))
        seen += 1
    if progress:
        progress(f"fetch-{role}", seen, seen)
    return assets


def _live_photo_case(keeper: AssetInfo, loser: AssetInfo) -> str:
    if keeper.type != "IMAGE":
        return LivePhotoCase.ALIGNED
    keeper_motion = keeper.live_photo_video_id is not None
    loser_motion = loser.live_photo_video_id is not None
    if keeper_motion == loser_motion:
        return LivePhotoCase.ALIGNED
    return (
        LivePhotoCase.KEEPER_LACKS_MOTION if loser_motion else LivePhotoCase.LOSER_LACKS_MOTION
    )


def _enrich_loser_albums(
    client: ImmichClient, pairs: list[DuplicatePair], progress: ProgressFn | None
) -> None:
    for index, pair in enumerate(pairs, start=1):
        if progress:
            progress("albums", index, len(pairs))
        albums: dict[str, AlbumRef] = {}
        for role in (PRIMARY, SECONDARY):
            for album in client.get_albums_for_asset(role, pair.loser.id):
                albums.setdefault(
                    album["id"],
                    AlbumRef(id=album["id"], name=album.get("albumName", ""), owner_id=album.get("ownerId", "")),
                )
        pair.loser.albums = list(albums.values())


def scan(
    client: ImmichClient,
    primary: User,
    secondary: User,
    *,
    enrich_albums: bool = True,
    progress: ProgressFn | None = None,
) -> ScanResult:
    primary_assets = _user_assets(client, primary, progress)
    secondary_assets = _user_assets(client, secondary, progress)

    by_checksum: dict[str, list[AssetInfo]] = defaultdict(list)
    for asset in primary_assets + secondary_assets:
        if asset.checksum:
            by_checksum[asset.checksum].append(asset)

    id_to_asset = {a.id: a for a in primary_assets + secondary_assets}
    motion_ids = {a.live_photo_video_id for a in id_to_asset.values() if a.live_photo_video_id}

    pairs: list[DuplicatePair] = []
    for checksum, assets in by_checksum.items():
        keepers = [a for a in assets if a.owner_role == PRIMARY]
        losers = [a for a in assets if a.owner_role == SECONDARY]
        if keepers and losers:
            pairs.append(
                DuplicatePair(
                    checksum=checksum,
                    keeper=keepers[0],
                    loser=losers[0],
                    live_photo=_live_photo_case(keepers[0], losers[0]),
                )
            )
    pairs.sort(key=lambda p: (p.loser.file_created_at or datetime.min.replace(tzinfo=None), p.checksum), reverse=True)

    if enrich_albums:
        _enrich_loser_albums(client, pairs, progress)

    stats = _build_stats(pairs, primary_assets, secondary_assets, id_to_asset)
    return ScanResult(
        primary=primary,
        secondary=secondary,
        pairs=pairs,
        stats=stats,
        motion_ids=motion_ids,
    )


def _build_stats(
    pairs: list[DuplicatePair],
    primary_assets: list[AssetInfo],
    secondary_assets: list[AssetInfo],
    id_to_asset: dict[str, AssetInfo],
) -> ScanStats:
    stats = ScanStats(primary_assets=len(primary_assets), secondary_assets=len(secondary_assets))
    stats.pair_count = len(pairs)

    for pair in pairs:
        if pair.loser.live_photo_video_id:
            pair.motion_ids = [pair.loser.live_photo_video_id]
        pair.reclaimable_bytes = pair.loser.file_size_bytes + sum(
            id_to_asset[m].file_size_bytes for m in pair.motion_ids if m in id_to_asset
        )

        if pair.live_photo == LivePhotoCase.KEEPER_LACKS_MOTION:
            stats.live_photo_keeper_lacks_motion += 1
        elif pair.live_photo == LivePhotoCase.LOSER_LACKS_MOTION:
            stats.live_photo_loser_lacks_motion += 1
        else:
            stats.live_photo_aligned += 1

    reclaimable_ids = {pair.loser.id for pair in pairs} | {m for pair in pairs for m in pair.motion_ids}
    stats.reclaimable_assets = len(reclaimable_ids)
    stats.reclaimable_bytes = sum(
        id_to_asset[a].file_size_bytes for a in reclaimable_ids if a in id_to_asset
    )
    stats.affected_albums = len({album.id for pair in pairs for album in pair.loser.albums})
    return stats


def fuzzy_candidates(
    primary_assets: list[AssetInfo], secondary_assets: list[AssetInfo]
) -> list[tuple[AssetInfo, AssetInfo]]:
    """Near-duplicates that differ byte-wise: same type + filename, timestamps
    within FUZZY_TIME_TOLERANCE seconds, sizes within FUZZY_SIZE_TOLERANCE.
    Excludes pairs whose checksums already match exactly. Report-only."""
    by_name: dict[tuple[str, str], list[AssetInfo]] = defaultdict(list)
    for asset in primary_assets + secondary_assets:
        if asset.original_file_name:
            by_name[(asset.type, asset.original_file_name)].append(asset)

    candidates: list[tuple[AssetInfo, AssetInfo]] = []
    for assets in by_name.values():
        primaries = [a for a in assets if a.owner_role == PRIMARY]
        secondaries = [a for a in assets if a.owner_role == SECONDARY]
        for keeper in primaries:
            for loser in secondaries:
                if keeper.checksum == loser.checksum or not keeper.file_created_at or not loser.file_created_at:
                    continue
                delta = abs((keeper.file_created_at - loser.file_created_at).total_seconds())
                if delta > FUZZY_TIME_TOLERANCE:
                    continue
                if keeper.file_size_bytes and loser.file_size_bytes:
                    relative = abs(keeper.file_size_bytes - loser.file_size_bytes) / max(
                        keeper.file_size_bytes, loser.file_size_bytes
                    )
                    if relative > FUZZY_SIZE_TOLERANCE:
                        continue
                candidates.append((keeper, loser))
    return candidates
