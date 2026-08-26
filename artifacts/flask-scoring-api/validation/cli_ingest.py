"""
validation/cli_ingest.py

WOW 1IP Outcome Ingestion CLI.

Usage
-----
    python -m validation.cli_ingest [options]

Options
-------
  --dry-run          Fetch Savant data but do NOT write outcomes (DEFAULT).
  --no-dry-run       Write outcomes to wow_validation_outcome_log.
  --before YYYY-MM-DD  Only process predictions with game_date < this date.
                       Default: today (UTC).
  --after  YYYY-MM-DD  Only process predictions with game_date >= this date.
                       Optional lower bound.
  --max-rows N       Max predictions to process per run (default 50, max 500).
  --out PATH         Write machine-readable JSON run report to this file.
  --verbose          Log per-row detail.

Exit codes
----------
  0  Success (all attached, or dry-run completed, or zero unresolved rows).
  1  Partial failure (some rows had errors; see summary).
  2  Total failure (DB unavailable or top-level error).

Cron example (attach outcomes for yesterday's games, log to file)
-----------------------------------------------------------------
  0 8 * * * cd /app && python -m validation.cli_ingest \
      --no-dry-run --max-rows 100 \
      --out /var/log/wow_1ip_ingest_$(date +%%Y%%m%%d).json >> /var/log/wow_1ip_ingest.log 2>&1
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone


logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
    stream=sys.stderr,
)
logger = logging.getLogger("wow.cli_ingest")

# Module-level import for test patchability
from validation.outcome_ingestion import ingest_outcomes  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog       = "python -m validation.cli_ingest",
        description= "WOW 1IP Automated Outcome Ingestion",
    )
    dry_group = p.add_mutually_exclusive_group()
    dry_group.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=True,
        help="Fetch data but do NOT write outcomes (DEFAULT — safe)",
    )
    dry_group.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="Write fetched outcomes to wow_validation_outcome_log",
    )
    p.add_argument(
        "--before", dest="before_date", metavar="YYYY-MM-DD", default=None,
        help="Process predictions with game_date < this date (default: today UTC)",
    )
    p.add_argument(
        "--after", dest="after_date", metavar="YYYY-MM-DD", default=None,
        help="Process predictions with game_date >= this date (no lower bound by default)",
    )
    p.add_argument(
        "--max-rows", dest="max_rows", type=int, default=50, metavar="N",
        help="Maximum predictions to process in this run (default 50, max 500)",
    )
    p.add_argument(
        "--out", dest="out_path", default=None, metavar="PATH",
        help="Write JSON run report to this file path",
    )
    p.add_argument(
        "--verbose", dest="verbose", action="store_true", default=False,
        help="Log per-row detail at INFO level",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.dry_run:
        logger.info("=== WOW 1IP Outcome Ingestion (DRY RUN — no writes) ===")
    else:
        logger.warning("=== WOW 1IP Outcome Ingestion (LIVE — outcomes will be written) ===")

    try:
        result = ingest_outcomes(
            dry_run       = args.dry_run,
            before_date   = args.before_date,
            after_date    = args.after_date,
            max_rows      = args.max_rows,
            verbose       = args.verbose,
        )
    except Exception as e:
        logger.error("Fatal error during ingestion: %s", e)
        return 2

    # ── Print summary ─────────────────────────────────────────────────────
    print(f"\n=== WOW 1IP Outcome Ingestion Run Summary ===")
    print(f"  Timestamp:           {result.run_timestamp}")
    print(f"  Dry-run:             {result.dry_run}")
    print(f"  Before date:         {result.before_date}")
    print(f"  After date:          {result.after_date or '(none)'}")
    print(f"  Predictions queried: {result.predictions_queried}")
    print(f"  Rows processed:      {len(result.rows)}")
    print(f"  Attached (or DRY):   {result.n_attached}")

    if result.top_level_error:
        print(f"\n  ✗ Top-level error: {result.top_level_error}")
    else:
        print(f"\n  Per-status breakdown:")
        for status, count in sorted(result.summary.items()):
            icon = "✓" if status in ("ATTACHED", "DRY_RUN", "ALREADY_SETTLED") else "✗"
            print(f"    {icon} {status:<30} {count}")

    if args.verbose:
        print("\n  Per-row detail:")
        for row in result.rows:
            print(f"    [{row.status:<28}] {row.game_date} | {row.pitcher_name} "
                  f"| line={row.line}{row.direction[0]} | pitches={row.pitch_count}"
                  f" | {row.detail or ''}")

    # ── Write JSON report ─────────────────────────────────────────────────
    report = result.to_dict()
    if args.out_path:
        try:
            with open(args.out_path, "w") as f:
                json.dump(report, f, indent=2, default=str)
            logger.info("Report written to %s", args.out_path)
        except Exception as e:
            logger.error("Failed to write JSON report: %s", e)

    # Print compact JSON to stdout for machine parsing
    print("\nJSON Summary:")
    compact = {
        "dry_run":             result.dry_run,
        "run_timestamp":       result.run_timestamp,
        "predictions_queried": result.predictions_queried,
        "rows_processed":      len(result.rows),
        "n_attached":          result.n_attached,
        "summary":             result.summary,
        "top_level_error":     result.top_level_error,
    }
    print(json.dumps(compact, indent=2))

    # ── Exit code ─────────────────────────────────────────────────────────
    if result.top_level_error:
        return 2
    error_statuses = {"FETCH_ERROR", "AMBIGUOUS_DOUBLEHEADER", "OUTCOME_ATTACH_ERROR",
                      "IDENTITY_MISMATCH", "DB_UNAVAILABLE"}
    n_errors = sum(1 for r in result.rows if r.status in error_statuses)
    return 1 if n_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
