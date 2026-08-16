"""Apply pass: album transfer, optional metadata merge, then trash the losers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from immich_dedup.core.api import ImmichApiError, ImmichClient
from immich_dedup.core.journal import Journal
from immich_dedup.core.models import LivePhotoCase, ScanResult

ProgressFn = Callable[[str, int, int | None], None]

MOTION_TRASH = "trash"  # trash loser's motion video together with the loser still
MOTION_SKIP = "skip"  # leave losers whose keeper lacks a motion video untouched


@dataclass
class ApplyOptions:
    merge_metadata: bool = False
    live_photo_motion: str = MOTION_TRASH
    limit: int | None = None


@dataclass
class ApplyResult:
    applied_groups: int = 0
    skipped_losers: int = 0
    albums_transferred: int = 0
    album_failures: list[str] = field(default_factory=list)
    trashed_assets: int = 0
    trashed_per_user: dict[str, int] = field(default_factory=dict)
    metadata_merges: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        per_user = ", ".join(f"{email}: {count}" for email, count in sorted(self.trashed_per_user.items()))
        lines = [
            f"Groups applied:    {self.applied_groups}",
            f"Losers skipped:    {self.skipped_losers} (live-photo motion policy)",
            f"Albums joined:     {self.albums_transferred}",
            f"Album failures:    {len(self.album_failures)}",
            f"Assets trashed:    {self.trashed_assets}" + (f" ({per_user})" if per_user else ""),
            f"Metadata merges:   {self.metadata_merges}",
        ]
        if self.errors:
            lines.append(f"Errors:            {len(self.errors)}")
        return "\n".join(lines)


def apply_groups(
    client: ImmichClient,
    result: ScanResult,
    options: ApplyOptions,
    journal: Journal,
    *,
    progress: ProgressFn | None = None,
) -> ApplyResult:
    outcome = ApplyResult()
    groups = result.eligible_groups()
    if options.limit is not None:
        groups = groups[: options.limit]
    trashed_ids: set[str] = set()

    journal.append(
        {
            "op": "run_start",
            "primary_id": result.primary.id,
            "primary_email": result.primary.email,
            "secondary_id": result.secondaries[0].id if result.secondaries else None,
            "users": [
                {"id": user.id, "email": user.email} for user in [result.primary, *result.secondaries]
            ],
            "options": {
                "merge_metadata": options.merge_metadata,
                "live_photo_motion": options.live_photo_motion,
                "limit": options.limit,
            },
        }
    )

    for index, group in enumerate(groups, start=1):
        if progress:
            progress("apply", index, len(groups))

        active_losers = [
            loser
            for loser in group.losers
            if loser.id not in trashed_ids
            and not (
                group.live_photo.get(loser.id) == LivePhotoCase.KEEPER_LACKS_MOTION
                and options.live_photo_motion == MOTION_SKIP
            )
        ]
        outcome.skipped_losers += len(group.losers) - len(active_losers)
        if not active_losers:
            continue

        _transfer_albums(client, result, group, active_losers, journal, outcome)
        if options.merge_metadata:
            _merge_metadata(client, result, group, journal, outcome)
        _trash_losers(client, result, group, active_losers, journal, outcome, trashed_ids)
        outcome.applied_groups += 1

    journal.append({"op": "run_end", "summary": {"applied_groups": outcome.applied_groups}})
    return outcome


def _transfer_albums(
    client: ImmichClient,
    result: ScanResult,
    group,
    losers: list,
    journal: Journal,
    outcome: ApplyResult,
) -> None:
    for loser in losers:
        for album in loser.albums:
            handle = result.handle_for_owner(album.owner_id)
            if handle is None:
                outcome.album_failures.append(
                    f"{album.name or album.id}: album owner is not a configured user"
                )
                journal.append(
                    {
                        "op": "album_add",
                        "album_id": album.id,
                        "album_name": album.name,
                        "album_owner_id": album.owner_id,
                        "keeper_id": group.keeper.id,
                        "loser_id": loser.id,
                        "added": False,
                        "error": "album owner not configured",
                    }
                )
                continue
            entry: dict[str, Any] = {
                "op": "album_add",
                "album_id": album.id,
                "album_name": album.name,
                "album_owner_id": album.owner_id,
                "keeper_id": group.keeper.id,
                "loser_id": loser.id,
            }
            try:
                responses = client.add_album_assets(handle, album.id, [group.keeper.id])
                response = responses[0] if responses else {"success": False, "error": "empty_response"}
                added = bool(response.get("success"))
                error = response.get("error")
            except ImmichApiError as api_error:
                added, error = False, str(api_error)
            if added:
                outcome.albums_transferred += 1
            elif error and error != "duplicate":
                outcome.album_failures.append(f"{album.name or album.id}: {error}")
            journal.append({**entry, "added": added, "error": error if error != "duplicate" else None})


def _merge_metadata(client: ImmichClient, result: ScanResult, group, journal: Journal, outcome: ApplyResult) -> None:
    keeper = group.keeper
    updates: dict[str, Any] = {}
    prev: dict[str, Any] = {}
    if any(loser.is_favorite for loser in group.losers) and not keeper.is_favorite:
        updates["isFavorite"] = True
        prev["is_favorite"] = keeper.is_favorite
    if not keeper.description:
        description = next((loser.description for loser in group.losers if loser.description), "")
        if description:
            updates["description"] = description
            prev["description"] = keeper.description
    if not updates:
        return
    try:
        client.update_asset(result.primary.email, keeper.id, **updates)
    except ImmichApiError as api_error:
        outcome.errors.append(f"metadata merge for keeper {keeper.id}: {api_error}")
        return
    journal.append({"op": "meta_merge", "asset_id": keeper.id, "prev": prev})
    outcome.metadata_merges += 1


def _trash_losers(
    client: ImmichClient,
    result: ScanResult,
    group,
    losers: list,
    journal: Journal,
    outcome: ApplyResult,
    trashed_ids: set[str],
) -> None:
    for loser in losers:
        motions = [m for m in group.motion_ids.get(loser.id, []) if m not in trashed_ids]
        ids = [loser.id, *motions]
        handle = result.handle_for_owner(loser.owner_id)
        if handle is None:
            outcome.errors.append(f"trashing loser {loser.id}: owner is not a configured user")
            continue
        try:
            responses = client.trash_assets(handle, ids)
        except ImmichApiError as api_error:
            outcome.errors.append(f"trashing loser {loser.id}: {api_error}")
            continue
        trashed_now = [r["id"] for r in responses if r.get("success")]
        if not trashed_now:
            continue
        trashed_ids.update(trashed_now)
        outcome.trashed_assets += len(trashed_now)
        outcome.trashed_per_user[loser.owner_email] = (
            outcome.trashed_per_user.get(loser.owner_email, 0) + len(trashed_now)
        )
        journal.append({"op": "trash", "owner_id": loser.owner_id, "asset_ids": trashed_now})
