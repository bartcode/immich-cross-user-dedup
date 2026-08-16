"""Pre-flight checks: validate keys, resolve users, verify the partner star."""

from __future__ import annotations

from dataclasses import dataclass, field

from immich_dedup.core.api import ImmichApiError, ImmichAuthError, ImmichClient
from immich_dedup.core.config import DedupConfig
from immich_dedup.core.models import User


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class PreflightReport:
    checks: list[Check]
    primary: User | None = None
    secondaries: list[User] = field(default_factory=list)
    # user_id -> User registry for every successfully validated user
    users: dict[str, User] = field(default_factory=dict)
    # secondary email -> partner sharing with primary is bidirectional
    partner_status: dict[str, bool] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return any(not check.ok for check in self.checks)


def run_preflight(client: ImmichClient, config: DedupConfig) -> PreflightReport:
    checks: list[Check] = []
    report = PreflightReport(checks=checks)

    def resolve(label: str, handle: str, expected_email: str) -> User | None:
        try:
            me = client.get_me(handle)
        except ImmichAuthError as error:
            checks.append(Check(f"{label} API key", False, str(error)))
            return None
        except ImmichApiError as error:
            checks.append(
                Check(
                    f"{label} connection",
                    False,
                    f"could not reach Immich as {label}: {error}",
                )
            )
            return None
        actual_email = me.get("email", "").strip().lower()
        if actual_email != expected_email:
            checks.append(
                Check(
                    f"{label} key matches {expected_email}",
                    False,
                    f"the {label} API key belongs to {actual_email!r} — check that the keys are not "
                    "swapped or misassigned",
                )
            )
            return None
        user = User(id=me["id"], email=actual_email, name=me.get("name", ""))
        checks.append(Check(f"{label} API key", True, f"valid for {actual_email} ({user.id})"))
        return user

    primary = resolve("primary", config.primary_email, config.primary_email)
    if primary is not None:
        report.primary = primary
        report.users[primary.id] = primary

    for creds in config.secondaries:
        secondary = resolve(f"secondary {creds.email}", creds.email, creds.email)
        if secondary is None:
            continue
        if secondary.id in report.users:
            checks.append(
                Check(
                    f"secondary {creds.email} distinct", False, "this key belongs to an already-listed user"
                )
            )
            continue
        checks.append(Check(f"secondary {creds.email} distinct", True, "a separate user account"))
        report.secondaries.append(secondary)
        report.users[secondary.id] = secondary

    # Probe the read scopes every key needs (asset.statistics + album.read via
    # count/list calls) so missing scopes surface here — Immich's error names
    # the exact missing permission. Write scopes (asset.delete,
    # albumAsset.create/delete, asset.update) are exercised at apply time.
    for user in [u for u in (report.primary, *report.secondaries) if u is not None]:
        try:
            assets = client.asset_count(user.email)
            albums = len(client.list_albums(user.email))
            checks.append(
                Check(
                    f"{user.email} scopes",
                    True,
                    f"asset.statistics + album.read verified ({assets} assets, {albums} albums visible)",
                )
            )
        except ImmichApiError as error:
            checks.append(Check(f"{user.email} scopes", False, str(error)))

    if report.primary is not None and report.secondaries:
        partners = client.get_partners(report.primary.email)
        shared_by = {p["id"] for p in partners.get("shared-by", [])}
        for secondary in report.secondaries:
            # Only the primary->secondary direction matters mechanically: it
            # lets the album owner add the primary's keeper with their own key.
            # Without it, affected albums are shared with the primary as editor
            # instead (see apply._transfer_foreign_album). Either way works.
            direct = secondary.id in shared_by
            report.partner_status[secondary.email] = direct
            checks.append(
                Check(
                    f"partner sharing with {secondary.email}",
                    True,
                    f"primary shares with {secondary.email} — direct album transfers"
                    if direct
                    else "not enabled — affected albums will be shared with the primary as "
                    "editor during apply and revoked on undo (partner sharing is optional)",
                )
            )

    return report
