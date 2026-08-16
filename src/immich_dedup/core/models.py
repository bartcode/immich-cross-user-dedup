"""Shared data models for the dedup pipeline (1 primary + N secondaries)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class User:
    """A participating user; the email doubles as the API-client handle."""

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
    owner_id: str
    owner_email: str
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
    """How the motion-video situation of a keeper/loser pair should be handled."""

    ALIGNED = "aligned"  # both stills have motion, or both have none
    KEEPER_LACKS_MOTION = "keeper-lacks-motion"
    LOSER_LACKS_MOTION = "loser-lacks-motion"


@dataclass
class DuplicateGroup:
    """One keeper (owned by the primary) plus every other user's copy."""

    checksum: str
    keeper: AssetInfo
    losers: list[AssetInfo]
    # loser asset id -> LivePhotoCase value
    live_photo: dict[str, str] = field(default_factory=dict)
    # loser asset id -> motion video ids trashed together with that loser
    motion_ids: dict[str, list[str]] = field(default_factory=dict)
    # loser asset id -> bytes reclaimed when that loser is applied
    loser_reclaimable: dict[str, int] = field(default_factory=dict)
    # Bytes reclaimed when this group is applied (losers + motions).
    reclaimable_bytes: int = 0


@dataclass
class SkippedGroup:
    """A duplicate group the primary never imported — reported, not touched."""

    checksum: str
    owner_emails: list[str]
    asset_ids: list[str]


@dataclass
class UserStats:
    assets: int = 0
    trashed_files: int = 0
    trashed_bytes: int = 0


@dataclass
class ScanStats:
    primary_assets: int = 0
    group_count: int = 0
    skipped_no_primary: int = 0
    reclaimable_assets: int = 0  # losers + motion videos that apply would trash
    reclaimable_bytes: int = 0
    affected_albums: int = 0
    live_photo_aligned: int = 0
    live_photo_keeper_lacks_motion: int = 0
    live_photo_loser_lacks_motion: int = 0
    # secondary email -> per-user view of the dedup (library size, what apply trashes)
    per_user: dict[str, UserStats] = field(default_factory=dict)

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
    secondaries: list[User]
    groups: list[DuplicateGroup]
    stats: ScanStats
    # user_id -> User registry covering primary + secondaries
    users: dict[str, User] = field(default_factory=dict)
    skipped: list[SkippedGroup] = field(default_factory=list)
    # IDs of assets that are the motion-video half of a live photo (by livePhotoVideoId).
    motion_ids: set[str] = field(default_factory=set)
    # Checksums excluded from apply by the user (session-level exclusion list).
    excluded: set[str] = field(default_factory=set)

    def eligible_groups(self) -> list[DuplicateGroup]:
        return [group for group in self.groups if group.checksum not in self.excluded]

    def handle_for_owner(self, owner_id: str) -> str | None:
        """Email handle for the API client that owns this user id."""
        user = self.users.get(owner_id)
        return user.email if user else None
