"""Serve the web UI backed by a fake Immich instance with seeded data.

For frontend development and demos — no real server needed:

    uv run python scripts/serve_fake.py [--port 8642]

Seeds three users: alice (primary), bob and carol (secondaries).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from immich_dedup.core.config import DedupConfig, SecondaryCredentials  # noqa: E402
from immich_dedup.web.app import create_app  # noqa: E402
from immich_dedup.web.state import Session  # noqa: E402
from tests.fakes.immich_api import FakeImmich, make_client  # noqa: E402

PRIMARY_EMAIL = "alice@example.com"
SECONDARY_EMAILS = ("bob@example.com", "carol@example.com")


def seed() -> tuple[FakeImmich, DedupConfig]:
    fake = FakeImmich()
    ids = {}
    keys = {}
    for email in (PRIMARY_EMAIL, *SECONDARY_EMAILS):
        user_id, key = fake.add_user(email)
        ids[email], keys[email] = user_id, key
    # deliberately NO partner sharing: the demo exercises the album-editor
    # sharing fallback (affected albums are shared with the primary on apply)

    p, b, c = ids[PRIMARY_EMAIL], ids[SECONDARY_EMAILS[0]], ids[SECONDARY_EMAILS[1]]
    now = datetime.now(UTC)

    # plain triplicate: everyone imported it
    for i in range(3):
        taken_at = now - timedelta(days=i)
        for owner in (p, b, c):
            fake.add_asset(
                owner, f"plain-{i}", file_name=f"IMG_{2000 + i}.jpg", created_at=taken_at, size_bytes=3_000_000
            )

    # duplicate shared via an album owned by each user
    beach_taken_at = now - timedelta(days=30)
    keeper = fake.add_asset(p, "album-1", file_name="beach.jpg", created_at=beach_taken_at, size_bytes=5_500_000)
    bob_copy = fake.add_asset(b, "album-1", file_name="beach.jpg", created_at=beach_taken_at, size_bytes=5_500_000)
    fake.add_album(b, "Summer trip", asset_ids=[bob_copy])
    fake.add_album(p, "Favourites", asset_ids=[keeper])

    # only bob and carol have it — no primary copy, so scan skips and reports it
    fake.add_asset(b, "no-primary", file_name="concert.jpg", size_bytes=8_000_000)
    fake.add_asset(c, "no-primary", file_name="concert.jpg", size_bytes=8_000_000)

    # live photo triplicate, all sides with motion
    fake.add_live_photo(p, "lp-1", "lp-1-m", file_name="cat.jpg", size_bytes=4_000_000)
    fake.add_live_photo(b, "lp-1", "lp-1-m", file_name="cat.jpg", size_bytes=4_000_000)
    fake.add_live_photo(c, "lp-1", "lp-1-m", file_name="cat.jpg", size_bytes=4_000_000)

    # live photo where the keeper lacks motion (policy case)
    fake.add_asset(p, "lp-2", file_name="dog.jpg", size_bytes=2_000_000)
    fake.add_live_photo(c, "lp-2", "lp-2-m", file_name="dog.jpg", size_bytes=2_000_000)

    config = DedupConfig(
        immich_url="http://immich.test",
        primary_email=PRIMARY_EMAIL,
        primary_api_key=keys[PRIMARY_EMAIL],
        secondaries=tuple(SecondaryCredentials(email, keys[email]) for email in SECONDARY_EMAILS),
    )
    return fake, config


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    args = parser.parse_args()

    fake, config = seed()
    keys = {config.primary_email: config.primary_api_key}
    keys.update({secondary.email: secondary.api_key for secondary in config.secondaries})
    session = Session(config=config, client=make_client(fake, keys), reports_dir=Path("reports"))
    app = create_app(session, web_dist=Path(__file__).resolve().parents[1] / "web" / "dist")
    print(
        f"Fake server on http://{args.host}:{args.port} "
        f"({PRIMARY_EMAIL} keeps; {', '.join(SECONDARY_EMAILS)} get trashed)"
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
