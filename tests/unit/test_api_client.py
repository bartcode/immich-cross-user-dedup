import pytest

from immich_dedup.core.api import ImmichApiError, ImmichAuthError, ImmichClient

from ..fakes.immich_api import BASE, FakeImmich, make_client

P = "primary@example.com"
S = "secondary@example.com"


@pytest.fixture
def world():
    return FakeImmich()


@pytest.fixture
def keys(world):
    p_id, p_key = world.add_user(P, "Primary")
    s_id, s_key = world.add_user(S, "Secondary")
    return {"p_id": p_id, "s_id": s_id, "client_keys": {P: p_key, S: s_key}}


def make(world, keys):
    return make_client(world, keys["client_keys"])


def test_me_requires_valid_key(world, keys):
    client = ImmichClient(BASE, {P: "bad-key"}, transport=world.transport())
    with pytest.raises(ImmichAuthError):
        client.get_me(P)


def test_iter_assets_paginates(world, keys):
    client = make(world, keys)
    for i in range(7):
        world.add_asset(keys["p_id"], f"checksum-{i}")
    items = list(client.iter_assets(P))
    assert len(items) == 7
    assert {item["checksum"] for item in items} == {f"checksum-{i}" for i in range(7)}


def test_iter_assets_includes_partner_assets_when_in_timeline(world, keys):
    """Immich metadata search includes partner assets — the caller must filter by ownerId."""
    client = make(world, keys)
    world.add_asset(keys["p_id"], "primary-only")
    world.add_asset(keys["s_id"], "secondary-shared")
    world.set_partner(keys["s_id"], keys["p_id"], in_timeline=True)

    items = list(client.iter_assets(P))
    assert {item["checksum"] for item in items} == {"primary-only", "secondary-shared"}


def test_trashed_and_deleted_assets_excluded_from_search(world, keys):
    client = make(world, keys)
    world.add_asset(keys["p_id"], "keep")
    b = world.add_asset(keys["p_id"], "trashed")
    c = world.add_asset(keys["p_id"], "deleted")
    world.asset(b)["trashed"] = True
    world.asset(c)["deleted"] = True

    checksums = {item["checksum"] for item in client.iter_assets(P)}
    assert checksums == {"keep"}


def test_albums_for_asset_respects_visibility(world, keys):
    client = make(world, keys)
    asset = world.add_asset(keys["s_id"], "x")
    owned_by_primary = world.add_album(keys["p_id"], "P album", asset_ids=[asset])
    shared_with_secondary = world.add_album(
        keys["p_id"], "Shared", asset_ids=[asset], shared_with={keys["s_id"]: "editor"}
    )

    # Both albums are owned by primary, so primary sees both; secondary
    # (a participant of only "Shared") sees just that one.
    with_primary = client.get_albums_for_asset(P, asset)
    with_secondary = client.get_albums_for_asset(S, asset)
    assert {album["id"] for album in with_primary} == {owned_by_primary, shared_with_secondary}
    assert {album["id"] for album in with_secondary} == {shared_with_secondary}


def test_add_album_assets_requires_partner_for_foreign_asset(world, keys):
    client = make(world, keys)
    album = world.add_album(keys["s_id"], "S album")
    keeper = world.add_asset(keys["p_id"], "k")

    results = client.add_album_assets(S, album, [keeper])
    assert results == [{"id": keeper, "success": False, "error": "no_permission"}]

    world.set_partner(keys["p_id"], keys["s_id"])
    results = client.add_album_assets(S, album, [keeper])
    assert results == [{"id": keeper, "success": True}]
    assert keeper in world.album_asset_ids(album)

    # adding again reports duplicate, does not fail
    results = client.add_album_assets(S, album, [keeper])
    assert results[0]["error"] == "duplicate"


def test_remove_album_assets(world, keys):
    client = make(world, keys)
    asset = world.add_asset(keys["p_id"], "x")
    album = world.add_album(keys["p_id"], "A", asset_ids=[asset])
    client.remove_album_assets(P, album, [asset])
    assert world.album_asset_ids(album) == set()


def test_trash_assets_owner_only(world, keys):
    client = make(world, keys)
    mine = world.add_asset(keys["s_id"], "mine")
    theirs = world.add_asset(keys["p_id"], "theirs")

    results = client.trash_assets(S, [mine, theirs])
    by_id = {r["id"]: r for r in results}
    assert by_id[mine]["success"] is True
    assert by_id[theirs]["error"] == "no_permission"
    assert world.asset(mine)["trashed"] is True
    assert world.asset(theirs)["trashed"] is False


def test_restore_assets_only_restores_owned_trashed(world, keys):
    client = make(world, keys)
    a = world.add_asset(keys["s_id"], "a")
    world.asset(a)["trashed"] = True
    response = client.restore_assets(S, [a])
    assert response == {"count": 1}
    assert world.asset(a)["trashed"] is False


def test_update_asset_owner_only(world, keys):
    client = make(world, keys)
    mine = world.add_asset(keys["p_id"], "x")
    with pytest.raises(ImmichApiError):
        client.update_asset(S, mine, isFavorite=True)
    client.update_asset(P, mine, isFavorite=True, description="hello")
    assert world.asset(mine)["isFavorite"] is True
    assert world.asset(mine)["description"] == "hello"


def test_get_partners_directions(world, keys):
    client = make(world, keys)
    world.set_partner(keys["p_id"], keys["s_id"])  # primary shares with secondary

    partners = client.get_partners(P)
    assert [p["id"] for p in partners["shared-by"]] == [keys["s_id"]]
    assert partners["shared-with"] == []

    partners_secondary = client.get_partners(S)
    assert partners_secondary["shared-with"][0]["id"] == keys["p_id"]


def test_thumbnail_permissions(world, keys):
    client = make(world, keys)
    asset = world.add_asset(keys["p_id"], "x")
    assert client.get_thumbnail(P, asset) == f"thumb:{asset}".encode()
    with pytest.raises(ImmichApiError):
        client.get_thumbnail(S, asset)
    world.set_partner(keys["p_id"], keys["s_id"])
    assert client.get_thumbnail(S, asset) == f"thumb:{asset}".encode()
