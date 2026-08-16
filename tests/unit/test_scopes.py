"""API-key permission scopes: enforcement in the fake, preflight probes, and
the required-scope sets the tool documents."""

from pathlib import Path

import pytest

from immich_dedup.core.api import ImmichApiError
from immich_dedup.core.config import DedupConfig, SecondaryCredentials
from immich_dedup.core.preflight import run_preflight

from ..fakes.immich_api import BASE, FakeImmich, make_client

P = "primary@example.com"
S = "secondary@example.com"

# the scope sets documented in README/.env.example and the UI connection form
PRIMARY_SCOPES = [
    "user.read",
    "partner.read",
    "asset.read",
    "asset.view",
    "asset.statistics",
    "album.read",
    "albumAsset.create",
    "albumAsset.delete",
]
SECONDARY_SCOPES = [*PRIMARY_SCOPES, "asset.delete", "albumUser.create", "albumUser.delete"]


def key_for(fake: FakeImmich, user_id: str) -> str:
    return next(key for key, record in fake.users.items() if record["id"] == user_id)


def seeded_world(primary_permissions=None, secondary_permissions=None):
    fake = FakeImmich()
    p_id, _ = fake.add_user(P, permissions=primary_permissions)
    s_id, _ = fake.add_user(S, permissions=secondary_permissions)
    fake.set_partner(p_id, s_id)
    fake.set_partner(s_id, p_id)
    keeper = fake.add_asset(p_id, "sum-1", size_bytes=10)
    loser = fake.add_asset(s_id, "sum-1", size_bytes=10)
    album = fake.add_album(s_id, "Trip", asset_ids=[loser])
    return fake, {"p_id": p_id, "s_id": s_id, "keeper": keeper, "loser": loser, "album": album}


def client_for(fake: FakeImmich, ids):
    return make_client(fake, {P: key_for(fake, ids["p_id"]), S: key_for(fake, ids["s_id"])})


def test_fake_enforces_missing_scope_with_named_permission():
    fake, ids = seeded_world(secondary_permissions=["user.read"])  # no asset.read
    client = client_for(fake, ids)

    list(client.iter_assets(P))  # unrestricted primary key works
    with pytest.raises(ImmichApiError, match="asset.read"):
        list(client.iter_assets(S))  # restricted secondary key gets the named error


def test_all_scope_grants_everything():
    fake, ids = seeded_world(secondary_permissions=["all"])
    client = client_for(fake, ids)
    # 2 = the secondary's own asset + the primary's partner-shared asset
    assert client.asset_count(S) == 2  # asset.statistics via 'all'
    client.trash_assets(S, [ids["loser"]])  # asset.delete via 'all'
    assert fake.asset(ids["loser"])["trashed"] is True


def test_album_add_uses_put():
    """Current Immich is PUT for add-assets-to-album (the client falls back to
    POST only on 404/405 for older servers)."""
    fake, ids = seeded_world()
    client = client_for(fake, ids)

    results = client.add_album_assets(S, ids["album"], [ids["keeper"]])

    assert results[0]["success"] is True
    assert ids["keeper"] in fake.album_asset_ids(ids["album"])
    assert any(method == "PUT" and path.endswith("/assets") for method, path, _ in fake.requests)


def test_album_add_post_fallback_on_404():
    fake, ids = seeded_world()

    def older_server(request):
        import httpx

        if (
            request.method == "PUT"
            and request.url.path.startswith("/api/albums/")
            and request.url.path.endswith("/assets")
        ):
            return httpx.Response(404, json={"message": "Cannot PUT"})
        return fake._handle(request)

    import httpx

    from immich_dedup.core.api import ImmichClient

    transport = httpx.MockTransport(older_server)
    client = ImmichClient(BASE, {P: key_for(fake, ids["p_id"]), S: key_for(fake, ids["s_id"])}, transport=transport)
    results = client.add_album_assets(S, ids["album"], [ids["keeper"]])
    assert results[0]["success"] is True
    assert ids["keeper"] in fake.album_asset_ids(ids["album"])


