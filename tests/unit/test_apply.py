from pathlib import Path

from immich_dedup.core.apply import ApplyOptions, apply_pairs
from immich_dedup.core.journal import Journal
from immich_dedup.core.match import scan
from immich_dedup.core.preflight import run_preflight

from .test_scan import World


def preflight_users(world):
    report = run_preflight(world.client, world._config())
    return report.primary, report.secondary


def test_apply_transfers_albums_and_trashes_loser(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    keeper = fake.add_asset(world.p_id, "sum-1", size_bytes=100)
    loser = fake.add_asset(world.s_id, "sum-1", size_bytes=100)
    album = fake.add_album(world.s_id, "Trip", asset_ids=[loser])
    fake.add_album(world.p_id, "Primary album")  # unrelated album stays untouched

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "journal.jsonl")
    outcome = apply_pairs(world.client, result, ApplyOptions(), journal)
    journal.close()

    assert outcome.applied_pairs == 1
    assert outcome.albums_transferred == 1
    assert outcome.trashed_assets == 1
    assert fake.asset(loser)["trashed"] is True
    assert fake.asset(keeper)["trashed"] is False
    # keeper now in the album that contained the loser; loser still in it too
    assert keeper in fake.album_asset_ids(album)
    assert loser in fake.album_asset_ids(album)

    entries = journal.entries()
    assert [e["op"] for e in entries] == ["run_start", "album_add", "trash", "run_end"]


def test_apply_respects_limit(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    for i in range(5):
        fake.add_asset(world.p_id, f"sum-{i}")
        fake.add_asset(world.s_id, f"sum-{i}")

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_pairs(world.client, result, ApplyOptions(limit=2), journal)
    journal.close()
    assert outcome.applied_pairs == 2
    assert outcome.trashed_assets == 2


def test_apply_excluded_pairs_untouched(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    fake.add_asset(world.p_id, "keep-me")
    loser = fake.add_asset(world.s_id, "keep-me")
    fake.add_asset(world.p_id, "dedupe-me")
    fake.add_asset(world.s_id, "dedupe-me")

    result = scan(world.client, primary, secondary)
    result.excluded.add("keep-me")
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_pairs(world.client, result, ApplyOptions(), journal)
    journal.close()

    assert outcome.applied_pairs == 1
    assert fake.asset(loser)["trashed"] is False


def test_apply_live_photo_motion_policy_skip(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    # keeper still has no motion; loser is a live photo
    fake.add_asset(world.p_id, "lp")
    loser_still, loser_motion = fake.add_live_photo(world.s_id, "lp", "lp-m")

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_pairs(world.client, result, ApplyOptions(live_photo_motion="skip"), journal)
    journal.close()

    assert outcome.applied_pairs == 0
    assert outcome.skipped_pairs == 1
    assert fake.asset(loser_still)["trashed"] is False
    assert fake.asset(loser_motion)["trashed"] is False


def test_apply_live_photo_motion_trashed_with_loser(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    fake.add_asset(world.p_id, "lp")
    loser_still, loser_motion = fake.add_live_photo(world.s_id, "lp", "lp-m")

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_pairs(world.client, result, ApplyOptions(), journal)
    journal.close()

    assert outcome.applied_pairs == 1
    assert fake.asset(loser_still)["trashed"] is True
    assert fake.asset(loser_motion)["trashed"] is True
    assert outcome.trashed_assets == 2


def test_apply_merge_metadata(tmp_path: Path):
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    keeper = fake.add_asset(world.p_id, "sum-1")
    fake.add_asset(world.s_id, "sum-1", is_favorite=True, description="our trip")

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_pairs(world.client, result, ApplyOptions(merge_metadata=True), journal)
    journal.close()

    assert outcome.metadata_merges == 1
    assert fake.asset(keeper)["isFavorite"] is True
    assert fake.asset(keeper)["description"] == "our trip"


def test_apply_is_idempotent_on_rerun(tmp_path: Path):
    """After apply, a fresh scan finds no pairs (trashed assets drop out of
    search), so re-applying does nothing."""
    world = World()
    fake = world.fake
    primary, secondary = preflight_users(world)
    fake.add_asset(world.p_id, "sum-1")
    fake.add_asset(world.s_id, "sum-1")

    first = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    apply_pairs(world.client, first, ApplyOptions(), journal)
    journal.close()

    second = scan(world.client, primary, secondary)
    assert second.pairs == []
    journal2 = Journal(tmp_path / "j2.jsonl")
    outcome = apply_pairs(world.client, second, ApplyOptions(), journal2)
    journal2.close()
    assert outcome.applied_pairs == 0


def test_apply_records_album_failure_without_aborting(tmp_path: Path):
    """No partner sharing -> cross-user album add fails, recorded, run continues."""
    world = World()
    fake = world.fake
    fake.partners.clear()  # drop partner sharing
    primary, secondary = preflight_users(world)
    keeper = fake.add_asset(world.p_id, "sum-1")
    loser = fake.add_asset(world.s_id, "sum-1")
    album = fake.add_album(world.s_id, "Trip", asset_ids=[loser])

    result = scan(world.client, primary, secondary)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_pairs(world.client, result, ApplyOptions(), journal)
    journal.close()

    assert outcome.applied_pairs == 1  # pair still processed
    assert outcome.album_failures  # failure recorded
    assert keeper not in fake.album_asset_ids(album)
    assert fake.asset(loser)["trashed"] is True  # trash still happened
