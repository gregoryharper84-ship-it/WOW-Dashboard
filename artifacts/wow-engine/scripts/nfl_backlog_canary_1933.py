#!/usr/bin/env python3
"""One-shot GitHub Actions canary for backlog #1933: strictly read-only NFL.

Downloads two allowlisted nflverse historical CSVs, checks exact-source
provenance against WOW's existing offline NFL prop training snapshot, and
prints a compact receipt. No data persistence, betting action, model updates,
credential access, or deployment.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from v17.multi_source_historical_collector import CollectionBlocked, download_public_source
from v17.nfl_multi_source_pilot import analyze_nfl_pilot, PLAYER_URL, SCHEDULE_URL
from v17.nfl_espn_independent_parity_1933 import compare_espn_date, fetch_espn_historical_scoreboard

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "wow_nfl_prop_training_report_v1.json"
SEASON = 2025


def reconcile_reference(report: dict[str, Any], source_snapshot: dict[str, Any]) -> dict[str, Any]:
    routes = report.get("routes", {})
    sources = []
    for route in ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"):
        item = (routes.get(route) or {}).get("source", {}).get("source_snapshot", {})
        asset = (item.get("assets") or {}).get(str(SEASON), {})
        if item.get("provider") != "NFLVERSE_STATS_PLAYER":
            raise CollectionBlocked("NFL_CANARY_REFERENCE_PROVIDER_MISMATCH", route)
        if not asset.get("sha256") or not asset.get("rows"):
            raise CollectionBlocked("NFL_CANARY_REFERENCE_UNAVAILABLE", route)
        sources.append((str(asset["sha256"]), int(asset["rows"]), str(asset.get("url") or "")))
    if len(set(sources)) != 1:
        raise CollectionBlocked("NFL_CANARY_REFERENCE_ROUTE_CONFLICT")
    reference_hash, reference_rows, reference_url = sources[0]
    actual_hash = source_snapshot["sha256"]
    actual_rows = source_snapshot["rows"]
    return {
        "source": "WOW_NFL_PROP_TRAINING_REPORT_V1",
        "expected_sha256": reference_hash,
        "observed_sha256": actual_hash,
        "expected_row_count": reference_rows,
        "observed_row_count": actual_rows,
        "expected_url": reference_url,
        "matches_frozen_source": actual_hash == reference_hash and actual_rows == reference_rows and source_snapshot["url"] == reference_url,
    }


def run(*, download=download_public_source, espn_download=None) -> dict[str, Any]:
    """Accept injected offline downloader in tests; production fetch is manual."""
    schedule = download(SCHEDULE_URL)
    players = download(PLAYER_URL.format(season=SEASON))
    pilot = analyze_nfl_pilot(season=SEASON, schedule_csv=schedule, player_csv=players)
    reference = json.loads(REPORT.read_text(encoding="utf-8"))
    assert pilot["can_execute"] is False
    assert pilot["ready_for_offline_training"] is False
    match = reconcile_reference(reference, pilot["sources"]["nflverse_stats_player"])
    parity = {"status": "NOT_CHECKED", "scope": "ONE_2025_REGULAR_SEASON_DATE", "full_season_parity_established": False}
    if espn_download is not None:
        try:
            parity = compare_espn_date(schedule_csv=schedule, espn_json=espn_download())
        except CollectionBlocked as exc:
            parity = {"status": "SOURCE_PARITY_BLOCKED", "typed_blocker": exc.code,
                      "scope": "ONE_2025_REGULAR_SEASON_DATE", "full_season_parity_established": False}
    blockers = []
    if not match["matches_frozen_source"]:
        blockers.append("SOURCE_REVISION_DRIFT")
    if pilot["counts"]["quarantined_identity_rows"]:
        blockers.append("NFL_PILOT_IDENTITY_ROWS_QUARANTINED")
    opportunity_status = pilot["counts"]["quarantine_opportunity_audit"]["status"]
    if opportunity_status in {"MISSING_COLUMNS", "NONZERO_OPPORTUNITY_HOLD", "INVALID_OPPORTUNITY_HOLD"}:
        blockers.append("NFL_PILOT_OPPORTUNITY_REVIEW_" + opportunity_status)
    if parity["status"] != "MATCHED":
        blockers.append("NFL_ESPN_INDEPENDENT_PARITY_" + parity["status"])
    if pilot["counts"]["held_unsettled_or_unjoined"] or not pilot["counts"]["joined_player_rows"]:
        blockers.append("JOIN_INCOMPLETE")
    status = "SOURCE_REFERENCE_AND_JOIN_VERIFIED" if not blockers else "SOURCE_VALIDATION_HOLD"
    return {
        "schema": "WOW_NFL_BACKLOG_CANARY_RECEIPT_V1",
        "status": status,
        "blockers": blockers,
        "season": SEASON,
        "captured_at": pilot["source_retrieved_at"],
        "pilot_status": pilot["status"],
        "source_rights_state": pilot["source_rights_state"],
        "reference": match,
        "source_manifest": pilot["sources"],
        "counts": pilot["counts"],
        "independent_espn_identity": pilot["independent_espn_identity"],
        "independent_espn_final_score_parity": parity,
        "research_only": True,
        "probability_publishable": False,
        "training_materialized": False,
        "can_execute": False,
    }


def main() -> int:
    try:
        receipt = run(espn_download=fetch_espn_historical_scoreboard)
    except CollectionBlocked as exc:
        print(json.dumps({
            "schema": "WOW_NFL_BACKLOG_CANARY_RECEIPT_V1",
            "status": "SOURCE_CANARY_BLOCKED",
            "typed_blocker": exc.code,
            "can_execute": False,
            "research_only": True,
        }, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({
            "schema": "WOW_NFL_BACKLOG_CANARY_RECEIPT_V1",
            "status": "SOURCE_CANARY_ERROR",
            "typed_blocker": type(exc).__name__,
            "can_execute": False,
            "research_only": True,
        }, sort_keys=True))
        return 3
    print(json.dumps(receipt, sort_keys=True))
    if receipt["status"] != "SOURCE_REFERENCE_AND_JOIN_VERIFIED":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
