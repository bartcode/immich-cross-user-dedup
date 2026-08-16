# immich-cross-user-dedup

Remove duplicate media shared by two Immich users — via the public Immich API, no
database or filesystem access required.

Built for the classic Google Photos migration problem: two people exported the same
shared albums from Google Photos and imported both exports into Immich, leaving every
shared photo duplicated across the two accounts. Immich's built-in duplicate detection
only works within a single user's library, so cross-user duplicates persist silently.

Status: work in progress. See the plan in the commit history; full documentation
 lands with the first release.

## How it works

1. **Scan** — fetches both users' assets through the API and groups them by SHA-1
   checksum. Same bytes on both sides = duplicate pair.
2. **Review** — inspect pairs (CLI CSV report or the web UI) and exclude any you
   want to keep in both libraries.
3. **Apply** — for every pair: add the primary user's copy to each album that
   contained the secondary user's copy, then move the secondary's copy to the trash.
4. **Undo** (until Immich purges the trash) — every action is journaled and can be
   reversed.
