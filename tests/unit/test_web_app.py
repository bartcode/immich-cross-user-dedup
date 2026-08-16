import time

import pytest
from fastapi.testclient import TestClient

from immich_dedup.core.config import DedupConfig
from immich_dedup.web.app import create_app
from immich_dedup.web.state import Session

from ..fakes.immich_api import FakeImmich, make_client


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
def setup():
    fake = FakeImmich()
    p_id, p_key = fake.add_user("primary@example.com")
    s_id, s_key = fake.add_user("secondary@example.com")
    fake.set_partner(p_id, s_id)
    fake.set_partner(s_id, p_id)
    config = DedupConfig(
        immich_url="http://immich.test",
        primary_email="primary@example.com",
        secondary_email="secondary@example.com",
        primary_api_key=p_key,
        secondary_api_key=s_key,
    )
    client = make_client(fake, p_key, s_key)
    return fake, p_id, s_id, config, client


def build_app(fake, p_id, s_id, config, client, tmp_path, token=None):
    import pathlib

    session = Session(
        config=config,
        client=client,
        reports_dir=pathlib.Path(tmp_path),
        env_file=pathlib.Path(tmp_path) / ".env",
    )
    app = create_app(session, token=token, web_dist=None)
    return TestClient(app), session


def build_unconfigured_app(tmp_path):
    import pathlib

    from immich_dedup.core.api import ImmichClient
    from immich_dedup.core.config import empty_config

    session = Session(
        config=empty_config(reports_dir=pathlib.Path(tmp_path) / "reports"),
        client=ImmichClient("", "unset", "unset"),
        reports_dir=pathlib.Path(tmp_path) / "reports",
        env_file=pathlib.Path(tmp_path) / ".env",
    )
    app = create_app(session, web_dist=None)
    return TestClient(app), session


def seed_duplicates(fake, p_id, s_id):
    keeper = fake.add_asset(p_id, "sum-1", size_bytes=100)
    loser = fake.add_asset(s_id, "sum-1", size_bytes=100)
    album = fake.add_album(s_id, "Trip", asset_ids=[loser])
    return keeper, loser, album


def test_config_endpoint_reports_checks(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)
    payload = api.get("/api/config").json()
    assert payload["primary_email"] == "primary@example.com"
    assert payload["partners_bidirectional"] is True
    assert all(check["ok"] for check in payload["checks"])


def test_scan_stats_pairs_and_thumbnails(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    keeper, loser, album = seed_duplicates(fake, p_id, s_id)
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)

    api.post("/api/scan")
    payload = wait_for_job(api)

    stats = payload["stats"]
    assert stats["pair_count"] == 1
    assert stats["reclaimable_bytes"] == 100

    pairs = api.get("/api/pairs").json()
    assert pairs["total"] == 1
    pair = pairs["items"][0]
    assert pair["keeper"]["id"] == keeper
    assert pair["loser"]["id"] == loser
    assert pair["loser"]["albums"][0]["name"] == "Trip"
    assert pair["live_photo"] == "aligned"

    thumb = api.get(f"/api/thumbnail/{loser}")
    assert thumb.status_code == 200
    assert thumb.content == f"thumb:{loser}".encode()


