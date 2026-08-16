"""Serve the web UI backed by a fake Immich instance with seeded data.

For frontend development and demos — no real server needed:

    uv run python scripts/serve_fake.py [--port 8642]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.staticfiles import StaticFiles  # noqa: E402

from immich_dedup.core.config import DedupConfig  # noqa: E402
from immich_dedup.web.app import create_app  # noqa: E402
from immich_dedup.web.state import Session  # noqa: E402
from tests.fakes.immich_api import FakeImmich, make_client  # noqa: E402


def seed() -> tuple[FakeImmich, str, str, DedupConfig]:
    fake = FakeImmich()
    p_id, p_key = fake.add_user("alice@example.com", "Alice")
    s_id, s_key = fake.add_user("bob@example.com", "Bob")
    fake.set_partner(p_id, s_id)
    fake.set_partner(s_id, p_id)

    now = datetime.now(UTC)

    # three plain duplicate pairs
    for i in range(3):
        fake.add_asset(p_id, f"plain-{i}", file_name=f"IMG_{2000 + i}.jpg", created_at=now - timedelta(days=i), size_bytes=3_000_000)
        fake.add_asset(s_id, f"plain-{i}", file_name=f"IMG_{2000 + i}.jpg", created_at=now - timedelta(days=i), size_bytes=3_000_000)

    # duplicate pair shared via an album owned by each user
    keeper = fake.add_asset(p_id, "album-1", file_name="beach.jpg", created_at=now - timedelta(days=30), size_bytes=5_500_000)
    loser = fake.add_asset(s_id, "album-1", file_name="beach.jpg", created_at=now - timedelta(days=30), size_bytes=5_500_000)
    fake.add_album(s_id, "Summer trip", asset_ids=[loser])
    fake.add_album(p_id, "Favourites", asset_ids=[keeper])

    # live photo pair, both sides with motion
    fake.add_live_photo(p_id, "lp-1", "lp-1-m", file_name="cat.jpg", size_bytes=4_000_000)
    fake.add_live_photo(s_id, "lp-1", "lp-1-m", file_name="cat.jpg", size_bytes=4_000_000)

    # live photo where keeper lacks motion (policy case)
    fake.add_asset(p_id, "lp-2", file_name="dog.jpg", size_bytes=2_000_000)
    fake.add_live_photo(s_id, "lp-2", "lp-2-m", file_name="dog.jpg", size_bytes=2_000_000)

    # non-duplicate solo assets
    fake.add_asset(p_id, "solo-p", file_name="only-alice.jpg")
    fake.add_asset(s_id, "solo-s", file_name="only-bob.jpg")

    config = DedupConfig(
        immich_url="http://immich.test",
        primary_email="alice@example.com",
        secondary_email="bob@example.com",
        primary_api_key=p_key,
        secondary_api_key=s_key,
    )
    return fake, p_key, s_key, config


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8642)
    args = parser.parse_args()

    fake, p_key, s_key, config = seed()
    client = make_client(fake, p_key, s_key)
    session = Session(config=config, client=client, reports_dir=Path("reports"))
    app = create_app(session, web_dist=Path(__file__).resolve().parents[1] / "web" / "dist")
    print(f"Fake server on http://{args.host}:{args.port} (alice keeps, bob gets trashed)")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
