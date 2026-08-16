"""Pre-flight checks: validate keys, resolve users, verify partner sharing."""

from __future__ import annotations

from dataclasses import dataclass

from immich_dedup.core.api import ImmichAuthError, ImmichClient
from immich_dedup.core.config import DedupConfig
from immich_dedup.core.models import PRIMARY, SECONDARY, User


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class PreflightReport:
    checks: list[Check]
    primary: User | None = None
    secondary: User | None = None
    partners_bidirectional: bool = False

    @property
    def failed(self) -> bool:
        return any(not check.ok for check in self.checks)


def run_preflight(client: ImmichClient, config: DedupConfig) -> PreflightReport:
    checks: list[Check] = []
    report = PreflightReport(checks=checks)

    users: dict[str, User] = {}
    for role, email in ((PRIMARY, config.primary_email), (SECONDARY, config.secondary_email)):
        try:
            me = client.get_me(role)
        except ImmichAuthError as error:
            checks.append(Check(f"{role} API key", False, str(error)))
            continue
        actual_email = me.get("email", "").strip().lower()
        if actual_email != email:
            checks.append(
                Check(
                    f"{role} key matches {email}",
                    False,
                    f"the {role} API key belongs to {actual_email!r} — check that PRIMARY_/SECONDARY_API_KEY "
                    "are not swapped",
                )
            )
            continue
        users[role] = User(role=role, id=me["id"], email=actual_email, name=me.get("name", ""))
        checks.append(Check(f"{role} API key", True, f"valid for {actual_email} ({users[role].id})"))

    if PRIMARY in users and SECONDARY in users:
        if users[PRIMARY].id == users[SECONDARY].id:
            checks.append(Check("distinct users", False, "both API keys belong to the same user"))
        else:
            checks.append(Check("distinct users", True, "primary and secondary are different users"))
            report.primary = users[PRIMARY]
            report.secondary = users[SECONDARY]

    if len(users) == 2:
        shared_by = {p["id"] for p in client.get_partners(PRIMARY).get("shared-by", [])}
        shared_with = {p["id"] for p in client.get_partners(PRIMARY).get("shared-with", [])}
        s = users[SECONDARY].id
        # primary's "shared-by" lists users primary shares with; its "shared-with"
        # lists users that share with primary — secondary must appear in both.
        primary_shares = s in shared_by
        secondary_shares = s in shared_with
        report.partners_bidirectional = primary_shares and secondary_shares
        if report.partners_bidirectional:
            checks.append(
                Check(
                    "partner sharing",
                    True,
                    "enabled in both directions — cross-user album transfers are permitted",
                )
            )
        else:
            missing = []
            if not primary_shares:
                missing.append(f"{users[PRIMARY].email} does not share with {users[SECONDARY].email}")
            if not secondary_shares:
                missing.append(f"{users[SECONDARY].email} does not share with {users[PRIMARY].email}")
            checks.append(
                Check(
                    "partner sharing",
                    False,
                    "; ".join(missing)
                    + ". Album membership transfer across users requires partner sharing in both "
                    "directions (Immich > Account Settings > Partner Sharing).",
                )
            )

    return report
