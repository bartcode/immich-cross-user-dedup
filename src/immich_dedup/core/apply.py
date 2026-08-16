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
    # owner email -> reason; losers of blocked owners are skipped for the rest
    # of the run (their album permissions failed systemically)
    blocked_owners: dict[str, str] = field(default_factory=dict)
    # losers kept because their owner was blocked (reported as one number,
    # not one error each)
    kept_blocked: int = 0
    # set when a permission error makes every remaining group hopeless; the
    # run stops instead of failing the same way thousands of times
    aborted: str | None = None

    def album_failure_reasons(self) -> dict[str, int]:
        """Aggregate album failures by their reason ('album: reason' -> reason)."""
        counts: dict[str, int] = {}
        for failure in self.album_failures:
            reason = failure.split(": ", 1)[-1]
            counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: -item[1]))

    def summary(self) -> str:
        per_user = ", ".join(f"{email}: {count}" for email, count in sorted(self.trashed_per_user.items()))
        lines = [
            f"Groups applied:    {self.applied_groups}",
            f"Losers skipped:    {self.skipped_losers} (live-photo motion policy)",
            f"Albums joined:     {self.albums_transferred}",
            f"Album failures:    {len(self.album_failures)}",
        ]
        for reason, count in self.album_failure_reasons().items():
            lines.append(f"  {count}× {reason[:120]}")
        for owner, reason in self.blocked_owners.items():
            lines.append(f"BLOCKED {owner}: {reason}")
        if self.kept_blocked:
            lines.append(f"Losers kept (blocked owner): {self.kept_blocked}")
        lines.append(
            f"Assets trashed:    {self.trashed_assets}" + (f" ({per_user})" if per_user else ""),
        )
        lines.append(f"Metadata merges:   {self.metadata_merges}")
        if self.aborted:
            lines.append(f"ABORTED:           {self.aborted}")
        if self.errors:
            lines.append(f"Errors:            {len(self.errors)}")
            for error in self.errors[:5]:
                lines.append(f"  - {error}")
            if len(self.errors) > 5:
                lines.append(f"  ... and {len(self.errors) - 5} more")
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

        safe_losers = _transfer_albums(client, result, group, active_losers, journal, outcome)
        if outcome.aborted:
            break
        if options.merge_metadata:
            _merge_metadata(client, result, group, journal, outcome)
        _trash_losers(client, result, group, active_losers, safe_losers, journal, outcome, trashed_ids)
        if outcome.aborted:
            break
        outcome.applied_groups += 1

    journal.append(
        {
            "op": "run_end",
            "summary": {"applied_groups": outcome.applied_groups, "aborted": bool(outcome.aborted)},
        }
    )
    return outcome


def _permission_denied(error: ImmichApiError) -> bool:
    """True for API-key SCOPE denials ('Missing required permission: ...').
    Other 403s (e.g. 'not an album owner or editor') are access-level and may
    be expected — e.g. the primary's pre-share add attempt — so they must not
    abort the run."""
    return error.status_code == 403 and "Missing required permission" in str(error)


def _transfer_albums(
    client: ImmichClient,
    result: ScanResult,
    group,
    losers: list,
    journal: Journal,
    outcome: ApplyResult,
) -> set[str]:
    """Add the keeper to every album containing a loser.

    Returns the loser ids that are SAFE TO TRASH: every album transfer for that
    loser succeeded (or it had no albums). Losers with failed transfers are
    kept — trashing them would remove the photo from an album without a
    replacement."""
    primary_handle = result.primary.email
    primary_id = result.primary.id
    safe_losers: set[str] = set()
    for loser in losers:
        if loser.owner_email in outcome.blocked_owners:
            # this owner's album permissions failed earlier — every remaining
            # album of theirs would fail identically, so skip without trying
            continue
        loser_ok = True
        for album in loser.albums:
            entry: dict[str, Any] = {
                "op": "album_add",
                "album_id": album.id,
                "album_name": album.name,
                "album_owner_email": result.handle_for_owner(album.owner_id) or "",
                "album_owner_id": album.owner_id,
                "keeper_id": group.keeper.id,
                "keeper_name": group.keeper.original_file_name,
                "loser_id": loser.id,
                "loser_name": loser.original_file_name,
            }

            if album.owner_id == result.primary.id:
                try:
                    added, error = _add_to_album(client, primary_handle, album.id, group.keeper.id)
                    method = "owner"
                except ImmichApiError as api_error:
                    if _permission_denied(api_error):
                        # the primary key cannot add to albums at all — every
                        # album transfer in the run would fail
                        outcome.aborted = f"permission denied joining albums — {api_error} "
                        "(check the primary API key's albumAsset.create scope)"
                    added, error, method = False, str(api_error), "owner"
            else:
                owner_handle = result.handle_for_owner(album.owner_id)
                if owner_handle is None:
                    outcome.album_failures.append(
                        f"{album.name or album.id}: album owner is not a configured user"
                    )
                    journal.append({**entry, "added": False, "error": "album owner not configured"})
                    loser_ok = False
                    continue
                added, error, method = _transfer_foreign_album(
                    client,
                    result,
                    owner_handle,
                    primary_handle,
                    primary_id,
                    album,
                    group.keeper.id,
                    group.keeper.original_file_name,
                    journal,
                    outcome,
                )

            if added:
                outcome.albums_transferred += 1
            elif error and error != "duplicate":
                outcome.album_failures.append(f"{album.name or album.id}: {error}")
                loser_ok = False  # photo would vanish from this album — keep the duplicate
            journal.append(
                {**entry, "added": added, "error": error if error != "duplicate" else None, "method": method}
            )
            if outcome.aborted:
                return safe_losers
        if loser_ok:
            safe_losers.add(loser.id)
    return safe_losers


