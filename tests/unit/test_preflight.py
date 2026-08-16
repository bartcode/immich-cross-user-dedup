from immich_dedup.core.config import DedupConfig, SecondaryCredentials
from immich_dedup.core.preflight import run_preflight

from ..fakes.immich_api import make_client
from ..fakes.world import PRIMARY_EMAIL, World


def test_preflight_ok_with_partner_star():
    world = World(secondary_emails=("bob@example.com", "carol@example.com"))

    report = world.preflight()
    assert not report.failed
    assert report.primary is not None
    assert report.primary.id == world.p_id
    assert len(report.secondaries) == 2
    assert set(report.partner_status.values()) == {True}
    assert set(report.users) == {world.p_id, *(user_id for user_id, _ in world.secondary.values())}


def test_preflight_fails_on_misassigned_key():
    world = World(secondary_emails=("bob@example.com", "carol@example.com"))
    bob_key = world.secondary["bob@example.com"][1]
    world.client.close()
    world.client = make_client(
        world.fake,
        {
            PRIMARY_EMAIL: world.p_key,
            "bob@example.com": bob_key,
            "carol@example.com": bob_key,  # carol's slot holds bob's key
        },
    )

    report = world.preflight()
    assert report.failed
    assert any("belongs to" in check.detail for check in report.checks if not check.ok)


def test_preflight_reports_each_secondary_partner_status():
    world = World(secondary_emails=("bob@example.com", "carol@example.com"))
    carol_id = world.secondary["carol@example.com"][0]
    world.fake.partners.pop((world.p_id, carol_id))  # primary no longer shares with carol

    report = world.preflight()
    assert report.failed
    assert report.partner_status["bob@example.com"] is True
    assert report.partner_status["carol@example.com"] is False
    failed_names = [check.name for check in report.checks if not check.ok]
    assert "partner sharing with carol@example.com" in failed_names


def test_preflight_rejects_primary_listed_as_secondary():
    world = World()
    other_key = world.fake.add_api_key(world.p_id)
    world.config = DedupConfig(
        immich_url=world.config.immich_url,
        primary_email=PRIMARY_EMAIL,
        primary_api_key=world.p_key,
        secondaries=(SecondaryCredentials(PRIMARY_EMAIL, other_key),),
    )
    report = run_preflight(world.client, world.config)
    # same user for both keys is caught either by validation or the distinct check
    assert report.failed
    assert any(
        "must not also be listed" in check.detail or "already-listed" in check.detail
        for check in report.checks
        if not check.ok
    )