def test_preflight_probe_reports_missing_scope():
    fake, ids = seeded_world(secondary_permissions=["user.read"])  # missing asset.read
    client = client_for(fake, ids)

    report = run_preflight(client, DedupConfig(BASE, P, key_for(fake, ids["p_id"]),
                                               (SecondaryCredentials(S, key_for(fake, ids["s_id"])),)))
    assert report.failed
    scope_check = next(c for c in report.checks if c.name == f"{S} scopes")
    assert scope_check.ok is False
    # every scope this key lacks is named up front, not just the first one
    for scope in ("partner.read", "asset.read", "asset.statistics", "album.read",
                  "albumAsset.create", "albumUser.create", "asset.delete"):
        assert scope in scope_check.detail
    # the unrestricted primary key passes fully
    primary_check = next(c for c in report.checks if c.name == f"{P} scopes")
    assert primary_check.ok is True


def test_preflight_probe_passes_with_documented_scopes():
    fake, ids = seeded_world(primary_permissions=PRIMARY_SCOPES, secondary_permissions=SECONDARY_SCOPES)
    client = client_for(fake, ids)

    report = run_preflight(client, DedupConfig(BASE, P, key_for(fake, ids["p_id"]),
                                               (SecondaryCredentials(S, key_for(fake, ids["s_id"])),)))
    assert not report.failed, [c.detail for c in report.checks if not c.ok]
    scope_checks = [c for c in report.checks if c.name.endswith(" scopes")]
    assert len(scope_checks) == 2
    # the primary set has no asset.update — noted as optional, not a failure
    primary_check = next(c for c in scope_checks if c.name == f"{P} scopes")
    assert primary_check.ok is True
    assert "asset.update missing" in primary_check.detail


def test_full_pipeline_works_with_minimal_documented_scopes(tmp_path: Path):
    """End-to-end with exactly the documented scope sets (the primary set has no
    asset.delete — the primary key never deletes anything)."""
    from immich_dedup.core.apply import ApplyOptions, apply_groups
    from immich_dedup.core.journal import Journal, undo_journal
    from immich_dedup.core.match import scan

    fake, ids = seeded_world(primary_permissions=PRIMARY_SCOPES, secondary_permissions=SECONDARY_SCOPES)
    client = client_for(fake, ids)
    config = DedupConfig(BASE, P, key_for(fake, ids["p_id"]),
                         (SecondaryCredentials(S, key_for(fake, ids["s_id"])),))

    report = run_preflight(client, config)
    assert not report.failed
    result = scan(client, report.primary, report.secondaries, users=report.users)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_groups(client, result, ApplyOptions(), journal)
    journal.close()
    assert outcome.applied_groups == 1
    assert fake.asset(ids["loser"])["trashed"] is True

    undo = undo_journal(client, journal)
    assert undo.restored_assets == 1
    assert fake.asset(ids["loser"])["trashed"] is False


def test_no_partner_sharing_pipeline_uses_album_editor_fallback(tmp_path: Path):
    """Partner sharing entirely absent: transfers work by sharing each affected
    album with the primary as editor (albumUser.create) and revoking on undo
    (albumUser.delete) — no partner write scopes are involved."""
    from immich_dedup.core.apply import ApplyOptions, apply_groups
    from immich_dedup.core.journal import Journal, undo_journal
    from immich_dedup.core.match import scan

    fake, ids = seeded_world(primary_permissions=PRIMARY_SCOPES, secondary_permissions=SECONDARY_SCOPES)
    fake.partners.clear()  # no partner sharing at all
    client = client_for(fake, ids)
    config = DedupConfig(BASE, P, key_for(fake, ids["p_id"]),
                         (SecondaryCredentials(S, key_for(fake, ids["s_id"])),))

    report = run_preflight(client, config)
    assert not report.failed  # informational only

    result = scan(client, report.primary, report.secondaries, users=report.users)
    journal = Journal(tmp_path / "j.jsonl")
    outcome = apply_groups(client, result, ApplyOptions(), journal)
    journal.close()
    assert outcome.applied_groups == 1
    assert outcome.album_failures == []
    assert ids["keeper"] in fake.album_asset_ids(ids["album"])
    assert fake.albums[ids["album"]]["users"].get(ids["p_id"]) == "editor"

    undo = undo_journal(client, journal)
    assert undo.errors == []
    assert undo.restored_assets == 1
    assert ids["keeper"] not in fake.album_asset_ids(ids["album"])
    assert ids["p_id"] not in fake.albums[ids["album"]]["users"]
