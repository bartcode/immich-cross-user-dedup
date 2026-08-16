# immich-cross-user-dedup

Removes duplicate media shared across Immich users — via the public Immich API,
with a web UI. Built for the Google Photos migration problem: two (or more)
people imported overlapping Takeout exports, and Immich's duplicate detection
only works within a single user's library.

## Quick start

```sh
docker run --pull always -p 8642:8642 \
  -v immich-dedup-reports:/app/reports \
  ghcr.io/bartcode/immich-cross-user-dedup:main
```

Open **http://localhost:8642** and follow the wizard — the connection (your
Immich URL, the users, and an API key per user) is configured entirely in the
browser and persists in the container. The volume keeps reports and undo
journals outside the container.

Prefer a release pin (`:0.2`) over `:main` for repeatable runs — see
[Versioning](#versioning--releases).

### Before you connect

- An Immich API key per user (Account Settings → API Keys). The connection
  screen lists the exact scopes to select — and verifies every one against
  your server when you save, naming anything missing. No admin account needed.
- No partner sharing, database, or SSH access required.
- Take a fresh backup of Immich first: the tool only moves duplicates to the
  trash (never hard-deletes), but still.

## The wizard

1. **Connect** — the Immich URL, the *primary* user (keeps the photos), and any
   number of *secondaries* (their duplicates get trashed).
2. **Scan** — dry run: matches by checksum across all libraries and shows
   counts and reclaimable space. Nothing changes.
3. **Review** — side-by-side thumbnails per duplicate group; switch off
   anything that should stay in both libraries (multi-select, filters,
   sorting). A near-duplicates report flags byte-different variants (edits,
   re-encodes) for manual review.
4. **Apply** — the keeper joins every album that contained a duplicate (via a
   temporary editor share when needed, revoked afterwards), then the
   duplicates move to the trash. Deduplicated photos stay visible to everyone
   through those albums.
5. **Finish** — results, a re-scan verification, and undo.

**Undo**: every apply run is journaled and reversible until Immich purges the
trash (default 30 days) — restores trashed assets, album additions, and merged
metadata; already-purged items are reported. Space is reclaimed at purge time.

## Security notes

- API keys act with their user's permissions: the primary key never deletes
  anything; secondary keys only trash their *own* copies.
- The UI binds to localhost — reach it over an SSH tunnel
  (`ssh -L 8642:127.0.0.1:8642 your-server`) rather than exposing it, or run
  with `--token SECRET` if you must bind beyond localhost.
- Runs from any machine that can reach your Immich web UI.

## CLI

The image also ships a CLI for scripted runs (configured via flags or env —
see `.env.example`):

```sh
docker run --rm -v "$PWD/reports:/app/reports" \
  --entrypoint cross-user-dedup \
  ghcr.io/bartcode/immich-cross-user-dedup:main --apply --limit 20
```

`cross-user-dedup --help` lists everything: dry-run reports, fuzzy
near-duplicates, undo, extra secondary users.

## Versioning & releases

SemVer; the version lives in `src/immich_dedup/__init__.py` and shows in the
UI footer, `--version`, and the image tags (`main`, `X.Y.Z`, `X.Y`,
`sha-<commit>`). To release: bump, commit, `git tag vX.Y.Z && git push origin
vX.Y.Z` — CI builds and publishes the versioned images.

## Development

```sh
make setup && make test    # uv, pytest, ruff
make fake                  # web UI against a seeded fake Immich — try it without a server
make ui                    # web UI from source (frontend dev: web/npm run dev)
```

`make help` lists all targets. Tests run against an in-memory fake Immich API
that models the real permission rules, including apply → undo round trips.
MIT licensed.
