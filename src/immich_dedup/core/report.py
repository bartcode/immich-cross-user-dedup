"""CSV report and stdout summary for a scan."""

from __future__ import annotations

import csv
from pathlib import Path

from immich_dedup.core.models import AssetInfo, ScanResult, human_size

GROUP_FIELDS = [
    "checksum",
    "type",
    "live_photo_case",
    "keeper_id",
    "keeper_file",
    "keeper_taken_at",
    "keeper_url",
    "loser_id",
    "loser_owner",
    "loser_file",
    "loser_taken_at",
    "loser_url",
    "loser_albums",
    "loser_bytes",
    "total_reclaimable_bytes",
]

FUZZY_FIELDS = [
    "keeper_id",
    "keeper_url",
    "keeper_file",
    "keeper_taken_at",
    "keeper_bytes",
    "loser_id",
    "loser_url",
    "loser_file",
    "loser_taken_at",
    "loser_bytes",
    "time_delta_seconds",
    "size_delta_percent",
]


def asset_url(base_url: str, asset_id: str) -> str:
    return f"{base_url}/photos/{asset_id}"


def write_csv(result: ScanResult, path: Path, base_url: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=GROUP_FIELDS)
        writer.writeheader()
        for group in result.groups:
            for loser in group.losers:
                writer.writerow(
                    {
                        "checksum": group.checksum,
                        "type": group.keeper.type,
                        "live_photo_case": group.live_photo.get(loser.id, "aligned"),
                        "keeper_id": group.keeper.id,
                        "keeper_file": group.keeper.original_file_name,
                        "keeper_taken_at": group.keeper.file_created_at.isoformat()
                        if group.keeper.file_created_at
                        else "",
                        "keeper_url": asset_url(base_url, group.keeper.id),
                        "loser_id": loser.id,
                        "loser_owner": loser.owner_email,
                        "loser_file": loser.original_file_name,
                        "loser_taken_at": loser.file_created_at.isoformat() if loser.file_created_at else "",
                        "loser_url": asset_url(base_url, loser.id),
                        "loser_albums": "; ".join(album.name for album in loser.albums),
                        "loser_bytes": loser.file_size_bytes,
                        "total_reclaimable_bytes": group.loser_reclaimable.get(loser.id, loser.file_size_bytes),
                    }
                )
    return path


def write_fuzzy_csv(candidates: list[tuple[AssetInfo, AssetInfo]], path: Path, base_url: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FUZZY_FIELDS)
        writer.writeheader()
        for keeper, loser in candidates:
            delta = abs((keeper.file_created_at - loser.file_created_at).total_seconds())
            relative = 0.0
            if keeper.file_size_bytes and loser.file_size_bytes:
                relative = 100 * abs(keeper.file_size_bytes - loser.file_size_bytes) / max(
                    keeper.file_size_bytes, loser.file_size_bytes
                )
            writer.writerow(
                {
                    "keeper_id": keeper.id,
                    "keeper_url": asset_url(base_url, keeper.id),
                    "keeper_file": keeper.original_file_name,
                    "keeper_taken_at": keeper.file_created_at.isoformat(),
                    "keeper_bytes": keeper.file_size_bytes,
                    "loser_id": loser.id,
                    "loser_url": asset_url(base_url, loser.id),
                    "loser_file": loser.original_file_name,
                    "loser_taken_at": loser.file_created_at.isoformat(),
                    "loser_bytes": loser.file_size_bytes,
                    "time_delta_seconds": f"{delta:.1f}",
                    "size_delta_percent": f"{relative:.2f}",
                }
            )
    return path


def summary_text(result: ScanResult, fuzzy_count: int = 0) -> str:
    stats = result.stats
    lines = [
        f"Primary:   {result.primary.email} — {stats.primary_assets} assets (keeps its copies)",
    ]
    for secondary in result.secondaries:
        user_stats = stats.per_user.get(secondary.email)
        lines.append(
            f"Secondary: {secondary.email} — {user_stats.assets if user_stats else 0} assets, "
            f"{user_stats.trashed_files if user_stats else 0} would be trashed "
            f"({human_size(user_stats.trashed_bytes) if user_stats else '0 B'})"
        )
    lines += [
        "",
        f"Cross-user duplicate groups: {stats.group_count}",
        f"  live photos, both sides equal:    {stats.live_photo_aligned}",
        f"  live photos, keeper lacks motion: {stats.live_photo_keeper_lacks_motion}",
        f"  live photos, loser lacks motion:  {stats.live_photo_loser_lacks_motion}",
    ]
    if stats.skipped_no_primary:
        owners = sorted(
            {email for skipped in result.skipped for email in skipped.owner_emails}
        )
        lines += [
            "",
            f"Skipped (no primary copy; only secondaries own these): {stats.skipped_no_primary} groups"
            f" involving {', '.join(owners)}",
        ]
    lines += [
        "",
        f"Reclaimable: {stats.reclaimable_assets} assets, {stats.reclaimable_human} "
        "(originals; Immich also purges previews/thumbnails)",
        f"Albums affected: {stats.affected_albums}",
    ]
    if fuzzy_count:
        lines.append(f"Byte-different near-duplicates (review manually): {fuzzy_count}")
    if stats.group_count == 0 and stats.skipped_no_primary == 0:
        lines += ["", "No cross-user duplicates found — nothing to do."]
    return "\n".join(lines)
