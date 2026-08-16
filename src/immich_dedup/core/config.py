"""Configuration from .env file and CLI overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_KEYS = (
    "IMMICH_URL",
    "PRIMARY_EMAIL",
    "SECONDARY_EMAIL",
    "PRIMARY_API_KEY",
    "SECONDARY_API_KEY",
)


class ConfigError(Exception):
    """Raised when required configuration is missing or inconsistent."""


@dataclass(frozen=True)
class DedupConfig:
    immich_url: str
    primary_email: str
    secondary_email: str
    primary_api_key: str
    secondary_api_key: str
    reports_dir: Path = Path("reports")


def load_config(
    env_file: Path | str | None = None,
    *,
    overrides: dict[str, str | None] | None = None,
    environ: dict[str, str] | None = None,
) -> DedupConfig:
    """Resolve configuration.

    Precedence: explicit overrides (CLI flags, non-None wins) > env vars > .env file.
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

    if missing:
        raise ConfigError(
            "Missing required configuration: "
            + ", ".join(missing)
            + ". Provide them via CLI flags or a .env file (see .env.example)."
        )

    url = values["IMMICH_URL"].rstrip("/")
    return DedupConfig(
        immich_url=url,
        primary_email=values["PRIMARY_EMAIL"].strip().lower(),
        secondary_email=values["SECONDARY_EMAIL"].strip().lower(),
        primary_api_key=values["PRIMARY_API_KEY"],
        secondary_api_key=values["SECONDARY_API_KEY"],
    )


def empty_config(reports_dir: Path = Path("reports")) -> DedupConfig:
    """A configuration with no values — the web UI starts like this and gets
    its connection details through POST /api/config."""
    return DedupConfig(
        immich_url="",
        primary_email="",
        secondary_email="",
        primary_api_key="",
        secondary_api_key="",
        reports_dir=reports_dir,
    )


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
