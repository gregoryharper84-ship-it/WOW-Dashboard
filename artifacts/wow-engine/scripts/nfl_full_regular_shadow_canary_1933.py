#!/usr/bin/env python3
"""WOW #1933: strictly read-only one-shot 2025 regular-season ESPN parity run.

No credentials, production state, database writes, model scoring, or wagers.
"""
from __future__ import annotations

import hashlib
import json

from v17.multi_source_historical_collector import CollectionBlocked, download_public_source
from v17.nfl_multi_source_pilot import SCHEDULE_URL
from v17.nfl_espn_full_regular_shadow_1933 import fetch_and_compare_2025
from scripts.nfl_backlog_canary_1933 import PINNED_SCHEDULE_SHA256


def run(*, fetch_schedule=download_public_source, fetch_week=None):
    schedule_bytes = fetch_schedule(SCHEDULE_URL)
    digest = hashlib.sha256(schedule_bytes).hexdigest()
    if digest != PINNED_SCHEDULE_SHA256:
        raise CollectionBlocked("NFL_ESPN_FULL_PINNED_SCHEDULE_HASH_MISMATCH")
    opts = {"schedule_csv": schedule_bytes}
    if fetch_week is not None:
        opts["fetch_week"] = fetch_week
    parity = fetch_and_compare_2025(**opts)
    audit = {
        "schema": "WOW_NFL_BACKLOG_FULL_SEASON_RESEARCH_V1",
        "status": ("RESEARCH_SOURCE_VERIFIED" if parity["status"] == "MATCHED"
                   else "RESEARCH_SOURCE_PARTIAL_HOLD"),
        "pinned_schedule_url": SCHEDULE_URL,
        "pinned_schedule_sha256": digest,
        "expected_schedule_sha256": PINNED_SCHEDULE_SHA256,
        "full_regular_season_parity": parity["status"],
        "season": 2025,
        "schedule_games": parity["schedule_games"],
        "matched_games": parity["matched_games"],
        "unresolved_games": parity["unresolved_games"],
        "weekly_evidence": parity["weeks"],
        "unresolved_problems": parity["problems"],
        "crosswalk_count": len(parity["crosswalk"]),
        "crosswalk_sha256": hashlib.sha256(json.dumps(
            parity["crosswalk"], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest(),
        "source_rights_state": "LICENSE_REVIEW_REQUIRED",
        "espn_rights_state": "UNREVIEWED_FOR_STAGING_OR_TRAINING",
        "terminal_model_status": "UNCHANGED",
        "player_identity_quarantine": "18_RECORDS_UNRESOLVED_ON_SEPARATE_NFL_PILOT",
        "training_ready": False, "storage_write_authorized": False,
        "model_certified": False, "probability_publishable": False,
        "attestation_verified": False, "can_execute": False,
    }
    return audit


def main() -> int:
    try:
        result = run()
    except CollectionBlocked as exc:
        print(json.dumps({
            "schema": "WOW_NFL_BACKLOG_FULL_SEASON_RESEARCH_V1",
            "status": "RESEARCH_SOURCE_BLOCKED",
            "typed_blocker": exc.code,
            "training_ready": False, "can_execute": False,
        }, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({
            "schema": "WOW_NFL_BACKLOG_FULL_SEASON_RESEARCH_V1",
            "status": "RESEARCH_SOURCE_ERROR",
            "typed_blocker": type(exc).__name__,
            "training_ready": False, "can_execute": False,
        }, sort_keys=True))
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0 if (result["status"] == "RESEARCH_SOURCE_VERIFIED"
                 and result["matched_games"] == 272
                 and result["unresolved_games"] == 0
                 and result["model_certified"] is False
                 and result["training_ready"] is False
                 and result["can_execute"] is False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
