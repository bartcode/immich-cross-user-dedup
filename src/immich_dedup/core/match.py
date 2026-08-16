"""Scan all users' libraries and build cross-user duplicate groups."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from immich_dedup.core.api import ImmichApiError, ImmichClient
from immich_dedup.core.models import (
    AlbumRef,
    AssetInfo,
    DuplicateGroup,
    LivePhotoCase,
    ScanResult,
    ScanStats,
    SkippedGroup,
    User,
    UserStats,
)

ProgressFn = Callable[[str, int, int | None], None]

# Fuzzy near-duplicate heuristics (report-only).
FUZZY_TIME_TOLERANCE = 2.0  # seconds between fileCreatedAt values
FUZZY_SIZE_TOLERANCE = 0.01  # relative difference of exif file sizes


def parse_asset(item: dict[str, Any], users: dict[str, User]) -> AssetInfo:
    exif = item.get("exifInfo") or {}
    owner = users.get(item["ownerId"])
    return AssetInfo(
        id=item["id"],
        owner_id=item["ownerId"],
        owner_email=owner.email if owner else "",
        checksum=item.get("checksum") or "",
        type=item.get("type", "IMAGE"),
        original_file_name=item.get("originalFileName", ""),
        file_created_at=_parse_datetime(item.get("fileCreatedAt")),
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


def user_assets(client: ImmichClient, user: User, progress: ProgressFn | None = None) -> list[AssetInfo]:
    handle = user.email
    users = {user.id: user}
    try:
        total: int | None = client.asset_count(handle)  # true count via statistics
    except ImmichApiError:
        total = None  # older server or missing scope — progress without a denominator

    def on_page(count: int, _ignored_total: int | None) -> None:
        if progress:
            progress(f"fetch-{user.email}", count, total)

    assets: list[AssetInfo] = []
    for item in client.iter_assets(handle, with_exif=True, progress=on_page):
        # Partner-shared assets appear in search results when "show in timeline"
        # is enabled; only assets actually owned by this user belong in the scan.
        if item.get("ownerId") != user.id:
            continue
        assets.append(parse_asset(item, users))
    if progress:
        progress(f"fetch-{user.email}", len(assets), len(assets))
    return assets


def _live_photo_case(keeper: AssetInfo, loser: AssetInfo) -> str:
    if keeper.type != "IMAGE":
        return LivePhotoCase.ALIGNED
    keeper_motion = keeper.live_photo_video_id is not None
    loser_motion = loser.live_photo_video_id is not None
    if keeper_motion == loser_motion:
        return LivePhotoCase.ALIGNED
    return LivePhotoCase.KEEPER_LACKS_MOTION if loser_motion else LivePhotoCase.LOSER_LACKS_MOTION


def parse_album_ref(album: dict) -> AlbumRef:
    """Resolve an album's owner across Immich API versions.

    v3 removed `ownerId` from album responses — the owner is the FIRST entry of
    `albumUsers` (documented upstream); older versions carry `ownerId`."""
    owner_id = album.get("ownerId") or ""
    owner_email = ""
    if not owner_id:
        members = album.get("albumUsers") or []
        if members:
            owner = members[0].get("user") or {}
            owner_id = owner.get("id") or ""
            owner_email = owner.get("email") or ""
    return AlbumRef(id=album["id"], name=album.get("albumName", ""), owner_id=owner_id, owner_email=owner_email)


def _enrich_loser_albums(
    client: ImmichClient, result_users: list[User], groups: list[DuplicateGroup], progress: ProgressFn | None
) -> None:
    losers = [loser for group in groups for loser in group.losers]
    for index, loser in enumerate(losers, start=1):
        if progress:
            progress("albums", index, len(losers))
        albums: dict[str, AlbumRef] = {}
        # An album can be owned by any participant; query with every user's key
        # and union the results so third-party-owned albums are found too.
        for user in result_users:
            for album in client.get_albums_for_asset(user.email, loser.id):
                albums.setdefault(album["id"], parse_album_ref(album))
        loser.albums = list(albums.values())


def scan(
    client: ImmichClient,
    primary: User,
    secondaries: list[User],
    *,
    users: dict[str, User] | None = None,
    enrich_albums: bool = True,
    progress: ProgressFn | None = None,
) -> ScanResult:
    all_users = [primary, *secondaries]
    registry = users if users is not None else {user.id: user for user in all_users}

    primary_assets = user_assets(client, primary, progress)
    secondary_assets: dict[str, list[AssetInfo]] = {
        secondary.email: user_assets(client, secondary, progress) for secondary in secondaries
    }

    by_checksum: dict[str, list[AssetInfo]] = defaultdict(list)
    for asset in primary_assets:
        if asset.checksum:
            by_checksum[asset.checksum].append(asset)
    for assets in secondary_assets.values():
        for asset in assets:
            if asset.checksum:
                by_checksum[asset.checksum].append(asset)

    id_to_asset = {a.id: a for a in [primary_assets, *secondary_assets.values()] for a in a}
    motion_ids = {a.live_photo_video_id for a in id_to_asset.values() if a.live_photo_video_id}

    groups: list[DuplicateGroup] = []
    skipped: list[SkippedGroup] = []
    for checksum, assets in by_checksum.items():
        owners = {asset.owner_id for asset in assets}
        if len(owners) < 2:
            continue
        keeper = next((a for a in assets if a.owner_id == primary.id), None)
        if keeper is None:
            skipped.append(
                SkippedGroup(
                    checksum=checksum,
                    owner_emails=sorted({a.owner_email for a in assets}),
                    asset_ids=sorted(a.id for a in assets),
                )
            )
            continue
        losers = [a for a in assets if a.owner_id != primary.id]
        group = DuplicateGroup(checksum=checksum, keeper=keeper, losers=losers)
        for loser in losers:
            group.live_photo[loser.id] = _live_photo_case(keeper, loser)
        groups.append(group)

    def _group_sort_key(group: DuplicateGroup) -> tuple[datetime, str]:
        latest = max(
            (loser.file_created_at for loser in group.losers if loser.file_created_at),
            default=datetime.min.replace(tzinfo=UTC),
        )
        return (latest, group.checksum)

    groups.sort(key=_group_sort_key, reverse=True)

    if enrich_albums:
        _enrich_loser_albums(client, all_users, groups, progress)

    stats = _build_stats(primary, secondaries, primary_assets, secondary_assets, groups, skipped, id_to_asset)
    return ScanResult(
        primary=primary,
        secondaries=secondaries,
        users=registry,
        groups=groups,
        skipped=skipped,
        stats=stats,
        motion_ids=motion_ids,
    )


def _build_stats(
    primary: User,
    secondaries: list[User],
    primary_assets: list[AssetInfo],
    secondary_assets: dict[str, list[AssetInfo]],
    groups: list[DuplicateGroup],
    skipped: list[SkippedGroup],
    id_to_asset: dict[str, AssetInfo],
) -> ScanStats:
    stats = ScanStats(primary_assets=len(primary_assets), group_count=len(groups))
    stats.skipped_no_primary = len(skipped)

    per_user = {
        secondary.email: UserStats(assets=len(secondary_assets[secondary.email])) for secondary in secondaries
    }
    for group in groups:
        for loser in group.losers:
            user_stats = per_user.setdefault(loser.owner_email, UserStats())
            if loser.live_photo_video_id:
                group.motion_ids[loser.id] = [loser.live_photo_video_id]

            case = group.live_photo.get(loser.id, LivePhotoCase.ALIGNED)
            if case == LivePhotoCase.KEEPER_LACKS_MOTION:
                stats.live_photo_keeper_lacks_motion += 1
            elif case == LivePhotoCase.LOSER_LACKS_MOTION:
                stats.live_photo_loser_lacks_motion += 1
            else:
                stats.live_photo_aligned += 1

            user_stats.trashed_files += 1 + len(group.motion_ids.get(loser.id, []))
            group.loser_reclaimable[loser.id] = loser.file_size_bytes + sum(
                id_to_asset[m].file_size_bytes for m in group.motion_ids.get(loser.id, []) if m in id_to_asset
            )

        group.reclaimable_bytes = sum(group.loser_reclaimable.values())

    reclaimable_ids = {loser.id for group in groups for loser in group.losers} | {
        motion for group in groups for motions in group.motion_ids.values() for motion in motions
    }
    stats.reclaimable_assets = len(reclaimable_ids)
    stats.reclaimable_bytes = sum(
        id_to_asset[a].file_size_bytes for a in reclaimable_ids if a in id_to_asset
    )
    stats.affected_albums = len({album.id for group in groups for loser in group.losers for album in loser.albums})
    for email, user_stats in per_user.items():
        user_stats.trashed_bytes = sum(
            id_to_asset[a].file_size_bytes
            for a in reclaimable_ids
            if a in id_to_asset and id_to_asset[a].owner_email == email
        )
    stats.per_user = per_user
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
        primaries = [a for a in assets if a.owner_id == _primary_owner_id(primary_assets)]
        secondaries_ = [a for a in assets if a.owner_id != _primary_owner_id(primary_assets)]
        for keeper in primaries:
            for loser in secondaries_:
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


def _primary_owner_id(primary_assets: list[AssetInfo]) -> str:
    return primary_assets[0].owner_id if primary_assets else ""
