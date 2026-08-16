from immich_dedup.core.config import DedupConfig
from immich_dedup.core.preflight import run_preflight

from ..fakes.immich_api import FakeImmich, make_client


def config_for(fake_urls="http://immich.test"):
    return DedupConfig(
        immich_url=fake_urls,
        primary_email="primary@example.com",
        secondary_email="secondary@example.com",
        primary_api_key="pk",
        secondary_api_key="sk",
    )


def build(fake, primary_key, secondary_key):
    return make_client(fake, primary_key, secondary_key)


def setup_two_users(fake):
    primary_id, primary_key = fake.add_user("primary@example.com")
    secondary_id, secondary_key = fake.add_user("secondary@example.com")
    return primary_id, primary_key, secondary_id, secondary_key


def test_preflight_ok_with_partner_sharing():
    fake = FakeImmich()
    p_id, p_key, s_id, s_key = setup_two_users(fake)
    fake.set_partner(p_id, s_id)
    fake.set_partner(s_id, p_id)

    report = run_preflight(build(fake, p_key, s_key), config_for())
    assert not report.failed
    assert report.primary is not None and report.secondary is not None
    assert report.primary.id == p_id
    assert report.partners_bidirectional is True


def test_preflight_fails_on_swapped_keys():
    fake = FakeImmich()
    _, p_key, _, s_key = setup_two_users(fake)
    # keys swapped: primary slot holds secondary's key
    report = run_preflight(build(fake, s_key, p_key), config_for())
    assert report.failed
    assert any("not swapped" in check.detail or "belongs to" in check.detail for check in report.checks)


def test_preflight_warns_when_partner_sharing_missing():
    fake = FakeImmich()
    p_id, p_key, s_id, s_key = setup_two_users(fake)

    report = run_preflight(build(fake, p_key, s_key), config_for())
    assert report.failed
    assert report.partners_bidirectional is False
    assert any("partner" in check.name for check in report.checks if not check.ok)


def test_preflight_rejects_same_user_for_both_keys():
    fake = FakeImmich()
    p_id, p_key, _, _ = setup_two_users(fake)
    other_key = fake.add_api_key(p_id)  # same user, different API key

    same_email_config = DedupConfig(
        immich_url="http://immich.test",
        primary_email="primary@example.com",
        secondary_email="primary@example.com",
        primary_api_key="pk",
        secondary_api_key="sk",
    )
    report = run_preflight(build(fake, p_key, other_key), same_email_config)
    assert report.failed
    assert any("same user" in check.detail for check in report.checks)


def test_preflight_rejects_key_of_wrong_user_in_slot():
    fake = FakeImmich()
    _, p_key, _, s_key = setup_two_users(fake)
    # secondary's key in the primary slot: email mismatch is reported
    report = run_preflight(build(fake, s_key, s_key), config_for())
    assert report.failed
    assert any("belongs to" in check.detail for check in report.checks)
