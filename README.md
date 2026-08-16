# immich-cross-user-dedup

Remove duplicate media shared by two Immich users — through the public Immich API,
with a CLI and a web UI. No database, filesystem, or SSH access to the Immich
server required.

Built for the classic Google Photos migration problem: two people exported the
same shared albums from Google Photos (Takeout) and imported both exports into
Immich, so every shared photo exists once per account. Immich's built-in
duplicate detection only works within a single user's library, so these
cross-user duplicates persist silently.

## How it works

1. **Scan** — fetches both users' assets via the API and groups them by SHA-1
   checksum (`asset.checksum`, computed by Immich at upload). Same bytes on both
   sides = duplicate pair. The *primary* user's copy is the **keeper**; the
   *secondary* user's copy is the **loser**.
2. **Review** — inspect pairs (web UI with side-by-side thumbnails, or the CSV
   report) and exclude any pair you want to keep in both libraries.
3. **Apply** — for every remaining pair:
   - the keeper is added to **every album** that contained the loser (queried
     with both users' keys, so albums owned by either user are covered);
   - optionally, the loser's favorite flag / description is merged onto the
     keeper (`--merge-metadata`);
   - the loser is moved to the **trash** via `DELETE /assets` (`force: false`) —
     exactly what Immich's own trash does. Live-photo motion videos are trashed
     together with their still so no invisible orphans linger.
4. **Undo** — every action is journaled (`reports/dedup_apply_*.jsonl`).
   `--undo <journal>` restores trashed assets, removes the album additions made
   by the run, and reverts metadata merges. This works until Immich's purge job
   hard-deletes trashed assets (default: 30 days); already-purged assets are
   reported and skipped.

Immich itself handles all heavy lifting after the trash: purging originals and
generated thumbnails/previews/encoded videos, quota updates, stack maintenance.

### How album access works (partner sharing is optional)

After dedup, each photo has exactly one owner (the primary user). Every other
user still sees all of it because:

1. **Shared albums — automatic.** The keeper joins every album that contained a
   loser, and albums can contain assets from multiple users in Immich. If a
   secondary had the photo in "Summer trip", they still see it there.
2. **How the keeper gets into other users' albums** — one of two ways, chosen
   automatically per album:
   - *Direct* (fast path): if the primary shares partner sharing with the album
     owner, the owner adds the keeper with their own key.
   - *Album-editor sharing* (no partner sharing needed): the album owner's key
     shares **that one album** with the primary as editor (`albumUser.create`),
     the primary adds their own keeper, and undo revokes the share afterwards.
     Nobody gets access to anyone's full library.
3. **Partner sharing is a privacy trade-off, not a requirement.** Without it,
   secondaries keep seeing deduped photos through the albums they always used,
   but lose *timeline* visibility of duplicates that weren't in any album. With
   it (either direction), they'd see the primary's whole timeline instead. The
   pre-flight check reports which mode applies per user — it never fails on
   missing partner sharing.
4. **Other trade-offs.** The secondaries lose direct ownership of the shared
   copies: they can no longer independently trash/re-upload them, and their
   copies' face-detection samples disappear with the purge (the primary's
   copies keep theirs; people are per-owner in Immich).

## Multiple users

One primary plus any number of secondaries. Every user's library is scanned;
checksum groups with a primary-owned copy become **one keeper + N losers**
(processed independently per loser). Groups the primary never imported — only
secondaries own a copy — are **skipped and reported** rather than deduplicated
automatically, so no unexpected ownership decisions happen. Per-user stats show
what apply would trash from each library.

## Prerequisites

