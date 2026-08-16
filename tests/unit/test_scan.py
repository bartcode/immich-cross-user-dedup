from datetime import timedelta

from immich_dedup.core.match import fuzzy_candidates, scan, user_assets
from immich_dedup.core.models import LivePhotoCase
from immich_dedup.core.report import summary_text, write_csv

from ..fakes.immich_api import days_ago
from ..fakes.world import World


def test_scan_finds_exact_cross_user_groups_and_albums():
    world = World()
    fake = world.fake
    primary, secondaries, users = world.users()
    # plain duplicate pair
    p_plain = fake.add_asset(world.p_id, "sum-plain", size_bytes=100)
    s_plain = fake.add_asset(world.s_id, "sum-plain", size_bytes=100)
    # duplicate pair in an album owned by the secondary
    fake.add_asset(world.p_id, "sum-album", size_bytes=50)
    s_album = fake.add_asset(world.s_id, "sum-album", size_bytes=50)
    fake.add_album(world.s_id, "Trip", asset_ids=[s_album])
    # solo assets are not groups
    fake.add_asset(world.p_id, "sum-solo-p")
    fake.add_asset(world.s_id, "sum-solo-s")

    result = scan(world.client, primary, secondaries, users=users)

    assert result.stats.group_count == 2
    by_checksum = {group.checksum: group for group in result.groups}
    plain = by_checksum["sum-plain"]
    assert plain.keeper.id == p_plain
    assert [loser.id for loser in plain.losers] == [s_plain]
    assert plain.live_photo[s_plain] == LivePhotoCase.ALIGNED

    album_group = by_checksum["sum-album"]
    assert [album.name for album in album_group.losers[0].albums] == ["Trip"]
    assert result.stats.affected_albums == 1
    assert result.stats.reclaimable_assets == 2
    assert result.stats.reclaimable_bytes == 150


def test_scan_excludes_partner_assets_from_owner_listing():
    """The secondary's search includes the primary's assets via partner sharing;
    the scan must not treat primary assets as secondary-owned duplicates."""
    world = World()
    world.fake.add_asset(world.p_id, "only-primary")
    primary, secondaries, users = world.users()

    result = scan(world.client, primary, secondaries, users=users)
    assert result.stats.primary_assets == 1
    assert result.stats.per_user["secondary@example.com"].assets == 0
    assert result.groups == []


def test_scan_live_photo_group_counts_motion_once():
    world = World()
    fake = world.fake
    # both sides have still + motion; motions also share a checksum
    p_still, p_motion = fake.add_live_photo(world.p_id, "lp-still", "lp-motion", size_bytes=500)
    s_still, s_motion = fake.add_live_photo(world.s_id, "lp-still", "lp-motion", size_bytes=500)
    primary, secondaries, users = world.users()

    result = scan(world.client, primary, secondaries, users=users)

    assert result.stats.group_count == 2  # still group + motion group
    still_group = next(g for g in result.groups if g.keeper.id == p_still)
    assert any(g.keeper.id == p_motion for g in result.groups)
    assert still_group.live_photo[s_still] == LivePhotoCase.ALIGNED
    assert still_group.motion_ids[s_still] == [s_motion]
    # reclaimable: loser still (500) + loser motion (2000, counted once despite
    # appearing as both the still's motion and its own group)
    assert result.stats.reclaimable_assets == 2
    assert result.stats.reclaimable_bytes == 2_500
    assert s_motion in result.motion_ids


def test_scan_keeper_lacks_motion_case():
    world = World()
    fake = world.fake
    fake.add_asset(world.p_id, "x-still", size_bytes=10)
    s_still, _ = fake.add_live_photo(world.s_id, "x-still", "x-motion", size_bytes=10)
    primary, secondaries, users = world.users()

    result = scan(world.client, primary, secondaries, users=users)
    group = result.groups[0]
    assert group.live_photo[s_still] == LivePhotoCase.KEEPER_LACKS_MOTION
    assert result.stats.live_photo_keeper_lacks_motion == 1


def test_scan_skips_groups_without_primary_copy():
    world = World(secondary_emails=("bob@example.com", "carol@example.com"))
    fake = world.fake
    bob_id = world.secondary["bob@example.com"][0]
    carol_id = world.secondary["carol@example.com"][0]
    # only bob and carol have it — no primary copy
    b = fake.add_asset(bob_id, "no-primary", size_bytes=100)
    c = fake.add_asset(carol_id, "no-primary", size_bytes=100)
    # and a normal group for contrast
    fake.add_asset(world.p_id, "with-primary")
    fake.add_asset(bob_id, "with-primary")
    primary, secondaries, users = world.users()

    result = scan(world.client, primary, secondaries, users=users)

    assert result.stats.group_count == 1
    assert result.stats.skipped_no_primary == 1
    skipped = result.skipped[0]
    assert skipped.checksum == "no-primary"
    assert skipped.owner_emails == ["bob@example.com", "carol@example.com"]
    assert {b, c} == set(skipped.asset_ids)
    # per-user stats only count the eligible group's loser (bob)
    assert result.stats.per_user["bob@example.com"].trashed_files == 1
    assert result.stats.per_user["carol@example.com"].trashed_files == 0


def test_fuzzy_candidates_same_name_near_time_different_bytes():
    world = World()
    fake = world.fake
    fake.add_asset(world.p_id, "ck-a", file_name="IMG_001.jpg", created_at=days_ago(3), size_bytes=1_000_000)
    fake.add_asset(
        world.s_id,
        "ck-b",
        file_name="IMG_001.jpg",
        created_at=days_ago(3) + timedelta(seconds=1),
        size_bytes=1_005_000,
    )
    # same name but far apart in time -> not a candidate
    fake.add_asset(world.p_id, "ck-c", file_name="IMG_002.jpg", created_at=days_ago(30))
    fake.add_asset(world.s_id, "ck-d", file_name="IMG_002.jpg", created_at=days_ago(10))
    # identical checksums -> handled by exact match, not fuzzy
    fake.add_asset(world.p_id, "ck-e", file_name="IMG_003.jpg", created_at=days_ago(5))
    fake.add_asset(world.s_id, "ck-e", file_name="IMG_003.jpg", created_at=days_ago(5))
    primary, secondaries, _ = world.users()

    candidates = fuzzy_candidates(
        user_assets(world.client, primary), user_assets(world.client, secondaries[0])
    )

    assert len(candidates) == 1
    keeper, loser = candidates[0]
    assert keeper.checksum == "ck-a"
    assert loser.checksum == "ck-b"


def test_csv_and_summary(tmp_path):
    world = World()
    fake = world.fake
    fake.add_asset(world.p_id, "sum-plain", size_bytes=100)
    s = fake.add_asset(world.s_id, "sum-plain", size_bytes=100)
    primary, secondaries, users = world.users()

    result = scan(world.client, primary, secondaries, users=users)
    csv_path = write_csv(result, tmp_path / "dedup_report.csv", "http://immich.test")

    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("checksum,")
    assert "http://immich.test/photos/" + s in lines[1]
    assert "secondary@example.com" in lines[1]  # loser_owner column

    summary = summary_text(result)
    assert "Cross-user duplicate groups: 1" in summary
    assert "Reclaimable: 1 assets" in summary
