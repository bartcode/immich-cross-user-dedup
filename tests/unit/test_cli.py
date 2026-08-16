import json

import pytest

import immich_dedup.cli as cli
from immich_dedup.core.config import SecondaryCredentials

from ..fakes.world import World


@pytest.fixture
def world(tmp_path, monkeypatch):
    world = World()
    # build a fresh fake-wired client per run: the CLI closes it after each run
    from ..fakes.immich_api import make_client

    monkeypatch.setattr(cli, "load_config", lambda *a, **k: world.config)
    monkeypatch.setattr(cli, "_make_client", lambda cfg: make_client(world.fake, world.keys))
    return world


def test_cli_dry_run_writes_report(world, tmp_path, capsys):
    fake = world.fake
    fake.add_asset(world.p_id, "sum-1")
    fake.add_asset(world.s_id, "sum-1")

    assert cli.main(["--reports-dir", str(tmp_path / "reports")]) == 0
    out = capsys.readouterr().out
    assert "Cross-user duplicate groups: 1" in out
    assert "Dry run only" in out
    assert (tmp_path / "reports" / "dedup_report.csv").exists()
    # nothing trashed
    assert all(not asset["trashed"] for asset in fake.assets.values())


def test_cli_apply_then_undo_round_trip(world, tmp_path, capsys):
    fake = world.fake
    keeper = fake.add_asset(world.p_id, "sum-1", size_bytes=10)
    loser = fake.add_asset(world.s_id, "sum-1", size_bytes=10)
    album = fake.add_album(world.s_id, "Trip", asset_ids=[loser])

    assert cli.main(["--reports-dir", str(tmp_path / "reports"), "--apply"]) == 0
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
    for key in (
        "IMMICH_URL",
        "PRIMARY_EMAIL",
        "PRIMARY_API_KEY",
        "SECONDARY_EMAILS",
        "SECONDARY_API_KEYS",
        "SECONDARY_EMAIL",
        "SECONDARY_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    code = cli.main(["--immich-url", "http://x"])
    assert code == 2
    assert "at least one secondary" in capsys.readouterr().err


def test_cli_undo_missing_file(world):
    assert cli.main(["--undo", "/nonexistent/path.jsonl"]) == 2


def test_cli_secondary_flags_parse():
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "--secondary", "bob@example.com", "key-bob",
            "--secondary", "carol@example.com", "key-carol",
        ]
    )
    pairs = cli._secondary_overrides(args)
    assert pairs == [("bob@example.com", "key-bob"), ("carol@example.com", "key-carol")]


def test_cli_legacy_secondary_flags_merge():
    parser = cli.build_parser()
    args = parser.parse_args(
        ["--secondary-email", "bob@example.com", "--secondary-api-key", "key-bob"]
    )
    assert cli._secondary_overrides(args) == [("bob@example.com", "key-bob")]


def test_cli_multi_user_summary(world, tmp_path, capsys):
    from ..fakes.world import World as WorldClass

    multi = WorldClass(secondary_emails=("bob@example.com", "carol@example.com"))
    fake = multi.fake
    bob_id = multi.secondary["bob@example.com"][0]
    carol_id = multi.secondary["carol@example.com"][0]
    for owner in (multi.p_id, bob_id, carol_id):
        fake.add_asset(owner, "sum-1", size_bytes=100)
    import immich_dedup.cli as cli_module

    original_make_client = cli_module._make_client
    original_load = cli_module.load_config
    cli_module._make_client = lambda cfg: multi.client
    cli_module.load_config = lambda *a, **k: multi.config
    try:
        assert cli.main(["--reports-dir", str(tmp_path / "reports")]) == 0
    finally:
        cli_module._make_client = original_make_client
        cli_module.load_config = original_load
    out = capsys.readouterr().out
    assert "bob@example.com" in out
    assert "carol@example.com" in out
    assert multi.config.secondaries == (
        SecondaryCredentials("bob@example.com", multi.secondary["bob@example.com"][1]),
        SecondaryCredentials("carol@example.com", multi.secondary["carol@example.com"][1]),
    )
