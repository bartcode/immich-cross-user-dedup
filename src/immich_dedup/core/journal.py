"""Append-only JSONL action journal and its reverse-replay (undo).

Every mutation the apply pass performs is journaled immediately after the API
call succeeds (or with its error if it failed). The undo pass replays the
journal in reverse to restore the pre-run state while Immich has not yet purged
the trash.

Entry shapes::

    {"op": "run_start", "ts", "primary_id", "primary_email", "secondary_id",
     "users": [{"id", "email"}, ...], "options": {...}}
    {"op": "album_add", "ts", "album_id", "album_name", "album_owner_id",
     "keeper_id", "loser_id", "added": bool, "error": str | null}
    {"op": "meta_merge", "ts", "asset_id", "prev": {"is_favorite": ..., "description": ...}}
    {"op": "trash", "ts", "owner_id", "asset_ids": [...]}
    {"op": "run_end", "ts", "summary": {...}}
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from immich_dedup.core.api import ImmichApiError, ImmichClient
from immich_dedup.core.models import User


class Journal:
    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def append(self, entry: dict[str, Any]) -> None:
        record = {"ts": dt.datetime.now(dt.UTC).isoformat(), **entry}
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a")
        self._handle.write(json.dumps(record) + "\n")
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def entries(self) -> list[dict[str, Any]]:
        with self.path.open() as handle:
            return [json.loads(line) for line in handle if line.strip()]


@dataclass
class UndoResult:
    restored_assets: int = 0
    unrestorable: list[str] = field(default_factory=list)
    album_rows_removed: int = 0
    album_rows_kept: int = 0  # kept because the paired loser could not be restored
    metadata_restored: int = 0
    errors: list[str] = field(default_factory=list)


def undo_journal(
    client: ImmichClient,
    journal: Journal,
    users: dict[str, User] | None = None,
    progress=None,
) -> UndoResult:
    """Reverse-replay a journal. Requires Immich not to have purged the trash
    yet; assets that no longer exist are reported as unrestorable.

    ``users`` is the current pre-flight registry (user_id -> User). It resolves
    API-client handles for journals written before the multi-user format
    (legacy primary_id/secondary_id headers without emails)."""
    result = UndoResult()
    entries = journal.entries()

    header = next((e for e in entries if e["op"] == "run_start"), None)
    if header is None:
        raise ValueError("journal has no run_start header")

    handles: dict[str, str] = {}  # user_id -> email handle
    unresolved: list[str] = []
    for entry_user in header.get("users", []):
        handles[entry_user["id"]] = entry_user["email"]
    for legacy_key in ("primary_id", "secondary_id"):
        legacy_id = header.get(legacy_key)
        if legacy_id and legacy_id not in handles:
            user = (users or {}).get(legacy_id)
            if user:
                handles[legacy_id] = user.email
            else:
                unresolved.append(legacy_id)

    def handle_for(user_id: str) -> str | None:
        handle = handles.get(user_id)
        if handle is None:
            result.errors.append(f"no API key available for user {user_id} in this journal")
        return handle

    # Phase 1 — un-trash assets (reverse order), tracking which losers came back.
    loser_state: dict[str, str] = {}  # asset id -> 'restored' | 'active' | 'gone'
    trash_entries = [e for e in entries if e["op"] == "trash"]
    for index, entry in enumerate(reversed(trash_entries), start=1):
        if progress:
            progress("restore", index, len(trash_entries))
        handle = handle_for(entry["owner_id"])
        if handle is None:
            continue
        ids = entry["asset_ids"]
        restored = client.restore_assets(handle, ids).get("count", 0)
        result.restored_assets += int(restored)
        for asset_id in ids:
            loser_state[asset_id] = "unknown"

    # Phase 2 — remove keeper-from-album rows the run added, but only when the
    # paired loser is back (otherwise the album would lose the photo entirely).
    def loser_available(loser_id: str) -> bool:
        state = loser_state.get(loser_id)
        if state in ("restored", "active"):
            return True
        if state == "gone":
            return False
        owner_handle = handle_for(_owner_of_loser(loser_id, entries))
        if owner_handle is None:
            return False
        try:
            asset = client.get_asset(owner_handle, loser_id)
        except ImmichApiError as error:
            if error.status_code == 404:
                loser_state[loser_id] = "gone"
                result.unrestorable.append(loser_id)
                return False
            result.errors.append(f"lookup {loser_id}: {error}")
            return False
        state = "active" if not asset.get("trashed") else "restored"
        loser_state[loser_id] = state
        return True

    album_entries = [e for e in entries if e["op"] == "album_add" and e.get("added")]
    primary_handle = handles.get(header.get("primary_id"))
    for index, entry in enumerate(reversed(album_entries), start=1):
        if progress:
            progress("albums", index, len(album_entries))
        if not loser_available(entry["loser_id"]):
            result.album_rows_kept += 1
            continue
        # Removal must mirror how the row was added: "owner" rows went in with
        # the album owner's key (partner fast path), "editor" rows with the
        # primary's key after the album-editor fallback — only the actor who
        # passed AssetShare at add time can remove the keeper later.
        if entry.get("method") == "editor":
            handle = primary_handle
        else:
            handle = handle_for(entry.get("album_owner_id"))
        if handle is None:
            continue
        try:
            responses = client.remove_album_assets(handle, entry["album_id"], [entry["keeper_id"]])
            removed = any(response.get("success") for response in responses)
            if removed:
                result.album_rows_removed += 1
            else:
                result.errors.append(
                    f"album {entry.get('album_name')}: keeper row not removed "
                    f"({responses[0].get('error') if responses else 'empty response'})"
                )
        except ImmichApiError as error:
            result.errors.append(f"album {entry.get('album_name')} remove keeper: {error}")

    # Phase 2.5 — revoke album shares the run created (album-editor fallback).
    share_entries = [e for e in entries if e["op"] == "album_share"]
    for index, entry in enumerate(reversed(share_entries), start=1):
        if progress:
            progress("shares", index, len(share_entries))
        handle = handle_for(entry.get("album_owner_id"))
        if handle is None:
            continue
        try:
            client.remove_album_user(handle, entry["album_id"], entry["user_id"])
        except ImmichApiError as error:
            result.errors.append(f"album {entry.get('album_name')} revoke share: {error}")

    # Phase 3 — restore merged metadata (keeper always belongs to the primary).
    meta_entries = [e for e in entries if e["op"] == "meta_merge"]
    for entry in reversed(meta_entries):
        prev = entry.get("prev", {})
        fields: dict[str, Any] = {}
        if "is_favorite" in prev:
            fields["isFavorite"] = prev["is_favorite"]
        if "description" in prev:
            fields["description"] = prev["description"]
        if not fields:
            continue
        primary_handle = handles.get(header.get("primary_id"))
        if primary_handle is None:
            result.errors.append(f"metadata restore {entry['asset_id']}: primary user unresolved")
            continue
        try:
            client.update_asset(primary_handle, entry["asset_id"], **fields)
            result.metadata_restored += 1
        except ImmichApiError as error:
            result.errors.append(f"metadata restore {entry['asset_id']}: {error}")

    return result


def _owner_of_loser(loser_id: str, entries: list[dict[str, Any]]) -> str:
    for entry in entries:
        if entry["op"] == "trash" and loser_id in entry["asset_ids"]:
            return entry["owner_id"]
    return ""
