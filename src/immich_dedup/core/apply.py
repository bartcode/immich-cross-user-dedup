"""Apply pass: album transfer, optional metadata merge, then trash the losers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from immich_dedup.core.api import ImmichApiError, ImmichClient
from immich_dedup.core.journal import Journal
from immich_dedup.core.models import PRIMARY, SECONDARY, LivePhotoCase, ScanResult

ProgressFn = Callable[[str, int, int | None], None]

MOTION_TRASH = "trash"  # trash loser's motion video together with the loser still
MOTION_SKIP = "skip"  # leave pairs where the keeper lacks a motion video untouched


@dataclass
class ApplyOptions:
    merge_metadata: bool = False
    live_photo_motion: str = MOTION_TRASH
    limit: int | None = None


@dataclass
class ApplyResult:
    applied_pairs: int = 0
    skipped_pairs: int = 0
    albums_transferred: int = 0
    album_failures: list[str] = field(default_factory=list)
    trashed_assets: int = 0
    metadata_merges: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Pairs applied:    {self.applied_pairs}",
            f"Pairs skipped:    {self.skipped_pairs} (live-photo motion policy)",
            f"Albums joined:    {self.albums_transferred}",
            f"Album failures:   {len(self.album_failures)}",
            f"Assets trashed:   {self.trashed_assets}",
            f"Metadata merges:  {self.metadata_merges}",
        ]
        if self.errors:
            lines.append(f"Errors:           {len(self.errors)}")
        return "\n".join(lines)


def _role_for_owner(owner_id: str, result: ScanResult) -> str:
    if owner_id == result.primary.id:
        return PRIMARY
    return SECONDARY


def _album_display(album) -> str:
    return album.name or album.id


def apply_pairs(
    client: ImmichClient,
    result: ScanResult,
    options: ApplyOptions,
    journal: Journal,
    *,
    progress: ProgressFn | None = None,
) -> ApplyResult:
    outcome = ApplyResult()
    pairs = result.eligible_pairs()
    if options.limit is not None:
        pairs = pairs[: options.limit]
    trashed_ids: set[str] = set()

    journal.append(
        {
            "op": "run_start",
            "primary_id": result.primary.id,
            "secondary_id": result.secondary.id,
            "options": {
                "merge_metadata": options.merge_metadata,
                "live_photo_motion": options.live_photo_motion,
                "limit": options.limit,
            },
        }
    )

    for index, pair in enumerate(pairs, start=1):
        if progress:
            progress("apply", index, len(pairs))

        if pair.loser.id in trashed_ids:
            continue  # already trashed via another pair (live-photo motion unit)

        if pair.live_photo == LivePhotoCase.KEEPER_LACKS_MOTION and options.live_photo_motion == MOTION_SKIP:
            outcome.skipped_pairs += 1
            continue

        _transfer_albums(client, result, pair, journal, outcome)
        if options.merge_metadata:
            _merge_metadata(client, pair, journal, outcome)
        _trash_pair(client, pair, journal, outcome, trashed_ids)
        outcome.applied_pairs += 1

    journal.append({"op": "run_end", "summary": {"applied_pairs": outcome.applied_pairs}})
    return outcome


def _transfer_albums(client: ImmichClient, result: ScanResult, pair, journal: Journal, outcome: ApplyResult) -> None:
    for album in pair.loser.albums:
        role = _role_for_owner(album.owner_id, result)
        entry: dict[str, Any] = {
            "op": "album_add",
            "album_id": album.id,
            "album_name": album.name,
            "album_owner_id": album.owner_id,
            "keeper_id": pair.keeper.id,
            "loser_id": pair.loser.id,
        }
        try:
            responses = client.add_album_assets(role, album.id, [pair.keeper.id])
            response = responses[0] if responses else {"success": False, "error": "empty_response"}
            added = bool(response.get("success"))
            error = response.get("error")
        except ImmichApiError as api_error:
            added, error = False, str(api_error)
        if added:
            outcome.albums_transferred += 1
        elif error and error != "duplicate":
            outcome.album_failures.append(f"{_album_display(album)}: {error}")
        journal.append({**entry, "added": added, "error": error if error != "duplicate" else None})


def _merge_metadata(client: ImmichClient, pair, journal: Journal, outcome: ApplyResult) -> None:
    updates: dict[str, Any] = {}
    prev: dict[str, Any] = {}
    if pair.loser.is_favorite and not pair.keeper.is_favorite:
        updates["isFavorite"] = True
        prev["is_favorite"] = pair.keeper.is_favorite
    if pair.loser.description and not pair.keeper.description:
        updates["description"] = pair.loser.description
        prev["description"] = pair.keeper.description
    if not updates:
        return
    try:
        client.update_asset(PRIMARY, pair.keeper.id, **updates)
    except ImmichApiError as api_error:
        outcome.errors.append(f"metadata merge for keeper {pair.keeper.id}: {api_error}")
        return
    journal.append({"op": "meta_merge", "asset_id": pair.keeper.id, "prev": prev})
    outcome.metadata_merges += 1


def _trash_pair(client: ImmichClient, pair, journal: Journal, outcome: ApplyResult, trashed_ids: set[str]) -> None:
    ids = [pair.loser.id] + [m for m in pair.motion_ids if m not in trashed_ids]
    owner_id = pair.loser.owner_id
    try:
        responses = client.trash_assets(SECONDARY, ids)
    except ImmichApiError as api_error:
        outcome.errors.append(f"trashing loser {pair.loser.id}: {api_error}")
        return
    trashed_now = [r["id"] for r in responses if r.get("success")]
    if not trashed_now:
        return
    trashed_ids.update(trashed_now)
    outcome.trashed_assets += len(trashed_now)
    journal.append({"op": "trash", "owner_id": owner_id, "asset_ids": trashed_now})
