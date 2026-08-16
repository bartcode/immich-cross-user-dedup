import pytest

from immich_dedup.core.config import ConfigError, load_config, save_env

REQUIRED = {
    "IMMICH_URL": "http://immich.example:2283/",
    "PRIMARY_EMAIL": "Primary@Example.com",
    "SECONDARY_EMAIL": "secondary@example.com",
    "PRIMARY_API_KEY": "pk",
    "SECONDARY_API_KEY": "sk",
}


def test_load_config_from_environ():
    config = load_config(environ=REQUIRED)
    assert config.immich_url == "http://immich.example:2283"  # trailing slash stripped
    assert config.primary_email == "primary@example.com"  # normalized
    assert config.secondary_email == "secondary@example.com"
    assert config.primary_api_key == "pk"
    assert config.secondary_api_key == "sk"


def test_load_config_missing_values_lists_them():
    env = {k: v for k, v in REQUIRED.items() if k != "PRIMARY_API_KEY"}
    with pytest.raises(ConfigError, match="PRIMARY_API_KEY"):
        load_config(environ=env)


def test_load_config_overrides_win_over_environ():
    overrides = {"PRIMARY_API_KEY": "flag-key"}
    config = load_config(environ=REQUIRED, overrides=overrides)
    assert config.primary_api_key == "flag-key"
    assert config.secondary_api_key == "sk"  # untouched


def test_load_config_none_override_falls_through():
    config = load_config(environ=REQUIRED, overrides={"PRIMARY_API_KEY": None})
    assert config.primary_api_key == "pk"


def test_save_env_updates_appends_and_preserves(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment stays\nIMMICH_URL=http://old\nOTHER=1\n")

    save_env(env, {"IMMICH_URL": "http://new", "PRIMARY_API_KEY": "k"})

    lines = env.read_text().splitlines()
    assert lines == ["# comment stays", "IMMICH_URL=http://new", "OTHER=1", "PRIMARY_API_KEY=k"]


def test_save_env_creates_missing_file(tmp_path):
    env = tmp_path / ".env"
    save_env(env, {"IMMICH_URL": "http://x"})
    assert env.read_text() == "IMMICH_URL=http://x\n"
