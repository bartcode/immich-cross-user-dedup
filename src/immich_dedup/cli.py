"""Command-line interface.

Modes:
  cross-user-dedup                 # dry-run: scan + CSV report + summary
  cross-user-dedup --apply [...]   # apply: album transfer + trash, journaled
  cross-user-dedup --fuzzy         # additionally list byte-different near-duplicates
  cross-user-dedup --undo FILE     # reverse-replay a journal
  cross-user-dedup-ui              # web UI (separate entry point)
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from immich_dedup.core.api import ImmichClient
from immich_dedup.core.apply import MOTION_SKIP, MOTION_TRASH, ApplyOptions, apply_pairs
from immich_dedup.core.config import load_config
from immich_dedup.core.journal import Journal, undo_journal
from immich_dedup.core.match import _user_assets, fuzzy_candidates, scan
from immich_dedup.core.preflight import run_preflight
from immich_dedup.core.report import summary_text, write_csv, write_fuzzy_csv


def _progress(stage: str, current: int, total: int | None) -> None:
    suffix = f" of {total}" if total is not None else ""
    print(f"  [{stage}] {current}{suffix}", end="\r", flush=True)


def _done_line() -> None:
    print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cross-user-dedup",
        description="Remove duplicate media shared by two Immich users (via the public API).",
    )
    parser.add_argument("--env-file", default=None, help="path to a .env file (default: ./.env if present)")
    parser.add_argument("--immich-url", default=None)
    parser.add_argument("--primary-email", default=None)
    parser.add_argument("--secondary-email", default=None)
    parser.add_argument("--primary-api-key", default=None)
    parser.add_argument("--secondary-api-key", default=None)
    parser.add_argument(
        "--reports-dir",
        default=None,
        help="directory for reports and journals (default: reports/)",
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="apply the dedup (album transfer + trash losers)")
    mode.add_argument("--undo", metavar="JOURNAL", help="reverse-replay a journal file")

    parser.add_argument("--fuzzy", action="store_true", help="also report byte-different near-duplicates")
    parser.add_argument("--limit", type=int, default=None, help="apply to at most N pairs")
    parser.add_argument(
        "--merge-metadata",
        action="store_true",
        help="merge favorites and descriptions from losers onto keepers",
    )
    parser.add_argument(
        "--live-photo-motion",
        choices=(MOTION_TRASH, MOTION_SKIP),
        default=MOTION_TRASH,
        help="policy when the keeper lacks the loser's motion video (default: trash both)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = load_config(
            args.env_file,
            overrides={
                "IMMICH_URL": args.immich_url,
                "PRIMARY_EMAIL": args.primary_email,
                "SECONDARY_EMAIL": args.secondary_email,
                "PRIMARY_API_KEY": args.primary_api_key,
                "SECONDARY_API_KEY": args.secondary_api_key,
            },
        )
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.reports_dir is not None:
        config = replace(config, reports_dir=Path(args.reports_dir))

    if args.undo:
        return _undo(Path(args.undo), config)

    return _scan_and_maybe_apply(config, args)


def _make_client(config):
    return ImmichClient(config.immich_url, config.primary_api_key, config.secondary_api_key)


def _undo(journal_path: Path, config) -> int:
    if not journal_path.exists():
        print(f"error: journal file not found: {journal_path}", file=sys.stderr)
        return 2
    with _make_client(config) as client:
        report = run_preflight(client, config)
        if report.failed:
            _print_preflight(report)
            return 1
        print(f"Undoing journal {journal_path} ...")
        outcome = undo_journal(client, Journal(journal_path), progress=_progress)
        _done_line()
        print(
            f"  restored assets:     {outcome.restored_assets}\n"
            f"  unrestorable:        {len(outcome.unrestorable)} (already purged by Immich)\n"
            f"  album rows removed:  {outcome.album_rows_removed}\n"
            f"  album rows kept:     {outcome.album_rows_kept} (loser unrestorable)\n"
            f"  metadata restored:   {outcome.metadata_restored}"
        )
        if outcome.errors:
            print("  errors:")
            for error in outcome.errors:
                print(f"    - {error}")
        return 0 if not outcome.errors else 1


def _scan_and_maybe_apply(config, args) -> int:
    with _make_client(config) as client:
        print("Pre-flight checks")
        report = run_preflight(client, config)
        _print_preflight(report)
        if report.failed:
            return 1

        print("\nScanning libraries ...")
        result = scan(client, report.primary, report.secondary, progress=_progress)
        _done_line()

        csv_path = write_csv(result, config.reports_dir / "dedup_report.csv", config.immich_url)
        print(summary_text(result))
        print(f"\nReport written to {csv_path}")

        if args.fuzzy:
            fuzzy = fuzzy_candidates(
                _user_assets(client, result.primary, None), _user_assets(client, result.secondary, None)
            )
            fuzzy_path = write_fuzzy_csv(fuzzy, config.reports_dir / "dedup_fuzzy.csv", config.immich_url)
            print(f"Fuzzy near-duplicates: {len(fuzzy)} (report only) -> {fuzzy_path}")

        if not args.apply:
            print("\nDry run only. Re-run with --apply to transfer albums and trash the losers.")
            return 0

        if result.stats.pair_count == 0:
            return 0

        options = ApplyOptions(
            merge_metadata=args.merge_metadata,
            live_photo_motion=args.live_photo_motion,
            limit=args.limit,
        )
        journal = Journal(config.reports_dir / f"dedup_apply_{_timestamp()}.jsonl")
        print("\nApplying ...")
        try:
            outcome = apply_pairs(client, result, options, journal, progress=_progress)
        finally:
            journal.close()
        _done_line()
        print(outcome.summary())
        print(f"\nJournal: {journal.path}")
        print("Undo (until Immich purges the trash) with:")
        print(f"  cross-user-dedup --undo '{journal.path}'")
        return 0 if not outcome.errors else 1


def _print_preflight(report) -> None:
    for check in report.checks:
        marker = "ok  " if check.ok else "FAIL"
        print(f"  [{marker}] {check.name}: {check.detail}")


def _timestamp() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).strftime("%Y%m%d-%H%M%S")


if __name__ == "__main__":
    sys.exit(main())
