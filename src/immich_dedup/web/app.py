"""FastAPI backend for the review UI.

JSON API under /api; the built frontend (web/dist) is served at / when present.
Run with: cross-user-dedup ui [--host 127.0.0.1] [--port 8642] [--token SECRET]
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from immich_dedup.core.api import ImmichApiError, ImmichClient
from immich_dedup.core.apply import ApplyOptions, apply_pairs
from immich_dedup.core.config import ConfigError, empty_config, load_config
from immich_dedup.core.journal import Journal, undo_journal
from immich_dedup.core.match import fuzzy_candidates, scan
from immich_dedup.core.models import PRIMARY, SECONDARY, AssetInfo, LivePhotoCase, ScanResult
from immich_dedup.core.preflight import run_preflight
from immich_dedup.core.report import asset_url, write_csv, write_fuzzy_csv
from immich_dedup.web.state import JobBusyError, Session

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


# -- serialization -----------------------------------------------------------


def asset_dto(asset: AssetInfo, base_url: str, *, with_albums: bool = False) -> dict[str, Any]:
    data = {
        "id": asset.id,
        "owner_role": asset.owner_role,
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
            {"id": album.id, "name": album.name, "owner_role": album.owner_id} for album in asset.albums
        ]
    return data


def pair_dto(result: ScanResult, pair, base_url: str) -> dict[str, Any]:
    return {
        "checksum": pair.checksum,
        "excluded": pair.checksum in result.excluded,
        "live_photo": pair.live_photo,
        "keeper": asset_dto(pair.keeper, base_url),
        "loser": asset_dto(pair.loser, base_url, with_albums=True),
        "reclaimable_bytes": pair.reclaimable_bytes,
    }


def stats_dto(result: ScanResult) -> dict[str, Any]:
    stats = result.stats
    return {
        "primary_email": result.primary.email,
        "secondary_email": result.secondary.email,
        "primary_assets": stats.primary_assets,
        "secondary_assets": stats.secondary_assets,
        "pair_count": stats.pair_count,
        "excluded_count": len(result.excluded),
        "eligible_count": len(result.eligible_pairs()),
        "reclaimable_assets": stats.reclaimable_assets,
        "reclaimable_bytes": stats.reclaimable_bytes,
        "affected_albums": stats.affected_albums,
        "live_photo_aligned": stats.live_photo_aligned,
        "live_photo_keeper_lacks_motion": stats.live_photo_keeper_lacks_motion,
        "live_photo_loser_lacks_motion": stats.live_photo_loser_lacks_motion,
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
        payload: dict[str, Any] = {
            "configured": session.is_configured(),
            "immich_url": session.config.immich_url,
            "primary_email": session.config.primary_email,
            "secondary_email": session.config.secondary_email,
            "primary_key_set": bool(session.config.primary_api_key),
            "secondary_key_set": bool(session.config.secondary_api_key),
            "partners_bidirectional": False,
            "checks": [],
        }
        if payload["configured"]:
            report = session.ensure_preflight()
            payload["partners_bidirectional"] = report.partners_bidirectional
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
        try:
            session.reconfigure(
                immich_url=str(body.get("immich_url", "")),
                primary_email=str(body.get("primary_email", "")),
                secondary_email=str(body.get("secondary_email", "")),
                primary_api_key=str(body.get("primary_api_key", "")),
                secondary_api_key=str(body.get("secondary_api_key", "")),
                persist=bool(body.get("persist", True)),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except JobBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return config_payload()

    @app.post("/api/scan")
    def start_scan(_: None = Depends(require_token)) -> dict[str, Any]:
        require_configured()

        def run(progress) -> dict[str, Any]:
            report = run_preflight(session.client, session.config)
            with_session_preflight(report)
            if report.failed:
                raise RuntimeError("pre-flight checks failed: " + "; ".join(
                    c.detail for c in report.checks if not c.ok
                ))
            result = scan(session.client, report.primary, report.secondary, progress=progress)
            csv_path = write_csv(result, session.reports_dir / "dedup_report.csv", session.config.immich_url)
            with_scan_result(result)
            return {"pair_count": result.stats.pair_count, "report_csv": str(csv_path)}

        def with_session_preflight(report) -> None:
            session.preflight = report

        def with_scan_result(result) -> None:
            session.scan_result = result

        try:
            return session.run_job("scan", run)
        except JobBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/job")
    def get_job(_: None = Depends(require_token)) -> dict[str, Any]:
        payload: dict[str, Any] = {"job": session.job.as_dict()}
        if session.job.running:
            return payload
        # include enough context to render step counts while idle
        result = session.scan_result
        payload["stats"] = stats_dto(result) if result else None
        payload["last_result"] = session.last_result
        return payload

    @app.get("/api/stats")
    def get_stats(_: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        return stats_dto(result)

    @app.get("/api/pairs")
    def get_pairs(
        filter: str = Query("eligible", pattern="^(all|eligible|excluded|live-photo)$"),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        _: None = Depends(require_token),
    ) -> dict[str, Any]:
        result = scan_or_404()
        pairs = result.pairs
        if filter == "eligible":
            pairs = [p for p in pairs if p.checksum not in result.excluded]
        elif filter == "excluded":
            pairs = [p for p in pairs if p.checksum in result.excluded]
        elif filter == "live-photo":
            pairs = [p for p in pairs if p.live_photo != LivePhotoCase.ALIGNED]
        base_url = session.config.immich_url
        return {
            "total": len(pairs),
            "items": [pair_dto(result, p, base_url) for p in pairs[offset : offset + limit]],
        }

    @app.get("/api/pairs/{checksum}")
    def get_pair(checksum: str, _: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        pair = next((p for p in result.pairs if p.checksum == checksum), None)
        if pair is None:
            raise HTTPException(status_code=404, detail="unknown checksum")
        return pair_dto(result, pair, session.config.immich_url)

    @app.post("/api/pairs/{checksum}/exclude")
    def exclude_pair(checksum: str, _: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        if not any(p.checksum == checksum for p in result.pairs):
            raise HTTPException(status_code=404, detail="unknown checksum")
        result.excluded.add(checksum)
        return {"checksum": checksum, "excluded": True}

    @app.post("/api/pairs/{checksum}/include")
    def include_pair(checksum: str, _: None = Depends(require_token)) -> dict[str, Any]:
        result = scan_or_404()
        result.excluded.discard(checksum)
        return {"checksum": checksum, "excluded": False}

    @app.post("/api/apply")
    def start_apply(
        body: dict[str, Any], _: None = Depends(require_token)
    ) -> dict[str, Any]:
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
                outcome = apply_pairs(session.client, result, options, journal, progress=progress)
            finally:
                journal.close()
            return {"summary": outcome.summary(), "journal": journal.path.name}

        try:
            return session.run_job("apply", run)
        except JobBusyError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/journals")
    def get_journals(_: None = Depends(require_token)) -> list[dict[str, Any]]:
        return session.journals()

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
        return {"name": name, "entries": entries, "undo_preview": undo_preview}

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
            outcome = undo_journal(session.client, journal, progress=progress)
            return {
                "restored_assets": outcome.restored_assets,
                "unrestorable": outcome.unrestorable,
                "album_rows_removed": outcome.album_rows_removed,
                "album_rows_kept": outcome.album_rows_kept,
                "metadata_restored": outcome.metadata_restored,
                "errors": outcome.errors,
            }

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
        role = _owner_role_for(session.scan_result, asset_id)
        try:
            content = session.client.get_thumbnail(role, asset_id, size=size)
        except ImmichApiError:
            # role unknown or partner permissions changed — try the other key
            fallback = SECONDARY if role == PRIMARY else PRIMARY
            try:
                content = session.client.get_thumbnail(fallback, asset_id, size=size)
            except ImmichApiError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
        return Response(content=content, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/fuzzy")
    def run_fuzzy(_: None = Depends(require_token)) -> dict[str, Any]:
        """Report-only near-duplicate pass using the current scan's raw asset data."""
        result = scan_or_404()
        from immich_dedup.core.match import _user_assets

        primary_assets = _user_assets(session.client, result.primary, None)
        secondary_assets = _user_assets(session.client, result.secondary, None)
        candidates = fuzzy_candidates(primary_assets, secondary_assets)
        path = write_fuzzy_csv(candidates, session.reports_dir / "dedup_fuzzy.csv", session.config.immich_url)
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


def _owner_role_for(result: ScanResult | None, asset_id: str) -> str:
    if result is not None:
        for pair in result.pairs:
            if pair.keeper.id == asset_id:
                return PRIMARY
            if pair.loser.id == asset_id:
                return SECONDARY
    return PRIMARY


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="cross-user-dedup-ui", description="Web UI for immich-cross-user-dedup")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument("--token", default=None, help="require this bearer token on /api requests")
    parser.add_argument("--env-file", default=None)
    args = parser.parse_args()

    try:
        config = load_config(args.env_file)
    except ConfigError:
        config = empty_config()
        print("No .env configuration found — open the UI to set the connection details in the browser.")
    client = ImmichClient(
        config.immich_url,
        config.primary_api_key,
        config.secondary_api_key,
    )
    session = Session(
        config=config,
        client=client,
        reports_dir=config.reports_dir,
        env_file=Path(args.env_file) if args.env_file else Path(".env"),
    )
    app = create_app(session, token=args.token)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
