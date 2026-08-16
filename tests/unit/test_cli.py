import json

import pytest

import immich_dedup.cli as cli
from immich_dedup.core.config import DedupConfig

from ..fakes.immich_api import FakeImmich, make_client


@pytest.fixture
def world(tmp_path, monkeypatch):
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
        reports_dir=tmp_path / "reports",
    )
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cli, "_make_client", lambda cfg: make_client(fake, cfg.primary_api_key, cfg.secondary_api_key))
    return fake, p_id, s_id, tmp_path


def test_cli_dry_run_writes_report(world, capsys):
    fake, p_id, s_id, tmp_path = world
    fake.add_asset(p_id, "sum-1")
    fake.add_asset(s_id, "sum-1")

    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Cross-user duplicate pairs: 1" in out
    assert "Dry run only" in out
    assert (tmp_path / "reports" / "dedup_report.csv").exists()
    # nothing trashed
    assert all(not asset["trashed"] for asset in fake.assets.values())


def test_cli_apply_then_undo_round_trip(world, capsys):
    fake, p_id, s_id, tmp_path = world
    keeper = fake.add_asset(p_id, "sum-1", size_bytes=10)
    loser = fake.add_asset(s_id, "sum-1", size_bytes=10)
    album = fake.add_album(s_id, "Trip", asset_ids=[loser])

    assert cli.main(["--apply"]) == 0
    assert fake.asset(loser)["trashed"] is True
    assert keeper in fake.album_asset_ids(album)

    journals = list((tmp_path / "reports").glob("dedup_apply_*.jsonl"))
    assert len(journals) == 1
    entries = [json.loads(line) for line in journals[0].read_text().splitlines() if line]
    assert [e["op"] for e in entries] == ["run_start", "album_add", "trash", "run_end"]

    assert cli.main(["--undo", str(journals[0])]) == 0
    assert fake.asset(loser)["trashed"] is False
    assert keeper not in fake.album_asset_ids(album)
    out = capsys.readouterr().out
    assert "restored assets:     1" in out


def test_cli_missing_config_exits_with_error(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env file here
    for key in ("IMMICH_URL", "PRIMARY_EMAIL", "SECONDARY_EMAIL", "PRIMARY_API_KEY", "SECONDARY_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    code = cli.main(["--immich-url", "http://x"])
    assert code == 2
    assert "Missing required configuration" in capsys.readouterr().err


def test_cli_undo_missing_file(world):
    assert cli.main(["--undo", "/nonexistent/path.jsonl"]) == 2
