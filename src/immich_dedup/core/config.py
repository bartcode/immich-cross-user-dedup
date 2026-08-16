"""Configuration from .env file and CLI overrides.

Supports one primary user (whose copies are kept) and any number of secondary
users (whose duplicates get trashed). Secondary users are configured either as
comma-separated lists (``SECONDARY_EMAILS`` + ``SECONDARY_API_KEYS``, paired by
position) or via the legacy single-user ``SECONDARY_EMAIL`` / ``SECONDARY_API_KEY``
variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_KEYS = ("IMMICH_URL", "PRIMARY_EMAIL", "PRIMARY_API_KEY")
SECONDARY_EMAILS_KEY = "SECONDARY_EMAILS"
SECONDARY_KEYS_KEY = "SECONDARY_API_KEYS"
# legacy single-secondary variables, still honored
LEGACY_EMAIL_KEY = "SECONDARY_EMAIL"
LEGACY_KEY_KEY = "SECONDARY_API_KEY"


class ConfigError(Exception):
    """Raised when required configuration is missing or inconsistent."""


@dataclass(frozen=True)
class SecondaryCredentials:
    email: str
    api_key: str


@dataclass(frozen=True)
class DedupConfig:
    immich_url: str
    primary_email: str
    primary_api_key: str
    secondaries: tuple[SecondaryCredentials, ...] = ()
    reports_dir: Path = Path("reports")

    @property
    def secondary_emails(self) -> tuple[str, ...]:
        return tuple(secondary.email for secondary in self.secondaries)

    def api_key_for(self, email: str) -> str:
        normalized = email.strip().lower()
        if normalized == self.primary_email:
            return self.primary_api_key
        for secondary in self.secondaries:
            if secondary.email == normalized:
                return secondary.api_key
        raise ConfigError(f"no API key configured for {email!r}")


def _parse_env_secondaries(env: dict[str, str]) -> list[tuple[str, str]]:
    emails = [value.strip() for value in env.get(SECONDARY_EMAILS_KEY, "").split(",") if value.strip()]
    keys = [value.strip() for value in env.get(SECONDARY_KEYS_KEY, "").split(",") if value.strip()]
    result: list[tuple[str, str]] = []
    if emails or keys:
        if len(emails) != len(keys):
            raise ConfigError(
                f"{SECONDARY_EMAILS_KEY} and {SECONDARY_KEYS_KEY} must have the same number of "
                f"comma-separated entries (got {len(emails)} emails, {len(keys)} keys)"
            )
        result.extend(zip(emails, keys, strict=True))
    legacy_email = env.get(LEGACY_EMAIL_KEY, "").strip()
    legacy_key = env.get(LEGACY_KEY_KEY, "").strip()
    if legacy_email or legacy_key:
        if not (legacy_email and legacy_key):
            raise ConfigError(
                f"{LEGACY_EMAIL_KEY} and {LEGACY_KEY_KEY} must be set together "
                "(or use the SECONDARY_EMAILS / SECONDARY_API_KEYS lists)"
            )
        result.append((legacy_email, legacy_key))
    return result


def _validate_secondaries(
    primary_email: str, secondaries: list[tuple[str, str]]
) -> tuple[SecondaryCredentials, ...]:
    normalized: list[SecondaryCredentials] = []
    seen: set[str] = set()
    for email, key in secondaries:
        normalized_email = email.strip().lower()
        if not normalized_email or not key:
            raise ConfigError("secondary users need both an email and an API key")
        if normalized_email in seen:
            raise ConfigError(f"secondary user {normalized_email!r} is configured more than once")
        if normalized_email == primary_email:
            raise ConfigError("the primary user must not also be listed as a secondary user")
        seen.add(normalized_email)
        normalized.append(SecondaryCredentials(email=normalized_email, api_key=key))
    if not normalized:
        raise ConfigError(
            "at least one secondary user is required — set SECONDARY_EMAILS and SECONDARY_API_KEYS "
            "(or the legacy SECONDARY_EMAIL / SECONDARY_API_KEY)"
        )
    return tuple(normalized)


def load_config(
    env_file: Path | str | None = None,
    *,
    overrides: dict[str, str | None] | None = None,
    secondary_overrides: list[tuple[str, str]] | None = None,
    environ: dict[str, str] | None = None,
) -> DedupConfig:
    """Resolve configuration.

    Precedence: explicit overrides (CLI flags, non-None wins) > env vars > .env
    file. Secondary users from ``secondary_overrides`` come first, then the env
    lists, then the legacy single-user pair; duplicates by email are rejected.
    """
    if env_file is not None or Path(".env").exists():
        load_dotenv(env_file or ".env", override=False)

    env = dict(os.environ if environ is None else environ)

    values: dict[str, str] = {}
    missing: list[str] = []
    for key in ENV_KEYS:
        value = None
        if overrides and overrides.get(key) is not None:
            value = overrides[key]
        if not value:
            value = env.get(key, "")
        if not value:
            missing.append(key)
        values[key] = value

    primary_email = values["PRIMARY_EMAIL"].strip().lower()
    errors: list[str] = []
    if missing:
        errors.append(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Provide them via CLI flags or a .env file (see .env.example)."
        )
    try:
        pairs: list[tuple[str, str]] = list(secondary_overrides or [])
        pairs.extend(_parse_env_secondaries(env))
        secondaries = _validate_secondaries(primary_email, pairs)
    except ConfigError as error:
        secondaries = ()
        errors.append(str(error))
    if errors:
        raise ConfigError("\n".join(errors))

    return DedupConfig(
        immich_url=values["IMMICH_URL"].rstrip("/"),
        primary_email=primary_email,
        primary_api_key=values["PRIMARY_API_KEY"],
        secondaries=secondaries,
    )


def empty_config(reports_dir: Path = Path("reports")) -> DedupConfig:
    """A configuration with no values — the web UI starts like this and gets
    its connection details through POST /api/config."""
    return DedupConfig(
        immich_url="",
        primary_email="",
        primary_api_key="",
        secondaries=(),
        reports_dir=reports_dir,
    )


def secondary_env_values(secondaries: tuple[SecondaryCredentials, ...]) -> dict[str, str]:
    """Render secondaries as the comma-separated .env values."""
    return {
        SECONDARY_EMAILS_KEY: ",".join(secondary.email for secondary in secondaries),
        SECONDARY_KEYS_KEY: ",".join(secondary.api_key for secondary in secondaries),
    }


def save_env(path: Path, values: dict[str, str]) -> None:
    """Update the given keys in a .env file, preserving unrelated lines.

    Missing keys are appended; existing assignments are replaced in place."""
    lines = path.read_text().splitlines() if path.exists() else []
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        stripped = line.strip()
        is_assignment = stripped and not stripped.startswith("#") and "=" in stripped
        key = stripped.split("=", 1)[0].strip() if is_assignment else None
        if key in values:
            result.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            result.append(line)
    for key, value in values.items():
        if key not in seen:
            result.append(f"{key}={value}")
    path.write_text("\n".join(result) + "\n")
