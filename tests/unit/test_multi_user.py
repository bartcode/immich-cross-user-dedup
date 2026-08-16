"""End-to-end scenarios with three users: alice (primary), bob and carol."""

from pathlib import Path

from immich_dedup.core.apply import ApplyOptions, apply_groups
from immich_dedup.core.journal import Journal, undo_journal
from immich_dedup.core.match import scan

from ..fakes.world import World

BOB = "bob@example.com"
CAROL = "carol@example.com"


def make_world() -> tuple[World, str, str]:
    world = World(secondary_emails=(BOB, CAROL))
    return world, world.secondary[BOB][0], world.secondary[CAROL][0]


def test_three_user_group_trashes_one_loser_per_user(tmp_path: Path):
    world, bob_id, carol_id = make_world()
    fake = world.fake
    primary, secondaries, users = world.users()
    # everyone imported the same photo
    keeper = fake.add_asset(world.p_id, "trip", size_bytes=100)
    bob_copy = fake.add_asset(bob_id, "trip", size_bytes=100)
    carol_copy = fake.add_asset(carol_id, "trip", size_bytes=100)
    # each secondary has it in their own album
    bob_album = fake.add_album(bob_id, "Bob trip", asset_ids=[bob_copy])
    carol_album = fake.add_album(carol_id, "Carol trip", asset_ids=[carol_copy])

    result = scan(world.client, primary, secondaries, users=users)
    assert result.stats.group_count == 1
    group = result.groups[0]
    assert group.keeper.id == keeper
    assert {loser.id for loser in group.losers} == {bob_copy, carol_copy}
    assert result.stats.reclaimable_assets == 2
    assert result.stats.per_user[BOB].trashed_files == 1
    assert result.stats.per_user[CAROL].trashed_files == 1

    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_groups(world.client, result, ApplyOptions(), journal)
    journal.close()

    assert outcome.applied_groups == 1
    assert outcome.trashed_assets == 2
    assert outcome.trashed_per_user == {BOB: 1, CAROL: 1}
    assert fake.asset(bob_copy)["trashed"] is True
    assert fake.asset(carol_copy)["trashed"] is True
    assert fake.asset(keeper)["trashed"] is False
    # keeper joined both albums
    assert keeper in fake.album_asset_ids(bob_album)
    assert keeper in fake.album_asset_ids(carol_album)

    # undo restores everything
    undo = undo_journal(world.client, journal)
    assert undo.restored_assets == 2
    assert undo.album_rows_removed == 2
    assert fake.asset(bob_copy)["trashed"] is False
    assert fake.asset(carol_copy)["trashed"] is False
    assert keeper not in fake.album_asset_ids(bob_album)
    assert keeper not in fake.album_asset_ids(carol_album)


def test_album_owned_by_third_secondary_is_discovered(tmp_path: Path):
    """Bob's copy sits in an album owned by Carol — the union-of-keys album
    discovery must find it even though neither the loser's nor the primary's
    key alone sees it."""
    world, bob_id, carol_id = make_world()
    fake = world.fake
    primary, secondaries, users = world.users()
    keeper = fake.add_asset(world.p_id, "group-1", size_bytes=10)
    bob_copy = fake.add_asset(bob_id, "group-1", size_bytes=10)
    carol_album = fake.add_album(carol_id, "Carol only", asset_ids=[bob_copy])

    result = scan(world.client, primary, secondaries, users=users)
    loser_albums = [album.id for loser in result.groups[0].losers for album in loser.albums]
    assert carol_album in loser_albums

    journal = Journal(tmp_path / "j.jsonl")
    apply_groups(world.client, result, ApplyOptions(), journal)
    # carol (album owner, partner of primary) adds the keeper to her album
    assert keeper in fake.album_asset_ids(carol_album)


def test_album_transfer_fails_only_without_albumuser_scope(tmp_path: Path):
    """Carol has neither partner sharing nor the albumUser.create scope: her
    album transfer fails and is recorded — everything else proceeds."""
    world, bob_id, carol_id = make_world()
    fake = world.fake
    # drop both partner directions with carol AND restrict her key so the
    # editor-sharing fallback is impossible
    fake.partners.pop((world.p_id, carol_id))
    fake.partners.pop((carol_id, world.p_id))
    restricted_key = fake.add_api_key(
        carol_id,
        permissions=[
            "user.read", "partner.read", "asset.read", "asset.view", "album.read",
            "albumAsset.create", "asset.delete",
        ],  # no albumUser.create
    )
    world.client.close()
    from ..fakes.immich_api import make_client

    keys = dict(world.keys)
    keys[CAROL] = restricted_key
    world.client = make_client(fake, keys)
    primary, secondaries, users = world.users(strict=False)
    keeper = fake.add_asset(world.p_id, "group-1", size_bytes=10)
    bob_copy = fake.add_asset(bob_id, "group-1", size_bytes=10)
    carol_copy = fake.add_asset(carol_id, "group-1", size_bytes=10)
    bob_album = fake.add_album(bob_id, "Bob album", asset_ids=[bob_copy])
    carol_album = fake.add_album(carol_id, "Carol album", asset_ids=[carol_copy])

    result = scan(world.client, primary, secondaries, users=users)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_groups(world.client, result, ApplyOptions(), journal)
    journal.close()

    # bob's transfer works (editor fallback), carol's fails with a permission error
    assert keeper in fake.album_asset_ids(bob_album)
    assert keeper not in fake.album_asset_ids(carol_album)
    assert any("Carol album" in failure for failure in outcome.album_failures)
    # both losers still trashed
    assert fake.asset(bob_copy)["trashed"] is True
    assert fake.asset(carol_copy)["trashed"] is True


def test_secondary_only_group_skipped_and_untouched(tmp_path: Path):
    world, bob_id, carol_id = make_world()
    fake = world.fake
    fake.add_asset(bob_id, "shared-2")
    carol_copy = fake.add_asset(carol_id, "shared-2")
    primary, secondaries, users = world.users()

    result = scan(world.client, primary, secondaries, users=users)
    assert result.groups == []
    assert result.stats.skipped_no_primary == 1

    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_groups(world.client, result, ApplyOptions(), journal)
    journal.close()
    assert outcome.applied_groups == 0
    assert fake.asset(carol_copy)["trashed"] is False


def test_live_photo_motion_policy_skips_only_affected_loser(tmp_path: Path):
    """Keeper lacks motion; bob's copy has motion (skipped), carol's has none
    (still trashed)."""
    world, bob_id, carol_id = make_world()
    fake = world.fake
    primary, secondaries, users = world.users()
    fake.add_asset(world.p_id, "lp", size_bytes=10)
    bob_still, bob_motion = fake.add_live_photo(bob_id, "lp", "lp-m", size_bytes=10)
    carol_still = fake.add_asset(carol_id, "lp", size_bytes=10)

    result = scan(world.client, primary, secondaries, users=users)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_groups(world.client, result, ApplyOptions(live_photo_motion="skip"), journal)
    journal.close()

    assert outcome.applied_groups == 1  # group applied for carol's loser
    assert outcome.skipped_losers == 1  # bob's loser skipped
    assert fake.asset(bob_still)["trashed"] is False
    assert fake.asset(bob_motion)["trashed"] is False
    assert fake.asset(carol_still)["trashed"] is True
