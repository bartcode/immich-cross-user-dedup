import pytest

from immich_dedup.core.config import (
    ConfigError,
    DedupConfig,
    SecondaryCredentials,
    load_config,
    save_env,
    secondary_env_values,
)

BASE_ENV = {
    "IMMICH_URL": "http://immich.example:2283/",
    "PRIMARY_EMAIL": "Primary@Example.com",
    "PRIMARY_API_KEY": "pk",
}


def with_secondaries(*pairs: tuple[str, str]) -> dict[str, str]:
    env = dict(BASE_ENV)
    if pairs:
        env["SECONDARY_EMAILS"] = ",".join(email for email, _ in pairs)
        env["SECONDARY_API_KEYS"] = ",".join(key for _, key in pairs)
    return env


def test_load_config_single_secondary_from_lists():
    config = load_config(environ=with_secondaries(("Secondary@Example.com", "sk")))
    assert config.immich_url == "http://immich.example:2283"
    assert config.primary_email == "primary@example.com"
    assert config.secondaries == (SecondaryCredentials("secondary@example.com", "sk"),)
    assert config.secondary_emails == ("secondary@example.com",)
    assert config.api_key_for("secondary@example.com") == "sk"
    assert config.api_key_for("primary@example.com") == "pk"


def test_load_config_multiple_secondaries():
    config = load_config(environ=with_secondaries(("a@x.com", "k1"), ("b@x.com", "k2")))
    assert config.secondary_emails == ("a@x.com", "b@x.com")
    assert config.api_key_for("A@X.COM") == "k1"
    assert config.api_key_for("b@x.com") == "k2"


def test_load_config_legacy_single_secondary_still_works():
    env = {**BASE_ENV, "SECONDARY_EMAIL": "legacy@x.com", "SECONDARY_API_KEY": "lk"}
    config = load_config(environ=env)
    assert config.secondaries == (SecondaryCredentials("legacy@x.com", "lk"),)


def test_load_config_lists_and_legacy_merge_without_duplicates():
    env = with_secondaries(("a@x.com", "k1"))
    env |= {"SECONDARY_EMAIL": "legacy@x.com", "SECONDARY_API_KEY": "lk"}
    config = load_config(environ=env)
    assert config.secondary_emails == ("a@x.com", "legacy@x.com")


def test_load_config_missing_values_lists_them():
    env = dict(BASE_ENV)
    del env["PRIMARY_API_KEY"]
    with pytest.raises(ConfigError, match="PRIMARY_API_KEY"):
        load_config(environ=env)


def test_load_config_requires_at_least_one_secondary():
    with pytest.raises(ConfigError, match="at least one secondary"):
        load_config(environ=dict(BASE_ENV))


def test_load_config_mismatched_lists_rejected():
    env = {**BASE_ENV, "SECONDARY_EMAILS": "a@x.com,b@x.com", "SECONDARY_API_KEYS": "k1"}
    with pytest.raises(ConfigError, match="same number"):
        load_config(environ=env)


def test_load_config_duplicate_secondary_rejected():
    with pytest.raises(ConfigError, match="more than once"):
        load_config(environ=with_secondaries(("a@x.com", "k1"), ("A@X.COM", "k2")))


def test_load_config_primary_as_secondary_rejected():
    with pytest.raises(ConfigError, match="must not also be listed"):
        load_config(environ=with_secondaries(("primary@example.com", "k1")))


def test_load_config_secondary_overrides_win_over_environ():
    config = load_config(
        environ=with_secondaries(("env@x.com", "env-key")),
        secondary_overrides=[("cli@x.com", "cli-key")],
    )
    assert config.secondary_emails == ("cli@x.com", "env@x.com")


def test_load_config_none_override_falls_through():
    config = load_config(environ=with_secondaries(("a@x.com", "k1")), overrides={"PRIMARY_API_KEY": None})
    assert config.primary_api_key == "pk"


def test_api_key_for_unknown_user_raises():
    config = DedupConfig("http://x", "p@x.com", "pk", (SecondaryCredentials("s@x.com", "sk"),))
    with pytest.raises(ConfigError, match="no API key configured"):
        config.api_key_for("nobody@x.com")


def test_save_env_updates_appends_and_preserves(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment stays\nIMMICH_URL=http://old\nOTHER=1\n")

    save_env(env, {"IMMICH_URL": "http://new", "PRIMARY_API_KEY": "k"})

    lines = env.read_text().splitlines()
    assert lines == ["# comment stays", "IMMICH_URL=http://new", "OTHER=1", "PRIMARY_API_KEY=k"]


def test_save_env_writes_secondary_lists(tmp_path):
    env = tmp_path / ".env"
    save_env(env, secondary_env_values((SecondaryCredentials("a@x.com", "k1"), SecondaryCredentials("b@x.com", "k2"))))
    assert env.read_text() == "SECONDARY_EMAILS=a@x.com,b@x.com\nSECONDARY_API_KEYS=k1,k2\n"


def test_save_env_creates_missing_file(tmp_path):
    env = tmp_path / ".env"
    save_env(env, {"IMMICH_URL": "http://x"})
    assert env.read_text() == "IMMICH_URL=http://x\n"
