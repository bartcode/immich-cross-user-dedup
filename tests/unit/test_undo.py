from pathlib import Path

from immich_dedup.core.apply import ApplyOptions, apply_pairs
from immich_dedup.core.journal import Journal, undo_journal
from immich_dedup.core.match import scan
from immich_dedup.core.preflight import run_preflight

from .test_scan import World


def preflight_users(world):
    report = run_preflight(world.client, world._config())
    return report.primary, report.secondary


def test_undo_round_trip_restores_pre_apply_state(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    keeper = fake.add_asset(world.p_id, "sum-1", size_bytes=100)
    loser = fake.add_asset(world.s_id, "sum-1", size_bytes=100)
    secondary_album = fake.add_album(world.s_id, "Trip", asset_ids=[loser])
    primary_album = fake.add_album(world.p_id, "Home", asset_ids=[loser])

    before = {
        "albums": {aid: fake.album_asset_ids(aid) for aid in (secondary_album, primary_album)},
        "keeper_favorite": fake.asset(keeper)["isFavorite"],
    }

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    apply_pairs(world.client, result, ApplyOptions(), journal)

    # sanity: apply did its thing
    assert keeper in fake.album_asset_ids(secondary_album)
    assert fake.asset(loser)["trashed"] is True

    undo = undo_journal(world.client, journal)

    assert undo.restored_assets == 1
    assert undo.album_rows_removed == 2
    assert fake.asset(loser)["trashed"] is False
    assert fake.album_asset_ids(secondary_album) == before["albums"][secondary_album]
    assert fake.album_asset_ids(primary_album) == before["albums"][primary_album]
    assert fake.asset(keeper)["isFavorite"] == before["keeper_favorite"]

    # a fresh scan sees the pair again
    assert scan(world.client, primary, secondary).pairs


def test_undo_restores_metadata_merges(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    keeper = fake.add_asset(world.p_id, "sum-1", is_favorite=False)
    fake.add_asset(world.s_id, "sum-1", is_favorite=True, description="note")

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    apply_pairs(world.client, result, ApplyOptions(merge_metadata=True), journal)
    assert fake.asset(keeper)["isFavorite"] is True
    assert fake.asset(keeper)["description"] == "note"

    undo = undo_journal(world.client, journal)
    assert undo.metadata_restored == 1
    assert fake.asset(keeper)["isFavorite"] is False
    assert fake.asset(keeper)["description"] == ""  # restored to keeper's original


def test_undo_keeps_album_row_when_loser_purged(tmp_path: Path):
    """If Immich already hard-deleted the loser, undo must not remove the
    keeper from the album — the album would lose the photo entirely."""
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    keeper = fake.add_asset(world.p_id, "sum-1")
    loser = fake.add_asset(world.s_id, "sum-1")
    album = fake.add_album(world.s_id, "Trip", asset_ids=[loser])

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    apply_pairs(world.client, result, ApplyOptions(), journal)

    # simulate Immich's purge job having run (asset gone from the API)
    fake.asset(loser)["deleted"] = True

    undo = undo_journal(world.client, journal)

    assert loser in undo.unrestorable
    assert undo.album_rows_kept == 1
    assert undo.album_rows_removed == 0
    assert keeper in fake.album_asset_ids(album)


def test_undo_after_manual_restore_does_not_double_restore(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    fake.add_asset(world.p_id, "sum-1")
    loser = fake.add_asset(world.s_id, "sum-1")

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    apply_pairs(world.client, result, ApplyOptions(), journal)

    # user already restored the asset manually from the trash UI
    fake.asset(loser)["trashed"] = False

    undo = undo_journal(world.client, journal)
    assert fake.asset(loser)["trashed"] is False
    assert undo.errors == []
