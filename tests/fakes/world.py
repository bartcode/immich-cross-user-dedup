"""Shared test fixture: a fake Immich world with a primary and N secondaries."""

from __future__ import annotations

from immich_dedup.core.config import DedupConfig, SecondaryCredentials
from immich_dedup.core.preflight import run_preflight

from .immich_api import BASE, FakeImmich, make_client

PRIMARY_EMAIL = "primary@example.com"


class World:
    def __init__(self, secondary_emails: tuple[str, ...] = ("secondary@example.com",)):
        self.fake = FakeImmich()
        self.p_id, self.p_key = self.fake.add_user(PRIMARY_EMAIL)
        self.secondary: dict[str, tuple[str, str]] = {}  # email -> (user_id, api_key)
        keys = {PRIMARY_EMAIL: self.p_key}
        for email in secondary_emails:
            user_id, key = self.fake.add_user(email)
            self.secondary[email] = (user_id, key)
            keys[email] = key
        # partner star around the primary
        for user_id, _ in self.secondary.values():
            self.fake.set_partner(self.p_id, user_id)
            self.fake.set_partner(user_id, self.p_id)
        self.keys = keys
        self.client = make_client(self.fake, keys)
        self.config = DedupConfig(
            immich_url=BASE,
            primary_email=PRIMARY_EMAIL,
            primary_api_key=self.p_key,
            secondaries=tuple(
                SecondaryCredentials(email, key[1]) for email, key in self.secondary.items()
            ),
        )

    def preflight(self):
        return run_preflight(self.client, self.config)

    def users(self, strict: bool = True):
        """(primary User, [secondary Users], registry) from pre-flight.

        With ``strict=False`` a failing pre-flight (e.g. dropped partner sharing
        in the test scenario) is tolerated — users that resolved are returned."""
        report = self.preflight()
        if strict:
            assert not report.failed, [c.detail for c in report.checks if not c.ok]
            return report.primary, report.secondaries, report.users
        registry = dict(report.users)
        secondaries = [
            registry.pop(user.id) for user in report.secondaries if user.id in registry
        ]
        primary = report.primary
        if primary is not None and primary.id in registry:
            primary = registry.pop(primary.id)
        users = {user.id: user for user in ([primary] if primary else []) + secondaries}
        return primary, secondaries, users

    @property
    def s_id(self) -> str:
        """First secondary's user id (single-secondary worlds)."""
        return next(iter(self.secondary.values()))[0]