def test_exclude_include_flow(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    keeper, loser, _ = seed_duplicates(fake, p_id, s_id)
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    api.post("/api/pairs/sum-1/exclude")
    assert api.get("/api/pairs?filter=eligible").json()["total"] == 0
    assert api.get("/api/pairs?filter=excluded").json()["total"] == 1

    api.post("/api/pairs/sum-1/include")
    assert api.get("/api/pairs?filter=eligible").json()["total"] == 1


def test_apply_and_undo_via_api(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    keeper, loser, album = seed_duplicates(fake, p_id, s_id)
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    api.post("/api/apply", json={"merge_metadata": False, "live_photo_motion": "trash"})
    wait_for_job(api)

    assert fake.asset(loser)["trashed"] is True
    assert keeper in fake.album_asset_ids(album)

    journals = api.get("/api/journals").json()
    assert len(journals) == 1
    name = journals[0]["name"]

    detail = api.get(f"/api/journals/{name}").json()
    assert detail["undo_preview"]["trashed_assets"] == 1

    api.post("/api/undo", json={"name": name})
    wait_for_job(api)

    assert fake.asset(loser)["trashed"] is False
    assert keeper not in fake.album_asset_ids(album)


def test_journal_name_traversal_rejected(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)
    assert api.get("/api/journals/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)
    assert api.get("/api/journals/nope.jsonl").status_code == 404


def test_scan_required_before_pairs(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)
    assert api.get("/api/pairs").status_code == 409
    assert api.post("/api/apply", json={}).status_code == 409


def test_token_auth(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path, token="s3cret")
    assert api.get("/api/config").status_code == 401
    assert api.get("/api/config", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert api.get("/api/config", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_unconfigured_app_reports_and_guards(tmp_path):
    api, session = build_unconfigured_app(tmp_path)

    payload = api.get("/api/config").json()
    assert payload["configured"] is False
    assert payload["checks"] == []

    assert api.post("/api/scan").status_code == 409
    assert api.post("/api/undo", json={"name": "x"}).status_code == 409


def wire_fake_client(session, fake):
    """Make Session.reconfigure build fake-wired clients instead of real ones."""
    original_reconfigure = session.reconfigure

    def reconfigure_with_fake(**kwargs):
        kwargs["client_factory"] = lambda cfg: make_client(fake, cfg.primary_api_key, cfg.secondary_api_key)
        return original_reconfigure(**kwargs)

    session.reconfigure = reconfigure_with_fake


def test_configure_via_api_swaps_client_and_persists(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    fake.add_asset(p_id, "sum-1")
    fake.add_asset(s_id, "sum-1")

    api, session = build_unconfigured_app(tmp_path)
    wire_fake_client(session, fake)

    response = api.post(
        "/api/config",
        json={
            "immich_url": "http://immich.test/",
            "primary_email": "Primary@Example.com",
            "secondary_email": "secondary@example.com",
            "primary_api_key": config.primary_api_key,
            "secondary_api_key": config.secondary_api_key,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["configured"] is True
    assert payload["partners_bidirectional"] is True
    assert payload["immich_url"] == "http://immich.test"  # trailing slash stripped

    # .env persisted with normalized values
    env_text = (tmp_path / ".env").read_text()
    assert "IMMICH_URL=http://immich.test" in env_text
    assert "PRIMARY_EMAIL=primary@example.com" in env_text

    # the swapped client works end to end
    api.post("/api/scan")
    wait_for_job(api)
    assert api.get("/api/pairs").json()["total"] == 1


def test_reconfigure_blank_keys_keep_existing(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    api, session = build_app(fake, p_id, s_id, config, client, tmp_path)
    wire_fake_client(session, fake)

    api.post(
        "/api/config",
        json={
            "immich_url": "http://immich.test",
            "primary_email": "primary@example.com",
            "secondary_email": "secondary@example.com",
        },
    )
    assert session.config.primary_api_key == config.primary_api_key
    assert session.config.secondary_api_key == config.secondary_api_key


def test_reconfigure_rejects_missing_fields(setup, tmp_path):
    fake, p_id, s_id, config, client = setup
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)
    response = api.post(
        "/api/config",
        json={"immich_url": "", "primary_email": "a@x.com", "secondary_email": "b@x.com"},
    )
    assert response.status_code == 400


def test_fuzzy_endpoint(setup, tmp_path):
    from datetime import timedelta

    from ..fakes.immich_api import days_ago

    fake, p_id, s_id, config, client = setup
    fake.add_asset(p_id, "ck-a", file_name="IMG_1.jpg", created_at=days_ago(3), size_bytes=1_000_000)
    fake.add_asset(
        s_id, "ck-b", file_name="IMG_1.jpg", created_at=days_ago(3) + timedelta(seconds=1), size_bytes=1_004_000
    )
    api, _ = build_app(fake, p_id, s_id, config, client, tmp_path)
    api.post("/api/scan")
    wait_for_job(api)

    payload = api.post("/api/fuzzy").json()
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["keeper"]["file_name"] == "IMG_1.jpg"
    assert item["time_delta_seconds"] == pytest.approx(1.0, abs=0.01)
