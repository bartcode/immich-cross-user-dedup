import pytest

from immich_dedup.core.api import ImmichApiError, ImmichAuthError
from immich_dedup.core.models import PRIMARY, SECONDARY

from ..fakes.immich_api import FakeImmich, make_client


@pytest.fixture
def fake():
    return FakeImmich()


@pytest.fixture
def users(fake):
    primary_id, primary_key = fake.add_user("primary@example.com", "Primary")
    secondary_id, secondary_key = fake.add_user("secondary@example.com", "Secondary")
    return {"primary": (primary_id, primary_key), "secondary": (secondary_id, secondary_key)}


def make(fake, users):
    return make_client(fake, users["primary"][1], users["secondary"][1])


def test_me_requires_valid_key(fake, users):
    from immich_dedup.core.api import ImmichClient
    from tests.fakes.immich_api import BASE

    client = ImmichClient(BASE, "bad-key", users["secondary"][1], transport=fake.transport())
    with pytest.raises(ImmichAuthError):
        client.get_me(PRIMARY)


def test_iter_assets_paginates_and_filters_by_owner_arg(fake, users):
    client = make(fake, users)
    primary_id = users["primary"][0]
    for i in range(7):
        fake.add_asset(primary_id, f"checksum-{i}")
    items = list(client.iter_assets(PRIMARY))
    assert len(items) == 7
    assert {item["checksum"] for item in items} == {f"checksum-{i}" for i in range(7)}


def test_iter_assets_includes_partner_assets_when_in_timeline(fake, users):
    """Immich metadata search includes partner assets — the caller must filter by ownerId."""
    client = make(fake, users)
    primary_id, secondary_id = users["primary"][0], users["secondary"][0]
    fake.add_asset(primary_id, "primary-only")
    fake.add_asset(secondary_id, "secondary-shared")
    fake.set_partner(secondary_id, primary_id, in_timeline=True)

    items = list(client.iter_assets(PRIMARY))
    # The fake returns both because primary has partner sharing with timeline enabled.
    assert {item["checksum"] for item in items} == {"primary-only", "secondary-shared"}


def test_trashed_and_deleted_assets_excluded_from_search(fake, users):
    client = make(fake, users)
    primary_id = users["primary"][0]
    fake.add_asset(primary_id, "keep")
    b = fake.add_asset(primary_id, "trashed")
    c = fake.add_asset(primary_id, "deleted")
    fake.asset(b)["trashed"] = True
    fake.asset(c)["deleted"] = True

    checksums = {item["checksum"] for item in client.iter_assets(PRIMARY)}
    assert checksums == {"keep"}


def test_albums_for_asset_respects_visibility(fake, users):
    client = make(fake, users)
    primary_id, secondary_id = users["primary"][0], users["secondary"][0]
    asset = fake.add_asset(secondary_id, "x")
    owned_by_primary = fake.add_album(primary_id, "P album", asset_ids=[asset])
    shared_with_secondary = fake.add_album(
        primary_id, "Shared", asset_ids=[asset], shared_with={secondary_id: "editor"}
    )

    # Both albums are owned by primary, so primary sees both; secondary
    # (a participant of only "Shared") sees just that one.
    with_primary = client.get_albums_for_asset(PRIMARY, asset)
    with_secondary = client.get_albums_for_asset(SECONDARY, asset)
    assert {album["id"] for album in with_primary} == {owned_by_primary, shared_with_secondary}
    assert {album["id"] for album in with_secondary} == {shared_with_secondary}


def test_add_album_assets_requires_partner_for_foreign_asset(fake, users):
    client = make(fake, users)
    primary_id, secondary_id = users["primary"][0], users["secondary"][0]
    album = fake.add_album(secondary_id, "S album")
    keeper = fake.add_asset(primary_id, "k")

    results = client.add_album_assets(SECONDARY, album, [keeper])
    assert results == [{"id": keeper, "success": False, "error": "no_permission"}]

    fake.set_partner(primary_id, secondary_id)
    results = client.add_album_assets(SECONDARY, album, [keeper])
    assert results == [{"id": keeper, "success": True}]
    assert keeper in fake.album_asset_ids(album)

    # adding again reports duplicate, does not fail
    results = client.add_album_assets(SECONDARY, album, [keeper])
    assert results[0]["error"] == "duplicate"


def test_remove_album_assets(fake, users):
    client = make(fake, users)
    primary_id = users["primary"][0]
    asset = fake.add_asset(primary_id, "x")
    album = fake.add_album(primary_id, "A", asset_ids=[asset])
    client.remove_album_assets(PRIMARY, album, [asset])
    assert fake.album_asset_ids(album) == set()


def test_trash_assets_owner_only(fake, users):
    client = make(fake, users)
    primary_id, secondary_id = users["primary"][0], users["secondary"][0]
    mine = fake.add_asset(secondary_id, "mine")
    theirs = fake.add_asset(primary_id, "theirs")

    results = client.trash_assets(SECONDARY, [mine, theirs])
    by_id = {r["id"]: r for r in results}
    assert by_id[mine]["success"] is True
    assert by_id[theirs]["error"] == "no_permission"
    assert fake.asset(mine)["trashed"] is True
    assert fake.asset(theirs)["trashed"] is False


def test_restore_assets_only_restores_owned_trashed(fake, users):
    client = make(fake, users)
    secondary_id = users["secondary"][0]
    a = fake.add_asset(secondary_id, "a")
    fake.asset(a)["trashed"] = True
    response = client.restore_assets(SECONDARY, [a])
    assert response == {"count": 1}
    assert fake.asset(a)["trashed"] is False


def test_update_asset_owner_only(fake, users):
    client = make(fake, users)
    primary_id = users["primary"][0]
    mine = fake.add_asset(primary_id, "x")
    with pytest.raises(ImmichApiError):
        client.update_asset(SECONDARY, mine, isFavorite=True)
    client.update_asset(PRIMARY, mine, isFavorite=True, description="hello")
    assert fake.asset(mine)["isFavorite"] is True
    assert fake.asset(mine)["description"] == "hello"


def test_get_partners_directions(fake, users):
    client = make(fake, users)
    primary_id, secondary_id = users["primary"][0], users["secondary"][0]
    fake.set_partner(primary_id, secondary_id)  # primary shares with secondary

    partners = client.get_partners(PRIMARY)
    assert [p["id"] for p in partners["shared-by"]] == [secondary_id]
    assert partners["shared-with"] == []

    partners_secondary = client.get_partners(SECONDARY)
    assert partners_secondary["shared-with"][0]["id"] == primary_id


def test_thumbnail_permissions(fake, users):
    client = make(fake, users)
    primary_id, secondary_id = users["primary"][0], users["secondary"][0]
    asset = fake.add_asset(primary_id, "x")
    assert client.get_thumbnail(PRIMARY, asset) == f"thumb:{asset}".encode()
    with pytest.raises(ImmichApiError):
        client.get_thumbnail(SECONDARY, asset)
    fake.set_partner(primary_id, secondary_id)
    assert client.get_thumbnail(SECONDARY, asset) == f"thumb:{asset}".encode()
