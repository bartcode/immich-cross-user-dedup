"""FastAPI backend for the review UI.

JSON API under /api; the built frontend (web/dist) is served at / when present.
Run with: cross-user-dedup-ui [--host 127.0.0.1] [--port 8642] [--token SECRET]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from immich_dedup import __version__
from immich_dedup.core.api import ImmichApiError
from immich_dedup.core.apply import ApplyOptions, apply_groups
from immich_dedup.core.config import ConfigError, load_config
from immich_dedup.core.journal import Journal, undo_journal
from immich_dedup.core.match import fuzzy_candidates, scan, user_assets
from immich_dedup.core.models import AssetInfo, LivePhotoCase, ScanResult
from immich_dedup.core.preflight import run_preflight
from immich_dedup.core.report import asset_url, write_csv, write_fuzzy_csv
from immich_dedup.web.state import JobBusyError, Session, build_client, log, unconfigured_session

# static frontend: repo-relative by default, overridable (e.g. /app/web/dist in Docker)
WEB_DIST = Path(
    os.environ.get("IMMICH_DEDUP_WEB_DIST")
    or (Path(__file__).resolve().parents[3] / "web" / "dist")
)


# -- serialization -----------------------------------------------------------


def asset_dto(asset: AssetInfo, base_url: str, *, with_albums: bool = False) -> dict[str, Any]:
    data = {
        "id": asset.id,
        "owner_email": asset.owner_email,
        "type": asset.type,
        "file_name": asset.original_file_name,
        "taken_at": asset.file_created_at.isoformat() if asset.file_created_at else None,
        "size_bytes": asset.file_size_bytes,
        "is_favorite": asset.is_favorite,
        "description": asset.description,
        "is_live_photo": asset.live_photo_video_id is not None,
        "url": asset_url(base_url, asset.id),
        "thumbnail_url": f"/api/thumbnail/{asset.id}",
    }
    if with_albums:
        data["albums"] = [
            {
                "id": album.id,
                "name": album.name,
                "owner_email": album.owner_email,
            }
            for album in asset.albums
        ]
    return data


def group_dto(result: ScanResult, group, base_url: str) -> dict[str, Any]:
    return {
        "checksum": group.checksum,
        "excluded": group.checksum in result.excluded,
        "keeper": asset_dto(group.keeper, base_url),
        "losers": [
            {
                **asset_dto(loser, base_url, with_albums=True),
                "live_photo": group.live_photo.get(loser.id, LivePhotoCase.ALIGNED),
                "reclaimable_bytes": group.loser_reclaimable.get(loser.id, loser.file_size_bytes),
            }
            for loser in group.losers
        ],
        "reclaimable_bytes": group.reclaimable_bytes,
    }


def stats_dto(result: ScanResult) -> dict[str, Any]:
    stats = result.stats
    return {
        "primary_email": result.primary.email,
        "secondary_emails": [secondary.email for secondary in result.secondaries],
        "primary_assets": stats.primary_assets,
        "group_count": stats.group_count,
        "skipped_no_primary": stats.skipped_no_primary,
        "excluded_count": len(result.excluded),
        "eligible_count": len(result.eligible_groups()),
        "reclaimable_assets": stats.reclaimable_assets,
        "reclaimable_bytes": stats.reclaimable_bytes,
        "affected_albums": stats.affected_albums,
        "live_photo_aligned": stats.live_photo_aligned,
        "live_photo_keeper_lacks_motion": stats.live_photo_keeper_lacks_motion,
        "live_photo_loser_lacks_motion": stats.live_photo_loser_lacks_motion,
        "per_user": [
            {
                "email": secondary.email,
                "assets": stats.per_user[secondary.email].assets,
                "trashed_files": stats.per_user[secondary.email].trashed_files,
                "trashed_bytes": stats.per_user[secondary.email].trashed_bytes,
            }
            for secondary in result.secondaries
            if secondary.email in stats.per_user
        ],
    }


# -- app factory -------------------------------------------------------------


def create_app(
    session: Session,
    *,
    token: str | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="immich-cross-user-dedup", docs_url=None, redoc_url=None)

    def require_token(request: Request) -> None:
        if not token:
            return
        header = request.headers.get("authorization", "")
        supplied = header.removeprefix("Bearer ").strip() or request.headers.get("x-api-token", "")
        if supplied != token:
            raise HTTPException(status_code=401, detail="invalid or missing token")

    def require_configured() -> None:
        if not session.is_configured():
            raise HTTPException(
                status_code=409, detail="not configured yet — set the connection first (POST /api/config)"
            )

    def config_payload() -> dict[str, Any]:
        configured = session.is_configured()
        payload: dict[str, Any] = {
            "version": __version__,
            "configured": configured,
            "immich_url": session.config.immich_url,
            "primary_email": session.config.primary_email,
            "primary_key_set": bool(session.config.primary_api_key),
            "secondaries": [
                {
                    "email": secondary.email,
                    "key_set": bool(secondary.api_key),
                    "partner_ok": False,
                }
                for secondary in session.config.secondaries
            ],
            "partners_ok": False,
            "checks": [],
        }
        if configured:
            report = session.ensure_preflight()
            payload["partners_ok"] = all(report.partner_status.values()) and bool(report.partner_status)
            payload["secondaries"] = [
                {
                    "email": secondary.email,
                    "key_set": True,
                    "partner_ok": report.partner_status.get(secondary.email, False),
                }
                for secondary in session.config.secondaries
            ]
            payload["checks"] = [
                {"name": check.name, "ok": check.ok, "detail": check.detail} for check in report.checks
            ]
        return payload

    def scan_or_404() -> ScanResult:
        if session.scan_result is None:
            raise HTTPException(status_code=409, detail="no scan result yet — run a scan first")
        return session.scan_result

    @app.get("/api/config")
    def get_config(_: None = Depends(require_token)) -> dict[str, Any]:
        return config_payload()

    @app.post("/api/config")
    def set_config(body: dict[str, Any], _: None = Depends(require_token)) -> dict[str, Any]:
        secondaries = [
            {"email": str(entry.get("email", "")), "api_key": str(entry.get("api_key", ""))}
            for entry in body.get("secondaries", [])
            if isinstance(entry, dict)
        ]
        try:
            session.reconfigure(
                immich_url=str(body.get("immich_url", "")),
                primary_email=str(body.get("primary_email", "")),
                primary_api_key=str(body.get("primary_api_key", "")),
                secondaries=secondaries,
                persist=bool(body.get("persist", True)),
            )
        except (ValueError, ConfigError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except JobBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return config_payload()

    @app.post("/api/scan")
    def start_scan(_: None = Depends(require_token)) -> dict[str, Any]:
        require_configured()

        def run(progress) -> dict[str, Any]:
            report = run_preflight(session.client, session.config)
            session.preflight = report
            if report.failed:
                raise RuntimeError(
                    "pre-flight checks failed: " + "; ".join(c.detail for c in report.checks if not c.ok)
                )
            result = scan(
                session.client, report.primary, report.secondaries, users=report.users, progress=progress
            )
            # re-apply exclusions the user made in earlier scans (same photos,
            # same checksums) and keep only those still present
            checksums = {group.checksum for group in result.groups}
            result.excluded |= session.load_exclusions() & checksums
            session.save_exclusions(result.excluded)
            csv_path = write_csv(
                result, session.reports_dir / "dedup_report.csv", session.config.immich_url
            )
            session.scan_result = result
            session.persist_scan()  # survive restarts (includes exclusions)
            stats = result.stats
            log(
                "scan",
                f"{stats.group_count} groups, {stats.reclaimable_assets} assets reclaimable "
                f"({stats.reclaimable_human}), skipped {stats.skipped_no_primary} without primary copy"
                f" — report: {csv_path}",
            )
            return {"group_count": result.stats.group_count, "report_csv": str(csv_path)}

        try:
            return session.run_job("scan", run)
        except JobBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/job")
    def get_job(_: None = Depends(require_token)) -> dict[str, Any]:
        # stats/last_result are always included — also while a job is running —
        # so a browser refresh never wipes the UI back to "run a scan first"
        result = session.scan_result
        return {
            "job": session.job.as_dict(),
            "stats": stats_dto(result) if result else None,
            "last_result": session.last_result,
        }

    @app.post("/api/job/cancel")
    def cancel_job(_: None = Depends(require_token)) -> dict[str, Any]:
        if not session.cancel_job():
            raise HTTPException(status_code=409, detail="no job is running")
        return {"cancelled": True}

    @app.get("/api/stats")
    def get_stats(_: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        return stats_dto(result)

    @app.get("/api/pairs")
    def get_pairs(
        filter: str = Query("eligible", pattern="^(all|eligible|excluded|live-photo)$"),
        sort: str = Query("date-desc", pattern="^(date|size)-(asc|desc)$"),
        offset: int = Query(0, ge=0),
        limit: int = Query(10, ge=1, le=200),
        _: None = Depends(require_token),
    ) -> dict[str, Any]:
        result = scan_or_404()
        groups = result.groups
        if filter == "eligible":
            groups = [g for g in groups if g.checksum not in result.excluded]
        elif filter == "excluded":
            groups = [g for g in groups if g.checksum in result.excluded]
        elif filter == "live-photo":
            groups = [
                g for g in groups if any(case != LivePhotoCase.ALIGNED for case in g.live_photo.values())
            ]

        from datetime import UTC, datetime

        def _date_key(group):
            latest = max(
                (loser.file_created_at for loser in group.losers if loser.file_created_at),
                default=datetime.min.replace(tzinfo=UTC),
            )
            return (latest, group.checksum)

        _, direction = sort.split("-")
        reverse = direction == "desc"
        key = _date_key if sort.startswith("date") else (lambda g: (g.reclaimable_bytes, g.checksum))
        groups = sorted(groups, key=key, reverse=reverse)

        base_url = session.config.immich_url
        return {
            "total": len(groups),
            "items": [group_dto(result, g, base_url) for g in groups[offset : offset + limit]],
        }

    @app.get("/api/pairs/{checksum}")
    def get_pair(checksum: str, _: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        group = next((g for g in result.groups if g.checksum == checksum), None)
        if group is None:
            raise HTTPException(status_code=404, detail="unknown checksum")
        return group_dto(result, group, session.config.immich_url)

    @app.post("/api/pairs/{checksum}/exclude")
    def exclude_pair(checksum: str, _: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        if not any(g.checksum == checksum for g in result.groups):
            raise HTTPException(status_code=404, detail="unknown checksum")
        result.excluded.add(checksum)
        session.persist_scan()
        return {"checksum": checksum, "excluded": True}

    @app.post("/api/pairs/{checksum}/include")
    def include_pair(checksum: str, _: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        result.excluded.discard(checksum)
        session.persist_scan()
        return {"checksum": checksum, "excluded": False}

    @app.post("/api/pairs/bulk")
    def bulk_pairs(body: dict[str, Any], _: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        action = body.get("action")
        checksums = [str(checksum) for checksum in body.get("checksums", [])]
        if action not in ("exclude", "include"):
            raise HTTPException(status_code=400, detail="action must be 'exclude' or 'include'")
        known = {group.checksum for group in result.groups}
        unknown = [checksum for checksum in checksums if checksum not in known]
        if unknown:
            raise HTTPException(
                status_code=404, detail=f"unknown checksums: {', '.join(unknown[:5])}"
            )
        if action == "exclude":
            result.excluded.update(checksums)
        else:
            result.excluded.difference_update(checksums)
        session.persist_scan()
        return {"changed": len(checksums), "excluded_total": len(result.excluded)}

    @app.post("/api/apply")
    def start_apply(body: dict[str, Any], _: None = Depends(require_token)) -> dict[str, Any]:
        scan_or_404()
        options = ApplyOptions(
            merge_metadata=bool(body.get("merge_metadata", False)),
            live_photo_motion=body.get("live_photo_motion", "trash"),
            limit=body.get("limit"),
        )

        def run(progress) -> dict[str, Any]:
            result = session.scan_result
            journal = Journal(session.new_journal_path())
            try:
                outcome = apply_groups(session.client, result, options, journal, progress=progress)
            finally:
                journal.close()
                # the library changed — the snapshot is stale, so clear it and
                # keep only the user's exclusions for the next scan
                session.save_exclusions(result.excluded)
                session.clear_scan()
            # one greppable line with the full outcome (failures, blocks, abort)
            log("apply", outcome.summary().replace("\n", " | "))
            return {
                "headline": (
                    f"{outcome.applied_groups} of {len(result.eligible_groups())} eligible groups "
                    f"processed, {outcome.trashed_assets} assets moved to trash"
                ),
                "status": "aborted" if outcome.aborted else "finished",
                "summary": outcome.summary(),
                "error_count": len(outcome.errors),
                "error_samples": outcome.errors[:5],
                "album_failure_reasons": outcome.album_failure_reasons(),
                "blocked_owners": dict(outcome.blocked_owners),
                "aborted": bool(outcome.aborted),
                "journal": journal.path.name,
                "note": "scan state cleared — re-scan to see what remains",
            }

        try:
            return session.run_job("apply", run)
        except JobBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/journals")
    def get_journals(_: None = Depends(require_token)) -> list[dict[str, Any]]:
        # metrics per journal up front (before Inspect): counts come straight
        # from the journal entries, so no extra API traffic
        journals = []
        for meta in session.journals():
            entries = Journal(session.reports_dir / meta["name"]).entries()
            journals.append(
                {
                    **meta,
                    "trashed_assets": sum(len(e["asset_ids"]) for e in entries if e["op"] == "trash"),
                    "album_adds": sum(1 for e in entries if e["op"] == "album_add" and e.get("added")),
                    "album_shares": sum(1 for e in entries if e["op"] == "album_share"),
                    "metadata_merges": sum(1 for e in entries if e["op"] == "meta_merge"),
                }
            )
        return journals

    @app.get("/api/journals/{name}")
    def get_journal(name: str, _: None = Depends(require_token)) -> dict[str, Any]:
        try:
            path = session.journal_path(name)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not path.exists():
            raise HTTPException(status_code=404, detail="no such journal")
        entries = Journal(path).entries()
        undo_preview = {
            "trash_entries": sum(1 for e in entries if e["op"] == "trash"),
            "trashed_assets": sum(len(e["asset_ids"]) for e in entries if e["op"] == "trash"),
            "album_adds": sum(1 for e in entries if e["op"] == "album_add" and e.get("added")),
            "metadata_merges": sum(1 for e in entries if e["op"] == "meta_merge"),
        }
        return {
            "name": name,
            "entries": entries,
            "undo_preview": undo_preview,
            "undo_detail": _undo_detail(entries, session),
        }

    def _undo_detail(entries: list[dict[str, Any]], session: Session) -> dict[str, Any]:
        """Human-readable detail about what undo would restore. Asset info comes
        from the journal's own enrichment (newer journals) with the persisted
        scan as fallback (older journals); missing entries stay anonymous."""
        scan = session.scan_result

        def from_scan(asset_id: str) -> dict[str, Any] | None:
            if scan is None:
                return None
            for group in scan.groups:
                for asset in (group.keeper, *group.losers):
                    if asset.id == asset_id:
                        return {
                            "id": asset.id,
                            "name": asset.original_file_name,
                            "owner_email": asset.owner_email,
                            "bytes": asset.file_size_bytes,
                        }
            return None

        def owner_email(entry: dict[str, Any]) -> str:
            # journals record the owner's email at write time; the persisted scan
            # (when still present) covers older journals
            recorded = entry.get("album_owner_email") or entry.get("owner_email")
            if recorded:
                return recorded
            if scan is not None and entry.get("album_owner_id") in scan.users:
                return scan.users[entry["album_owner_id"]].email
            return ""

        assets: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if entry["op"] != "trash":
                continue
            details = {detail["id"]: detail for detail in entry.get("assets", [])}
            for asset_id in entry.get("asset_ids", []):
                detail = details.get(asset_id) or from_scan(asset_id) or {"id": asset_id, "bytes": 0}
                assets.setdefault(
                    asset_id,
                    {
                        "id": asset_id,
                        "name": detail.get("name") or "unknown file",
                        "owner_email": detail.get("owner_email") or entry.get("owner_email", ""),
                        "bytes": detail.get("bytes", 0),
                    },
                )

        albums = []
        for entry in entries:
            if entry["op"] != "album_add" or not entry.get("added"):
                continue
            keeper_name = entry.get("keeper_name")
            if not keeper_name:
                keeper_name = (from_scan(entry.get("keeper_id") or "") or {}).get("name")
            albums.append(
                {
                    "album": entry.get("album_name") or entry.get("album_id"),
                    "keeper_name": keeper_name,
                    "owner_email": owner_email(entry),
                    "method": entry.get("method", "owner"),
                }
            )
        shares = [
            {
                "album": entry.get("album_name") or entry.get("album_id"),
                "owner_email": owner_email(entry),
            }
            for entry in entries
            if entry["op"] == "album_share"
        ]
        return {
            "assets": sorted(assets.values(), key=lambda asset: asset["name"]),
            "albums": albums,
            "shares": shares,
        }

    @app.post("/api/undo")
    def start_undo(body: dict[str, Any], _: None = Depends(require_token)) -> dict[str, Any]:
        require_configured()
        try:
            path = session.journal_path(body.get("name", ""))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not path.exists():
            raise HTTPException(status_code=404, detail="no such journal")

        def run(progress) -> dict[str, Any]:
            journal = Journal(path)
            users = session.ensure_preflight().users
            try:
                outcome = undo_journal(session.client, journal, users, progress=progress)
                result_payload = {
                    "restored_assets": outcome.restored_assets,
                    "unrestorable": outcome.unrestorable,
                    "album_rows_removed": outcome.album_rows_removed,
                    "album_rows_kept": outcome.album_rows_kept,
                    "metadata_restored": outcome.metadata_restored,
                    "errors": outcome.errors,
                }
            finally:
                # the library changed — the scan snapshot no longer matches it
                session.clear_scan()
            log(
                "undo",
                f"restored {result_payload['restored_assets']} assets, "
                f"removed {result_payload['album_rows_removed']} album rows, "
                f"reverted {result_payload['metadata_restored']} merges, "
                f"{len(result_payload['errors'])} errors"
                f" — journal: {path.name}",
            )
            return result_payload

        try:
            return session.run_job("undo", run)
        except JobBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/thumbnail/{asset_id}")
    def get_thumbnail(
        asset_id: str,
        size: str = Query("preview", pattern="^(thumbnail|preview|fullsize)$"),
        _: None = Depends(require_token),
    ) -> Response:
        result = session.scan_result
        handle = _owner_handle(result, asset_id) or session.config.primary_email

        def fetch(with_handle: str) -> Response:
            upstream = session.client.get_thumbnail_response(with_handle, asset_id, size=size)
            return Response(
                content=upstream.content,
                media_type=upstream.headers.get("content-type", "image/jpeg"),
                headers={"Cache-Control": "private, max-age=3600"},
            )

        try:
            return fetch(handle)
        except ImmichApiError:
            for fallback in session.client.handles:
                if fallback == handle:
                    continue
                try:
                    return fetch(fallback)
                except ImmichApiError:
                    continue
            raise HTTPException(status_code=404, detail="thumbnail not available") from None

    @app.post("/api/fuzzy")
    def run_fuzzy(_: None = Depends(require_token)) -> dict[str, Any]:
        """Report-only near-duplicate pass using the current scan's raw asset data."""
        require_configured()
        result = scan_or_404()
        primary_assets = user_assets(session.client, result.primary)
        all_secondary_assets = [
            asset for secondary in result.secondaries for asset in user_assets(session.client, secondary)
        ]
        candidates = fuzzy_candidates(primary_assets, all_secondary_assets)
        path = write_fuzzy_csv(
            candidates, session.reports_dir / "dedup_fuzzy.csv", session.config.immich_url
        )
        base_url = session.config.immich_url
        return {
            "count": len(candidates),
            "csv": str(path),
            "items": [
                {
                    "keeper": asset_dto(keeper_asset, base_url),
                    "loser": asset_dto(loser_asset, base_url),
                    "time_delta_seconds": abs(
                        (keeper_asset.file_created_at - loser_asset.file_created_at).total_seconds()
                    ),
                }
                for keeper_asset, loser_asset in candidates[:200]
            ],
        }

    @app.exception_handler(JobBusyError)
    async def busy_handler(_: Request, error: JobBusyError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    dist = web_dist if web_dist is not None else WEB_DIST
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    else:

        @app.get("/")
        def index() -> dict[str, str]:
            return {"hint": "frontend not built — run `npm run build` in web/"}

    return app


def _owner_handle(result: ScanResult | None, asset_id: str) -> str | None:
    if result is not None:
        for group in result.groups:
            if group.keeper.id == asset_id:
                return group.keeper.owner_email
            for loser in group.losers:
                if loser.id == asset_id:
                    return loser.owner_email
    return None


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="cross-user-dedup-ui", description="Web UI for immich-cross-user-dedup")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument("--token", default=None, help="require this bearer token on /api requests")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()
    print(f"immich-cross-user-dedup {__version__} — http://{args.host}:{args.port}")

    try:
        config = load_config(args.env_file)
    except ConfigError:
        config = None
        print("No .env configuration found — open the UI to set the connection details in the browser.")
    if config is not None:
        session = Session(
            config=config,
            client=build_client(config),
            reports_dir=config.reports_dir,
            env_file=Path(args.env_file) if args.env_file else Path(".env"),
        )
    else:
        session = unconfigured_session(
            reports_dir=Path("reports"),
            env_file=Path(args.env_file) if args.env_file else Path(".env"),
        )
    app = create_app(session, token=args.token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
