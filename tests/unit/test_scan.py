from datetime import timedelta

from immich_dedup.core.match import fuzzy_candidates, scan
from immich_dedup.core.models import LivePhotoCase
from immich_dedup.core.preflight import run_preflight
from immich_dedup.core.report import summary_text, write_csv

from ..fakes.immich_api import FakeImmich, days_ago, make_client


class World:
    """Common fixture: two users with partner sharing and a few duplicate pairs."""

    def __init__(self):
        self.fake = FakeImmich()
        self.p_id, self.p_key = self.fake.add_user("primary@example.com")
        self.s_id, self.s_key = self.fake.add_user("secondary@example.com")
        self.fake.set_partner(self.p_id, self.s_id)
        self.fake.set_partner(self.s_id, self.p_id)
        self.client = make_client(self.fake, self.p_key, self.s_key)
        report = run_preflight(self.client, self._config())
        assert not report.failed
        self.primary, self.secondary = report.primary, report.secondary

    def _config(self):
        from immich_dedup.core.config import DedupConfig

        return DedupConfig(
            immich_url="http://immich.test",
            primary_email="primary@example.com",
            secondary_email="secondary@example.com",
            primary_api_key="p",
            secondary_api_key="s",
        )


def test_scan_finds_exact_cross_user_pairs_and_albums():
    world = World()
    fake = world.fake
    # plain duplicate pair
    p_plain = fake.add_asset(world.p_id, "sum-plain", size_bytes=100)
    s_plain = fake.add_asset(world.s_id, "sum-plain", size_bytes=100)
    # duplicate pair in an album owned by secondary
    fake.add_asset(world.p_id, "sum-album", size_bytes=50)
    s_album = fake.add_asset(world.s_id, "sum-album", size_bytes=50)
    fake.add_album(world.s_id, "Trip", asset_ids=[s_album])
    # primary-only and secondary-only assets are not pairs
    fake.add_asset(world.p_id, "sum-solo-p")
    fake.add_asset(world.s_id, "sum-solo-s")

    result = scan(world.client, world.primary, world.secondary)

    assert result.stats.pair_count == 2
    by_checksum = {p.checksum: p for p in result.pairs}
    plain = by_checksum["sum-plain"]
    assert plain.keeper.id == p_plain
    assert plain.loser.id == s_plain
    assert plain.live_photo == LivePhotoCase.ALIGNED

    album_pair = by_checksum["sum-album"]
    assert [a.name for a in album_pair.loser.albums] == ["Trip"]
    assert result.stats.affected_albums == 1
    # stats count the two losers only
    assert result.stats.reclaimable_assets == 2
    assert result.stats.reclaimable_bytes == 150


def test_scan_excludes_partner_assets_from_owner_listing():
    """Secondary's search includes primary's assets via partner sharing; the
    scan must not treat primary assets as secondary-owned duplicates."""
    world = World()
    fake = world.fake
    fake.add_asset(world.p_id, "only-primary")

    result = scan(world.client, world.primary, world.secondary)
    assert result.stats.primary_assets == 1
    assert result.stats.secondary_assets == 0
    assert result.pairs == []


def test_scan_live_photo_pair_counts_motion():
    world = World()
    fake = world.fake
    # both sides have still + motion; motions also share a checksum
    p_still, p_motion = fake.add_live_photo(world.p_id, "lp-still", "lp-motion", size_bytes=500)
    s_still, s_motion = fake.add_live_photo(world.s_id, "lp-still", "lp-motion", size_bytes=500)

    result = scan(world.client, world.primary, world.secondary)

    assert result.stats.pair_count == 2  # still pair + motion pair
    still_pair = next(p for p in result.pairs if p.keeper.id == p_still)
    assert any(p.keeper.id == p_motion for p in result.pairs)
    assert still_pair.live_photo == LivePhotoCase.ALIGNED
    assert still_pair.motion_ids == [s_motion]
    # reclaimable: loser still (500) + loser motion (2000, counted once despite
    # appearing as both the still's motion and its own pair)
    assert result.stats.reclaimable_assets == 2
    assert result.stats.reclaimable_bytes == 2_500
    assert s_motion in result.motion_ids


def test_scan_keeper_lacks_motion_case():
    world = World()
    fake = world.fake
    fake.add_asset(world.p_id, "x-still", size_bytes=10)
    fake.add_live_photo(world.s_id, "x-still", "x-motion", size_bytes=10)

    result = scan(world.client, world.primary, world.secondary)
    pair = result.pairs[0]
    assert pair.live_photo == LivePhotoCase.KEEPER_LACKS_MOTION
    assert result.stats.live_photo_keeper_lacks_motion == 1


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

    # re-fetch assets through the scan pipeline
    from immich_dedup.core.match import _user_assets

    p_assets = _user_assets(world.client, world.primary, None)
    s_assets = _user_assets(world.client, world.secondary, None)
    candidates = fuzzy_candidates(p_assets, s_assets)

    assert len(candidates) == 1
    keeper, loser = candidates[0]
    assert keeper.checksum == "ck-a"
    assert loser.checksum == "ck-b"


def test_csv_and_summary(tmp_path):
    world = World()
    fake = world.fake
    fake.add_asset(world.p_id, "sum-plain", size_bytes=100)
    s = fake.add_asset(world.s_id, "sum-plain", size_bytes=100)

    result = scan(world.client, world.primary, world.secondary)
    csv_path = write_csv(result, tmp_path / "dedup_report.csv", "http://immich.test")

    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("checksum,")
    assert "http://immich.test/photos/" + s in lines[1]

    summary = summary_text(result)
    assert "Cross-user duplicate pairs: 1" in summary
    assert "Reclaimable: 1 assets" in summary