def _add_to_album(client: ImmichClient, handle: str, album_id: str, asset_id: str) -> tuple[bool, str | None]:
    """Returns (added, per-id error). Immich reports per-asset errors (e.g.
    no_permission) with HTTP 200, so the response body must be inspected."""
    responses = client.add_album_assets(handle, album_id, [asset_id])
    response = responses[0] if responses else None
    if response is None:
        return False, "empty_response"
    return bool(response.get("success")), response.get("error")


def _transfer_foreign_album(
    client: ImmichClient,
    result: ScanResult,
    owner_handle: str,
    primary_handle: str,
    primary_id: str,
    album,
    keeper_id: str,
    keeper_name: str,
    journal: Journal,
    outcome: ApplyResult,
) -> tuple[bool, str | None, str]:
    """Transfer the keeper into an album owned by another user.

    Fast path: the owner's key adds the keeper (works when the primary shares a
    partner relationship with the owner). Otherwise: try the primary's key
    directly (the primary may already be an album editor), and as a last resort
    share the album with the primary as editor using the owner's key — journaled
    so undo revokes it — then add the keeper with the primary's key.

    A permission failure on the share step blocks the owner for the rest of the
    run (their key likely lacks albumUser.create) instead of failing once per
    album; a permission failure on the primary's own add aborts the run."""
    owner_email = result.handle_for_owner(album.owner_id) or ""
    try:
        added, error = _add_to_album(client, owner_handle, album.id, keeper_id)
        if added:
            return True, None, "owner"
    except ImmichApiError as api_error:
        return False, str(api_error), "owner"

    try:
        added, error = _add_to_album(client, primary_handle, album.id, keeper_id)
        if added:
            return True, None, "editor"
    except ImmichApiError as api_error:
        if _permission_denied(api_error):
            outcome.aborted = (
                f"permission denied joining albums — {api_error} "
                "(check the primary API key's albumAsset.create scope)"
            )
            return False, str(api_error), "editor"
        # other errors: fall through to the sharing fallback

    try:
        client.share_album_with_user(owner_handle, album.id, primary_id)
    except ImmichApiError as api_error:
        if _permission_denied(api_error):
            outcome.blocked_owners[owner_email] = (
                f"permission denied sharing albums — {api_error} "
                "(check this user's albumUser.create scope, or enable partner sharing)"
            )
            outcome.album_failures.append(f"{album.name or album.id}: {api_error}")
        return False, str(api_error), "editor"
    journal.append(
        {
            "op": "album_share",
            "album_id": album.id,
            "album_name": album.name,
            "album_owner_id": album.owner_id,
            "album_owner_email": owner_email,
            "user_id": primary_id,
            "keeper_name": keeper_name,
            "role": "editor",
        }
    )
    try:
        added, error = _add_to_album(client, primary_handle, album.id, keeper_id)
        if added:
            return True, None, "editor"
        if error and error != "duplicate":
            return False, error, "editor"
    except ImmichApiError as api_error:
        if _permission_denied(api_error):
            outcome.aborted = (
                f"permission denied joining albums — {api_error} "
                "(check the primary API key's albumAsset.create scope)"
            )
        return False, str(api_error), "editor"
    return added is True, error if error != "duplicate" else None, "editor"


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
    safe_losers: set[str],
    journal: Journal,
    outcome: ApplyResult,
    trashed_ids: set[str],
) -> None:
    for loser in losers:
        if loser.owner_email in outcome.blocked_owners:
            outcome.kept_blocked += 1
            continue
        if loser.id not in safe_losers:
            outcome.errors.append(
                f"kept duplicate {loser.original_file_name} ({loser.id}): its album transfer "
                "failed, so trashing it would remove the photo from that album"
            )
            continue
        motions = [m for m in group.motion_ids.get(loser.id, []) if m not in trashed_ids]
        ids = [loser.id, *motions]
        handle = result.handle_for_owner(loser.owner_id)
        if handle is None:
            outcome.errors.append(f"trashing loser {loser.id}: owner is not a configured user")
            continue
        try:
            responses = client.trash_assets(handle, ids)
        except ImmichApiError as api_error:
            if _permission_denied(api_error):
                # e.g. the secondary key lacks asset.delete — every remaining
                # group would fail identically, so stop the run right here
                outcome.aborted = (
                    f"permission denied trashing assets — {api_error} "
                    "(check the secondary API key scopes)"
                )
                return
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
        journal.append(
            {
                "op": "trash",
                "owner_id": loser.owner_id,
                "owner_email": loser.owner_email,
                "asset_ids": trashed_now,
                # human-readable detail for the undo preview (not used by undo logic)
                "assets": [
                    {"id": loser.id, "name": loser.original_file_name, "bytes": loser.file_size_bytes},
                    *(
                        {"id": motion_id, "name": "(motion video)", "bytes": 0}
                        for motion_id in trashed_now
                        if motion_id != loser.id
                    ),
                ],
            }
        )
