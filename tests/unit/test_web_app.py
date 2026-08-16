import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from immich_dedup.web.app import create_app
from immich_dedup.web.state import Session, build_client, unconfigured_session

from ..fakes.world import World


def wait_for_job(client: TestClient, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get("/api/job").json()
        if not payload["job"]["running"]:
            assert payload["job"]["error"] is None, f"job failed: {payload['job']['error']}"
            return payload
        time.sleep(0.01)
    raise TimeoutError("job did not finish")


@pytest.fixture
def world():
    return World()


def build_app(world, tmp_path, token=None):
    session = Session(
        config=world.config,
        client=world.client,
        reports_dir=Path(tmp_path),
        env_file=Path(tmp_path) / ".env",
    )
    app = create_app(session, token=token, web_dist=None)
    return TestClient(app), session


def seed_duplicates(world):
    keeper = world.fake.add_asset(world.p_id, "sum-1", size_bytes=100)
    loser = world.fake.add_asset(world.s_id, "sum-1", size_bytes=100)
    album = world.fake.add_album(world.s_id, "Trip", asset_ids=[loser])
    return keeper, loser, album


def test_config_endpoint_reports_checks(world, tmp_path):
    api, _ = build_app(world, tmp_path)
    payload = api.get("/api/config").json()
    assert payload["configured"] is True
    assert payload["primary_email"] == "primary@example.com"
    assert payload["secondaries"][0]["email"] == "secondary@example.com"
    assert payload["secondaries"][0]["partner_ok"] is True
    assert payload["partners_ok"] is True
    assert all(check["ok"] for check in payload["checks"])


def test_scan_groups_pairs_and_thumbnails(world, tmp_path):
    keeper, loser, _ = seed_duplicates(world)
    api, _ = build_app(world, tmp_path)

    api.post("/api/scan")
    payload = wait_for_job(api)

    stats = payload["stats"]
    assert stats["group_count"] == 1
    assert stats["reclaimable_bytes"] == 100
    assert stats["secondary_emails"] == ["secondary@example.com"]

    pairs = api.get("/api/pairs").json()
    assert pairs["total"] == 1
    group = pairs["items"][0]
    assert group["keeper"]["id"] == keeper
    assert group["losers"][0]["id"] == loser
    assert group["losers"][0]["albums"][0]["name"] == "Trip"
    assert group["losers"][0]["live_photo"] == "aligned"

    thumb = api.get(f"/api/thumbnail/{loser}")
    assert thumb.status_code == 200
    assert thumb.content == f"thumb:{loser}".encode()


def test_exclude_include_flow(world, tmp_path):
    seed_duplicates(world)
    api, _ = build_app(world, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    api.post("/api/pairs/sum-1/exclude")
    assert api.get("/api/pairs?filter=eligible").json()["total"] == 0
    assert api.get("/api/pairs?filter=excluded").json()["total"] == 1

    api.post("/api/pairs/sum-1/include")
    assert api.get("/api/pairs?filter=eligible").json()["total"] == 1


def test_apply_and_undo_via_api(world, tmp_path):
    keeper, loser, album = seed_duplicates(world)
    api, _ = build_app(world, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    api.post("/api/apply", json={"merge_metadata": False, "live_photo_motion": "trash"})
    wait_for_job(api)

    assert world.fake.asset(loser)["trashed"] is True
    assert keeper in world.fake.album_asset_ids(album)

    journals = api.get("/api/journals").json()
    assert len(journals) == 1
    name = journals[0]["name"]

    detail = api.get(f"/api/journals/{name}").json()
    assert detail["undo_preview"]["trashed_assets"] == 1

    api.post("/api/undo", json={"name": name})
    wait_for_job(api)

    assert world.fake.asset(loser)["trashed"] is False
    assert keeper not in world.fake.album_asset_ids(album)


def test_journal_name_traversal_rejected(world, tmp_path):
    api, _ = build_app(world, tmp_path)
    assert api.get("/api/journals/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    assert api.get("/api/journals/nope.jsonl").status_code == 404


def test_scan_required_before_pairs(world, tmp_path):
    api, _ = build_app(world, tmp_path)
    assert api.get("/api/pairs").status_code == 409
    assert api.post("/api/apply", json={}).status_code == 409


def test_token_auth(world, tmp_path):
    api, _ = build_app(world, tmp_path, token="s3cret")
    assert api.get("/api/config").status_code == 401
    assert api.get("/api/config", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert api.get("/api/config", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_unconfigured_app_reports_and_guards(tmp_path):
    session = unconfigured_session(reports_dir=tmp_path / "reports", env_file=tmp_path / ".env")
    api = TestClient(create_app(session, web_dist=None))

    payload = api.get("/api/config").json()
    assert payload["configured"] is False
    assert payload["checks"] == []

    assert api.post("/api/scan").status_code == 409
    assert api.post("/api/undo", json={"name": "x"}).status_code == 409


def test_configure_via_api_swaps_client_and_persists(tmp_path):
    world = World()
    world.fake.add_asset(world.p_id, "sum-1")
    world.fake.add_asset(world.s_id, "sum-1")
    session = unconfigured_session(reports_dir=tmp_path / "reports", env_file=tmp_path / ".env")
    api = TestClient(create_app(session, web_dist=None))

    # build fake-wired clients on reconfigure
    original_reconfigure = session.reconfigure

    def reconfigure_with_fake(**kwargs):
        kwargs["client_factory"] = lambda cfg: build_client(cfg, transport=world.fake.transport())
        return original_reconfigure(**kwargs)

    session.reconfigure = reconfigure_with_fake

    response = api.post(
        "/api/config",
        json={
            "immich_url": "http://immich.test/",
            "primary_email": "Primary@Example.com",
            "primary_api_key": world.config.primary_api_key,
            "secondaries": [{"email": "secondary@example.com", "api_key": world.config.secondaries[0].api_key}],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["partners_ok"] is True
    assert payload["immich_url"] == "http://immich.test"  # trailing slash stripped

    # .env persisted with the list format
    env_text = (tmp_path / ".env").read_text()
    assert "IMMICH_URL=http://immich.test" in env_text
    assert "SECONDARY_EMAILS=secondary@example.com" in env_text

    # the swapped client works end to end
    api.post("/api/scan")
    wait_for_job(api)
    assert api.get("/api/pairs").json()["total"] == 1


def wire_fake_client(session, fake):
    """Make Session.reconfigure build fake-wired clients instead of real ones."""
    original_reconfigure = session.reconfigure

    def reconfigure_with_fake(**kwargs):
        kwargs["client_factory"] = lambda cfg: build_client(cfg, transport=fake.transport())
        return original_reconfigure(**kwargs)

    session.reconfigure = reconfigure_with_fake


def test_reconfigure_can_add_and_remove_secondaries(tmp_path):
    world = World(secondary_emails=("bob@example.com", "carol@example.com"))
    session = Session(
        config=world.config,
        client=world.client,
        reports_dir=tmp_path,
        env_file=tmp_path / ".env",
    )
    wire_fake_client(session, world.fake)
    api = TestClient(create_app(session, web_dist=None))

    # drop carol, keep bob's stored key by leaving the api_key blank
    response = api.post(
        "/api/config",
        json={
            "immich_url": world.config.immich_url,
            "primary_email": world.config.primary_email,
            "secondaries": [{"email": "bob@example.com"}],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert [s["email"] for s in payload["secondaries"]] == ["bob@example.com"]
    assert session.config.secondaries[0].api_key == world.config.secondaries[0].api_key

    env_text = (tmp_path / ".env").read_text()
    assert "SECONDARY_EMAILS=bob@example.com" in env_text
    assert "carol" not in env_text


def test_reconfigure_blank_keys_keep_existing(world, tmp_path):
    api, session = build_app(world, tmp_path)
    wire_fake_client(session, world.fake)
    response = api.post(
        "/api/config",
        json={
            "immich_url": world.config.immich_url,
            "primary_email": world.config.primary_email,
            "secondaries": [{"email": "secondary@example.com"}],
        },
    )
    assert response.status_code == 200
    assert session.config.primary_api_key == world.config.primary_api_key
    assert session.config.secondaries[0].api_key == world.config.secondaries[0].api_key


def test_reconfigure_rejects_missing_fields(world, tmp_path):
    api, _ = build_app(world, tmp_path)
    response = api.post(
        "/api/config",
        json={"immich_url": "", "primary_email": "a@x.com", "secondaries": [{"email": "b@x.com"}]},
    )
    assert response.status_code == 400


def test_fuzzy_endpoint(world, tmp_path):
    from datetime import timedelta

    from ..fakes.immich_api import days_ago

    world.fake.add_asset(
        world.p_id, "ck-a", file_name="IMG_1.jpg", created_at=days_ago(3), size_bytes=1_000_000
    )
    world.fake.add_asset(
        world.s_id,
        "ck-b",
        file_name="IMG_1.jpg",
        created_at=days_ago(3) + timedelta(seconds=1),
        size_bytes=1_004_000,
    )
    api, _ = build_app(world, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    payload = api.post("/api/fuzzy").json()
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["keeper"]["file_name"] == "IMG_1.jpg"
    assert item["time_delta_seconds"] == pytest.approx(1.0, abs=0.01)


def test_cancel_endpoint_stops_a_running_job(tmp_path):
    import time

    world = World()
    api, session = build_app(world, tmp_path)

    def slow_job(progress):
        for i in range(100):
            progress("counting", i, 100)
            time.sleep(0.005)
        return {"done": True}

    session.run_job("scan", slow_job)
    time.sleep(0.05)  # let it get going
    assert api.get("/api/job").json()["job"]["running"] is True

    response = api.post("/api/job/cancel")
    assert response.status_code == 200
    payload = wait_for_job(api)
    assert payload["job"]["cancelled"] is True
    assert payload["job"]["error"] is None
    assert payload["last_result"]["cancelled"] is True

    # nothing is running afterwards; a second cancel is a 409
    assert api.post("/api/job/cancel").status_code == 409


def test_cancel_endpoint_without_job(tmp_path):
    world = World()
    api, _ = build_app(world, tmp_path)
    assert api.post("/api/job/cancel").status_code == 409


def test_pairs_sorting_by_size_and_date(world, tmp_path):
    from ..fakes.immich_api import days_ago

    fake = world.fake
    # three groups with distinct sizes and dates
    fake.add_asset(world.p_id, "small-new", size_bytes=100)
    fake.add_asset(world.s_id, "small-new", size_bytes=100)
    fake.add_asset(world.p_id, "big-old", size_bytes=900, created_at=days_ago(90))
    fake.add_asset(world.s_id, "big-old", size_bytes=900, created_at=days_ago(90))
    fake.add_asset(world.p_id, "mid-mid", size_bytes=500, created_at=days_ago(30))
    fake.add_asset(world.s_id, "mid-mid", size_bytes=500, created_at=days_ago(30))

    api, _ = build_app(world, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    by_size = api.get("/api/pairs?sort=size-desc").json()["items"]
    assert [group["checksum"] for group in by_size] == ["big-old", "mid-mid", "small-new"]

    by_size_asc = api.get("/api/pairs?sort=size-asc").json()["items"]
    assert [group["checksum"] for group in by_size_asc] == ["small-new", "mid-mid", "big-old"]

    by_date_asc = api.get("/api/pairs?sort=date-asc").json()["items"]
    assert [group["checksum"] for group in by_date_asc] == ["big-old", "mid-mid", "small-new"]

    # invalid sort is rejected
    assert api.get("/api/pairs?sort=nope").status_code == 422
