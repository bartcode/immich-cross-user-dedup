"""CSV report and stdout summary for a scan."""

from __future__ import annotations

import csv
from pathlib import Path

from immich_dedup.core.models import AssetInfo, ScanResult, human_size

PAIR_FIELDS = [
    "checksum",
    "type",
    "live_photo_case",
    "keeper_id",
    "keeper_file",
    "keeper_taken_at",
    "keeper_url",
    "loser_id",
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
        writer = csv.DictWriter(handle, fieldnames=PAIR_FIELDS)
        writer.writeheader()
        for pair in result.pairs:
            writer.writerow(
                {
                    "checksum": pair.checksum,
                    "type": pair.keeper.type,
                    "live_photo_case": pair.live_photo,
                    "keeper_id": pair.keeper.id,
                    "keeper_file": pair.keeper.original_file_name,
                    "keeper_taken_at": pair.keeper.file_created_at.isoformat() if pair.keeper.file_created_at else "",
                    "keeper_url": asset_url(base_url, pair.keeper.id),
                    "loser_id": pair.loser.id,
                    "loser_file": pair.loser.original_file_name,
                    "loser_taken_at": pair.loser.file_created_at.isoformat() if pair.loser.file_created_at else "",
                    "loser_url": asset_url(base_url, pair.loser.id),
                    "loser_albums": "; ".join(album.name for album in pair.loser.albums),
                    "loser_bytes": pair.loser.file_size_bytes,
                    "total_reclaimable_bytes": pair.reclaimable_bytes,
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
        f"Primary:   {result.primary.email} — {stats.primary_assets} assets",
        f"Secondary: {result.secondary.email} — {stats.secondary_assets} assets",
        "",
        f"Cross-user duplicate pairs: {stats.pair_count}",
        f"  live photos, both sides equal:    {stats.live_photo_aligned}",
        f"  live photos, keeper lacks motion: {stats.live_photo_keeper_lacks_motion}",
        f"  live photos, loser lacks motion:  {stats.live_photo_loser_lacks_motion}",
        "",
        f"Reclaimable: {stats.reclaimable_assets} assets, {human_size(stats.reclaimable_bytes)} "
        "(originals; Immich also purges previews/thumbnails)",
        f"Albums affected: {stats.affected_albums}",
    ]
    if fuzzy_count:
        lines.append(f"Byte-different near-duplicates (review manually): {fuzzy_count}")
    if stats.pair_count == 0:
        lines.append("")
        lines.append("No cross-user duplicates found — nothing to do.")
    return "\n".join(lines)
