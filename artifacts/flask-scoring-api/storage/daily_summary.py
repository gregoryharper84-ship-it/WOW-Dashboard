"""
Single selection boundary for WOW daily summaries.

The latest committed/reconciled canonical manifest is authoritative.  Legacy
scan_results is consulted only when no committed canonical run exists.
"""
from __future__ import annotations

from typing import Any

from storage.daily_manifest import (
    get_latest_committed_run,
    get_run_source_flags,
    get_run_summary_counts,
    get_run_summary_rows,
)
from storage.results import (
    get_compact_scan_rows,
    get_scan_results,
    get_scan_source_flags,
    get_scan_summary,
)

CANONICAL_MANIFEST = "canonical_manifest"
LEGACY_SCAN_RESULTS = "legacy_scan_results"


def get_effective_daily_summary(
    run_date: str,
    *,
    category: str | None = None,
    sport: str | None = None,
    limit: int = 80,
    offset: int = 0,
    compact: bool = True,
) -> dict[str, Any]:
    """
    Select exactly one daily-summary store and return a compatible data bundle.

    A canonical lookup failure propagates: only an explicit None result proves
    that legacy fallback is permitted.
    """
    canonical_run = get_latest_committed_run(run_date)
    if canonical_run is not None:
        run_id = canonical_run["run_id"]
        return {
            "selected_source": CANONICAL_MANIFEST,
            "run_id": run_id,
            "run": canonical_run,
            "status": "completed",
            "summary_counts": get_run_summary_counts(run_id),
            "rows": get_run_summary_rows(
                run_id,
                category=category,
                sport=sport,
                limit=limit,
                offset=offset,
            ),
            "source_flags": get_run_source_flags(run_id),
        }

    summary_counts = get_scan_summary(run_date)
    if compact:
        legacy_rows = get_compact_scan_rows(
            run_date,
            category=category,
            limit=limit + offset,
        )
        if sport:
            legacy_rows = [
                row for row in legacy_rows
                if str(row.get("sport") or "").upper() == sport.upper()
            ]
        legacy_rows = legacy_rows[offset:offset + limit]
    else:
        legacy_rows = get_scan_results(
            run_date,
            sport=sport,
            classification=category,
            limit=limit,
            offset=offset,
        )

    return {
        "selected_source": LEGACY_SCAN_RESULTS,
        "run_id": None,
        "run": None,
        "status": "completed" if sum(summary_counts.values()) > 0 else "pending",
        "summary_counts": summary_counts,
        "rows": legacy_rows,
        "source_flags": get_scan_source_flags(run_date),
    }