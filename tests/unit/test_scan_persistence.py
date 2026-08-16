"""Scan persistence: results (and exclusions) survive backend restarts."""

import time

from fastapi.testclient import TestClient

from immich_dedup.web.app import create_app
from immich_dedup.web.state import Session

from ..fakes.world import World


def build(world, reports_dir):
    session = Session(
        config=world.config,
        client=world.client,
        reports_dir=reports_dir,
        env_file=reports_dir / ".env",
    )
    return TestClient(create_app(session, web_dist=None)), session


def wait_for_job(client: TestClient, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get("/api/job").json()
        if not payload["job"]["running"]:
            assert payload["job"]["error"] is None, f"job failed: {payload['job']['error']}"
            return payload
        time.sleep(0.01)
    raise TimeoutError("job did not finish")


def test_scan_survives_restart(tmp_path):
    world = World()
    world.fake.add_asset(world.p_id, "sum-1", size_bytes=100)
    loser = world.fake.add_asset(world.s_id, "sum-1", size_bytes=100)

    api, _ = build(world, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)
    assert (tmp_path / "dedup_scan.json").exists()

    # mark an exclusion, then "restart": a brand-new session over the same reports dir
    api.post("/api/pairs/sum-1/exclude")

    api2, session2 = build(world, tmp_path)
    assert session2.scan_result is not None
    stats = api2.get("/api/stats").json()
    assert stats["group_count"] == 1
    pairs = api2.get("/api/pairs?filter=excluded").json()
    assert pairs["total"] == 1  # the exclusion survived too
    assert api2.get("/api/pairs?filter=eligible").json()["total"] == 0
    # and the restored scan still resolves thumbnails via owner handles
    assert api2.get(f"/api/thumbnail/{loser}").status_code == 200


def test_stored_scan_for_other_users_is_ignored(tmp_path):
    world = World()
    world.fake.add_asset(world.p_id, "sum-1")
    world.fake.add_asset(world.s_id, "sum-1")
    api, _ = build(world, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    # a new session configured with a different secondary email must not load it
    world2 = World(secondary_emails=("someoneelse@example.com",))
    session2 = Session(
        config=world2.config,
        client=world2.client,
        reports_dir=tmp_path,
        env_file=tmp_path / ".env",
    )
    assert session2.scan_result is None
    TestClient(create_app(session2, web_dist=None))
    assert api.get("/api/pairs").status_code == 200  # old session unaffected


def test_scan_round_trip_preserves_everything(tmp_path):
    from immich_dedup.core.match import scan
    from immich_dedup.core.serialize import load_scan, save_scan

    world = World()
    fake = world.fake
    fake.add_asset(world.p_id, "sum-1", size_bytes=100)
    s_still, s_motion = fake.add_live_photo(world.s_id, "sum-1", "sum-1-m", size_bytes=100)
    primary, secondaries, users = world.users()
    result = scan(world.client, primary, secondaries, users=users)
    result.excluded.add("sum-1")

    save_scan(tmp_path / "scan.json", result, immich_url="http://x")
    loaded = load_scan(tmp_path / "scan.json")

    assert loaded.primary.email == result.primary.email
    assert loaded.stats.reclaimable_bytes == result.stats.reclaimable_bytes
    assert loaded.stats.per_user.keys() == result.stats.per_user.keys()
    group = loaded.groups[0]
    loser = next(item for item in group.losers if item.id == s_still)
    original_loser = next(item for item in result.groups[0].losers if item.id == s_still)
    assert loser.file_created_at == original_loser.file_created_at
    assert group.motion_ids[s_still] == [s_motion]
    assert loaded.excluded == {"sum-1"}
    assert s_motion in loaded.motion_ids