- Immich v2/v3 with API access enabled
- An API key for the primary and for **every** secondary, with the scopes listed
  under [API key scopes](#api-key-scopes) (Account Settings → API Keys)
- Partner sharing is **optional** (see "How album access works" above)
- A current backup of your Immich instance (the tool only trashes, but still)

## API key scopes

When you create an API key in Immich (Account Settings → API Keys), you select
which permission **scopes** it gets — a key can only call endpoints whose scope
it carries (the `all` scope grants everything). No admin account is required.
These are the scopes to select:

**Primary key** — never deletes anything:

```text
user.read · partner.read · asset.read · asset.view · album.read ·
albumAsset.create · albumAsset.delete
(+ asset.update only if you use --merge-metadata)
```

**Each secondary key** — same as the primary, plus the trash and album-sharing
scopes:

```text
user.read · partner.read · asset.read · asset.view · album.read ·
albumAsset.create · albumAsset.delete · albumUser.create · albumUser.delete ·
asset.delete
```

How the scopes map to the calls this tool makes:

| call | scope | primary | secondaries |
| --- | --- | --- | --- |
| `GET /users/me` (pre-flight) | `user.read` | ✓ | ✓ |
| `GET /partners` (pre-flight) | `partner.read` | ✓ | ✓ |
| `POST /search/metadata` (list own library) | `asset.read` | ✓ | ✓ |
| `GET /albums` (find albums containing a copy) | `album.read` | ✓ | ✓ |
| `GET /assets/{id}` (undo checks) | `asset.read` | ✓ | ✓ |
| `GET /assets/{id}/thumbnail` (previews) | `asset.view` | ✓ | ✓ |
| `PUT /albums/{id}/assets` (keeper joins albums) | `albumAsset.create` | ✓ | ✓ |
| `DELETE /albums/{id}/assets` (undo of those additions) | `albumAsset.delete` | ✓ | ✓ |
| `PUT /albums/{id}/users` (album-editor sharing fallback, no partner sharing) | `albumUser.create` | — | ✓ |
| `DELETE /albums/{id}/user/{uid}` (revoke that share on undo) | `albumUser.delete` | — | ✓ |
| `PUT /assets/{id}` (merge favorites/descriptions, and revert on undo) | `asset.update` | optional | — |
| `DELETE /assets` with `force: false` (trash **own** copies only) | `asset.delete` | — | ✓ |
| `POST /trash/restore/assets` (undo) | `asset.delete` | — | ✓ |

(`albumUser.create`/`albumUser.delete` are only exercised when partner sharing
is absent; include them in the secondary key anyway so the fallback works.)

The read scopes (`user.read`, `partner.read`, `asset.read`, `album.read`) are
verified per key by the pre-flight check — Immich's error message names any
missing scope. The write scopes are exercised at apply time, and the tool never
hard-deletes, never modifies another user's assets, and never changes server
settings. The connection form in the web UI shows each key's scope list inline.

## Setup

Requires [uv](https://docs.astral.sh/uv/) (Python 3.11+):

```sh
git clone https://github.com/bartcode/immich-cross-user-dedup.git
cd immich-cross-user-dedup
make setup
```

For the **web UI** you can skip the `.env` entirely — run `make ui` and configure
the connection (URL, primary, and any number of secondaries with their API
keys) in the browser; it's saved to your local `.env` automatically. For the
**CLI**, create the config file upfront (`SECONDARY_EMAILS` / `SECONDARY_API_KEYS`
comma lists, or the legacy single `SECONDARY_EMAIL` / `SECONDARY_API_KEY`):

```sh
cp .env.example .env   # fill in your values
```

There's a `Makefile` for the common tasks — `make help` lists them:

| command | what it does |
| --- | --- |
| `make ui` | run the web UI (`ARGS='--port 9000'` to customize) |
| `make fake` | run the web UI against fake seeded data, no `.env` needed |
| `make cli` | CLI dry-run report (`ARGS='--apply --limit 20'` to apply) |
| `make test` / `make lint` | pytest / ruff + frontend lint |
| `make build` | rebuild the frontend (commit `web/dist` afterwards) |

The web frontend is pre-built (`web/dist`), so the server needs no Node.js.
Set `IMMICH_URL` to a URL reachable from where you run the tool.

## CLI usage

```sh
# Dry run: scan + CSV report + summary (changes nothing)
uv run cross-user-dedup

# Near-duplicates that differ byte-wise (edits/re-encodes), report only
uv run cross-user-dedup --fuzzy

# Apply: transfer albums + trash losers, journaled
uv run cross-user-dedup --apply
uv run cross-user-dedup --apply --limit 20          # small batch first
uv run cross-user-dedup --apply --merge-metadata    # favorites/descriptions
uv run cross-user-dedup --apply --live-photo-motion skip  # keep asymmetric live photos

# Extra secondary users on the command line (repeatable)
uv run cross-user-dedup --secondary bob@example.com bobs-key --secondary carol@example.com carols-key

# Undo an apply run (until Immich purges the trash)
uv run cross-user-dedup --undo reports/dedup_apply_<timestamp>.jsonl
```

Reports land in `reports/`: `dedup_report.csv` (one row per pair, with Immich
web URLs for spot-checking), `dedup_fuzzy.csv`, and the apply journals.

## Web UI

```sh
uv run cross-user-dedup-ui                 # http://127.0.0.1:8642
uv run cross-user-dedup-ui --token SECRET  # require Bearer token on /api
uv run cross-user-dedup-ui --host 0.0.0.0 --port 8080  # expose (use with token!)
```

The UI mirrors the pipeline: a step bar (Scan → Review → Apply → Done), overview
cards (pairs, exclusions, live-photo cases, reclaimable space), a pair browser
with side-by-side thumbnails and per-pair exclude toggles, an apply panel with a
confirmation dialog, and an undo panel listing journals with a preview of what
undo restores. One background job runs at a time, with live progress. The
connection (Immich URL, both emails, both API keys) is set in the browser on
first run — or changed later via the "Connection" button in the header — and is
persisted to your local `.env`.

### Running from another machine

The tool talks only to the public Immich API, so it runs from any machine that
can reach your Immich web UI — no SSH, database, or filesystem access needed.

- **Immich reachable on your network** (LAN IP, domain, reverse proxy): just
  clone, `make setup`, and set `IMMICH_URL` to the address you use in the
  browser.
- **Immich only exposed on its host**: forward the port over SSH and point the
  tool at it: `ssh -L 2283:localhost:2283 user@immich-host`, then
  `IMMICH_URL=http://localhost:2283`.
- **UI on machine A, browser on machine B**: the UI binds to `127.0.0.1` by
  default — tunnel it (`ssh -L 8642:127.0.0.1:8642 machine-a`) or bind wider
  with `--host 0.0.0.0 --token SECRET` (always set a token when binding beyond
  localhost).

Thumbnails are fetched by the backend and proxied to your browser, so the
browser only needs to reach the machine running the UI. API keys live only in
your local `.env` — treat them like passwords.

To use it from another machine, prefer an SSH tunnel over exposing the port:
`ssh -L 8642:127.0.0.1:8642 your-server`.

### Demo without a real server

```sh
uv run python scripts/serve_fake.py   # seeds a fake Immich with test data
```

## Notes & caveats

- **Checksums are trusted.** Matching relies on Immich's upload-time SHA-1; the
  API offers no independent byte verification.
- **Space is reclaimed at purge time.** The reported reclaimable size counts
  originals only; Immich additionally removes generated previews/thumbnails.
- During trash retention, affected albums show both copies side by side, and
  album covers may reset (cosmetic).
- The fuzzy tier is metadata-based (same type + filename + timestamp ±2s + size
  within 1%) and report-only — review those pairs by hand.
- Large libraries: scanning pages through `/search/metadata` (1000/page); a
  50k-asset library takes a few hundred requests.

## Development

```sh
uv run pytest              # unit tests (fast, no services needed)
uv run ruff check .
cd web && npm install && npm run dev   # frontend dev server (proxies /api to :8642)
npm run build                          # rebuild web/dist (commit the result)
```

Tests run against an in-memory fake Immich API (`tests/fakes/immich_api.py`)
that models the relevant permission rules (owner-only deletes, partner-gated
album adds, partner assets appearing in search). Round-trip coverage includes:
apply → idempotent re-apply → undo restores the exact prior state.

## License

MIT
