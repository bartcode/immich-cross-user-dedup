"""Shared data models for the dedup pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

PRIMARY = "primary"
SECONDARY = "secondary"


@dataclass(frozen=True)
class User:
    role: str  # PRIMARY or SECONDARY
    id: str
    email: str
    name: str = ""


@dataclass(frozen=True)
class AlbumRef:
    id: str
    name: str
    owner_id: str


@dataclass
class AssetInfo:
    id: str
    owner_role: str
    owner_id: str
    checksum: str  # base64 SHA-1 of the original file, as returned by the API
    type: str  # IMAGE or VIDEO
    original_file_name: str
    file_created_at: datetime | None
    file_size_bytes: int
    description: str
    is_favorite: bool
    live_photo_video_id: str | None
    # Albums containing this asset (enriched during scan; only filled for losers).
    albums: list[AlbumRef] = field(default_factory=list)

    @property
    def is_video(self) -> bool:
        return self.type == "VIDEO"


class LivePhotoCase:
    """How the motion-video situation of a duplicate pair should be handled."""

    ALIGNED = "aligned"  # both stills have motion, or both have none
    KEEPER_LACKS_MOTION = "keeper-lacks-motion"
    LOSER_LACKS_MOTION = "loser-lacks-motion"


@dataclass
class DuplicatePair:
    checksum: str
    keeper: AssetInfo
    loser: AssetInfo
    live_photo: str  # LivePhotoCase value
    # Motion-video asset ids trashed together with the loser (default policy).
    motion_ids: list[str] = field(default_factory=list)
    # Bytes reclaimed when this pair is applied (loser + motions).
    reclaimable_bytes: int = 0


@dataclass
class ScanStats:
    primary_assets: int = 0
    secondary_assets: int = 0
    pair_count: int = 0
    reclaimable_assets: int = 0  # losers + motion videos that apply would trash
    reclaimable_bytes: int = 0
    affected_albums: int = 0
    live_photo_aligned: int = 0
    live_photo_keeper_lacks_motion: int = 0
    live_photo_loser_lacks_motion: int = 0

    @property
    def reclaimable_human(self) -> str:
        return human_size(self.reclaimable_bytes)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TiB"


@dataclass
class ScanResult:
    primary: User
    secondary: User
    pairs: list[DuplicatePair]
    stats: ScanStats
    # IDs of assets that are the motion-video half of a live photo (by livePhotoVideoId).
    motion_ids: set[str] = field(default_factory=set)
    # Checksums excluded from apply by the user (session-level exclusion list).
    excluded: set[str] = field(default_factory=set)

    def eligible_pairs(self) -> list[DuplicatePair]:
        return [p for p in self.pairs if p.checksum not in self.excluded]
