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
from v17.nfl_espn_independent_parity_1933 import (
    PILOT_DAYS, compare_espn_date, fetch_espn_historical_scoreboard,
)
from v17.nfl_research_evidence_gate_1933 import review_research_evidence

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "data" / "wow_nfl_prop_training_report_v1.json"
SEASON = 2025
# Bound to immutable nflverse/nfldata commit a2c9f13593a311b09bfd2d00851e55dbdafb3c8d
PINNED_SCHEDULE_SHA256 = "6011ae7b41fe3dcea1572b6e508fc8af3b8e03cf063df2681eb9e9a8c19a4332"


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
    by_day = []
    for day in PILOT_DAYS:
        parity = {"status": "NOT_CHECKED", "date": day, "scope": "ONE_2025_REGULAR_SEASON_DATE",
                  "full_season_parity_established": False}
        if espn_download is not None:
            try:
                payload = espn_download(game_day=day)
                parity = compare_espn_date(schedule_csv=schedule, espn_json=payload, game_day=day)
            except CollectionBlocked as exc:
                parity = {"status": "SOURCE_PARITY_BLOCKED", "date": day,
                          "typed_blocker": exc.code, "scope": "ONE_2025_REGULAR_SEASON_DATE",
                          "full_season_parity_established": False}
        by_day.append(parity)
    parity = by_day[0]
    extra_parity = by_day[1:]
    completed = [x for x in by_day if x["status"] == "MATCHED"]
    coverage = {
        "status": "MATCHED" if len(completed) == len(PILOT_DAYS) else "PARTIAL_HOLD",
        "dates_requested": list(PILOT_DAYS),
        "dates_matched": [x["date"] for x in completed],
        "games_verified": sum(x["matched_final_games"] for x in completed),
        "full_season_parity_established": False,
    }
    blockers = []
    if not match["matches_frozen_source"]:
        blockers.append("SOURCE_REVISION_DRIFT")
    if pilot["sources"]["nflverse_schedule"]["sha256"] != PINNED_SCHEDULE_SHA256:
        blockers.append("NFL_PILOT_FROZEN_SCHEDULE_HASH_MISMATCH")
    if pilot["counts"]["quarantined_identity_rows"]:
        blockers.append("NFL_PILOT_IDENTITY_ROWS_QUARANTINED")
    opportunity_status = pilot["counts"]["quarantine_opportunity_audit"]["status"]
    if opportunity_status in {"MISSING_COLUMNS", "NONZERO_OPPORTUNITY_HOLD", "INVALID_OPPORTUNITY_HOLD"}:
        blockers.append("NFL_PILOT_OPPORTUNITY_REVIEW_" + opportunity_status)
    if coverage["status"] != "MATCHED":
        blockers.append("NFL_ESPN_INDEPENDENT_MULTI_DATE_PARITY_HOLD")
    if pilot["counts"]["held_unsettled_or_unjoined"] or not pilot["counts"]["joined_player_rows"]:
        blockers.append("JOIN_INCOMPLETE")
    status = "SOURCE_REFERENCE_AND_JOIN_VERIFIED" if not blockers else "SOURCE_VALIDATION_HOLD"
    receipt = {
        "schema": "WOW_NFL_BACKLOG_CANARY_RECEIPT_V1",
        "status": status,
        "blockers": blockers,
        "season": SEASON,
        "captured_at": pilot["source_retrieved_at"],
        "pilot_status": pilot["status"],
        "source_rights_state": pilot["source_rights_state"],
        "reference": match,
        "pinned_schedule_expected_sha256": PINNED_SCHEDULE_SHA256,
        "source_manifest": pilot["sources"],
        "counts": pilot["counts"],
        "independent_espn_identity": pilot["independent_espn_identity"],
        "independent_espn_final_score_parity": parity,
        "independent_espn_additional_date_parity": extra_parity,
        "independent_espn_coverage": coverage,
        "research_only": True,
        "probability_publishable": False,
        "training_materialized": False,
        "can_execute": False,
    }
    receipt["research_evidence_gate"] = review_research_evidence(receipt)
    return receipt


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
    gate = receipt.get("research_evidence_gate") or {}
    # This GitHub job validates public research snapshots, NOT the model,
    # source-entitlement approval or production release. The terminal hold
    # remains in the receipt even when research evidence is coherent.
    if (gate.get("status") == "RESEARCH_EVIDENCE_AUDIT_CANDIDATE"
            and gate.get("audit_coherent") is True
            and gate.get("attestation_verified") is False
            and gate.get("training_ready") is False
            and gate.get("storage_write_authorized") is False
            and gate.get("model_certified") is False
            and gate.get("can_execute") is False
            and receipt.get("source_rights_state") == "LICENSE_REVIEW_REQUIRED"
            and receipt.get("status") == "SOURCE_VALIDATION_HOLD"
            and receipt.get("blockers") == ["NFL_PILOT_IDENTITY_ROWS_QUARANTINED"]
            and receipt.get("training_materialized") is False
            and receipt.get("probability_publishable") is False
            and receipt.get("can_execute") is False):
        return 0  # RESEARCH CI PASS; MODEL & LICENSING HOLD UNCHANGED
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
