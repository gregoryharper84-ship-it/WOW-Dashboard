"""Regression for run-level Daily COMPACT response growth (issue #553)."""

from v17.daily_response_contract import compact_response, serialized_byte_size


CLIENT_RESPONSE_LIMIT_BYTES = 100_000
COMPACT_RESPONSE_BUDGET_BYTES = 50_000


def _receipt(index: int) -> dict:
    return {
        "event_id": f"CFB:{index}",
        "event_start_time": "2026-09-18T23:00:00+00:00",
        "sport": "CFB",
        "player": f"Player {index}",
        "stat_type": "PASSING_YARDS",
        "line": 287.5,
        "side": "LESS",
        "source_snapshot_id": f"snap-{index}",
        "write_status": "SNAPSHOT_WRITE_SUCCEEDED",
        "terminal_reason": "PERSISTED_AWAITING_CANONICAL_READBACK",
        "can_execute": False,
    }


def _blocked_row(index: int) -> dict:
    return {
        "lane": "PROPS",
        "identity": {
            "event_id": f"CFB:{index}",
            "player": f"Player {index}",
            "stat_type": "PASSING_YARDS",
            "line": 287.5,
        },
        "terminal": True,
        "row_status": "HELD",
        "probability_publishable": False,
        "terminal_reduction": {
            "lowest_stage_terminal": "HELD",
            "final_terminal": "HELD",
            "terminal_upgraded_from_rejection": False,
        },
        "result": {
            "outcomes": [
                {
                    "direction": "LESS",
                    "status": "HELD",
                    "payload": {
                        "code": "MODEL_UNAVAILABLE",
                        "terminal_label": "MODEL_UNAVAILABLE",
                        "model_evaluated": False,
                        "model_qualified": False,
                        "probability_publishable": False,
                        "rank_eligible": False,
                        "blockers": ["CAPABILITY_BLOCKED"],
                        "can_execute": False,
                    },
                }
            ]
        },
        "can_execute": False,
    }


def _cross_sport_row(index: int) -> dict:
    return {
        "sport": "NCAAF",
        "official_event_id": f"event-{index}",
        "home_team": f"Home {index}",
        "away_team": f"Away {index}",
        "bucket": "MODEL_UNAVAILABLE",
        "model_status": "MODEL_UNAVAILABLE",
        "detail": {
            "blocker": "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED",
            "diagnostic": "x" * 350,
        },
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _large_acquisition(receipts: list[dict]) -> dict:
    return {
        "status": "COMPLETED",
        "attempted": len(receipts),
        "hydrated": len(receipts),
        "persisted": len(receipts),
        "held": 0,
        "snapshot_write_succeeded": len(receipts),
        "snapshot_write_failed": 0,
        "explicit_prewrite_exclusions": 0,
        "candidate_source": "TEST_SOURCE",
        "line_source": "TEST_LINE_SOURCE",
        "receipts": receipts,
        "blockers": [],
        "can_execute": False,
    }


def _cross_sport_audit(row_count: int = 750) -> dict:
    acquisition_audit = [
        {
            "family": f"SPORT_{index}",
            "request_status": "EVENTS_RETURNED",
            "events_returned": 25,
            "diagnostic": "a" * 180,
            "can_execute": False,
        }
        for index in range(30)
    ]
    source_blockers = [
        {"family": f"SPORT_{index}", "blocker": "SOURCE_DELAYED", "detail": "b" * 180}
        for index in range(30)
    ]
    return {
        "discovery": {
            "objective": "OUTRIGHT_WINNER",
            "requested_slate_date": "2026-09-18",
            "requested_timezone": "America/Chicago",
            "sports_queried": [f"SPORT_{index}" for index in range(30)],
            "sports_with_events": [f"SPORT_{index}" for index in range(24)],
            "events_discovered": row_count,
            "source_blockers": source_blockers,
            "acquisition_audit": acquisition_audit,
            "discovery_independent_of_model_registry": True,
            "can_execute": False,
        },
        "rows": [_cross_sport_row(index) for index in range(row_count)],
        "reconciliation": {
            "audit": "CROSS_SPORT_DISCOVERY_AUDIT",
            "objective": "OUTRIGHT_WINNER",
            "requested_slate_date": "2026-09-18",
            "sports_queried": 30,
            "sports_with_events": 24,
            "events_discovered": row_count,
            "events_accounted": row_count,
            "buckets": {"MODEL_UNAVAILABLE": row_count},
            "model_supported_rows": 0,
            "retained_unsupported_rows": row_count,
            "row_reconciliation": "PASS",
            "run_status": "COMPLETED",
            "acquisition_audit": acquisition_audit,
            "families_without_configured_feed": [f"SPORT_{index}" for index in range(20)],
            "source_blockers": source_blockers,
            "discovery_independent_of_model_registry": True,
            "can_execute": False,
        },
        "market_evidence_counters": {"requests": 30, "failures": 0},
        "can_execute": False,
    }


def test_compact_response_bounds_all_slate_sized_run_level_collections():
    receipts = [_receipt(index) for index in range(2010)]
    missing_ids = [f"missing-{index}" for index in range(2010)]
    acquisition = _large_acquisition(receipts)
    response = {
        "run_id": "v17-daily-regression-2010",
        "run_status": "COMPLETED",
        "rows": [_blocked_row(index) for index in range(12)],
        "reconciliation": {
            "rows_in": 12,
            "rows_completed": 0,
            "rows_held": 12,
            "rows_rejected": 0,
            "balanced": True,
        },
        "lane_reconciliation": {
            "PROPS": {
                "discovered_count": 2010,
                "canonicalized_count": 2010,
                "requested_row_limit": 12,
                "scored_count": 12,
                "completed_count": 0,
                "held_count": 12,
                "rejected_count": 0,
                "zero_row_reason": None,
                "acquisition": acquisition,
                "handoff_reconciliation": {
                    "lane_discovered_raw": 2010,
                    "lane_snapshot_write_succeeded": 2010,
                    "lane_snapshot_write_failed": 0,
                    "explicit_prewrite_exclusions": 0,
                    "persisted_candidates": 2010,
                    "canonical_rows": 2010,
                    "canonical_rows_from_current_acquisition": 0,
                    "explicit_precanonical_exclusions": 0,
                    "missing_persisted_snapshot_ids": missing_ids,
                    "scored_rows": 12,
                    "explicitly_unscored_with_terminal_reason": 1998,
                    "prewrite_balanced": True,
                    "persisted_to_canonical_balanced": False,
                    "canonical_to_scored_balanced": True,
                    "can_execute": False,
                },
            }
        },
        # Daily also emits these at top level; COMPACT must not accidentally
        # leave a second copy of the same large acquisition packet inline.
        "prop_acquisition": acquisition,
        "cross_sport_discovery_audit": _cross_sport_audit(),
        "blockers": [f"blocker-{index}-" + "x" * 120 for index in range(100)],
        "can_execute": False,
    }

    # Reproduce the regression: run-level payloads alone can dwarf the client
    # budget even when only 12 prop rows are scored.
    assert serialized_byte_size(response) > CLIENT_RESPONSE_LIMIT_BYTES

    compact = compact_response(response, detail_available=True)
    assert serialized_byte_size(compact) < COMPACT_RESPONSE_BUDGET_BYTES

    lane = compact["lane_reconciliation"]["PROPS"]
    assert lane["canonicalized_count"] == 2010
    assert lane["scored_count"] == 12

    lane_acquisition = lane["acquisition"]
    assert lane_acquisition["receipts_count"] == 2010
    assert lane_acquisition["receipts_inlined"] is False
    assert "receipts" not in lane_acquisition

    top_acquisition = compact["prop_acquisition"]
    assert top_acquisition["receipts_count"] == 2010
    assert top_acquisition["receipts_inlined"] is False
    assert "receipts" not in top_acquisition

    handoff = lane["handoff_reconciliation"]
    assert handoff["missing_persisted_snapshot_ids_count"] == 2010
    assert len(handoff["missing_persisted_snapshot_ids"]) == 4
    assert handoff["missing_persisted_snapshot_ids_truncated"] == 2006
    assert handoff["canonical_to_scored_balanced"] is True

    cross = compact["cross_sport_discovery_audit"]
    assert cross["rows_count"] == 750
    assert cross["rows_inlined"] is False
    assert "rows" not in cross
    assert cross["reconciliation"]["row_reconciliation"] == "PASS"
    assert cross["reconciliation"]["events_accounted"] == 750
    assert cross["reconciliation"]["acquisition_audit_count"] == 30
    assert len(cross["reconciliation"]["acquisition_audit"]) == 4
    assert cross["discovery"]["events_discovered"] == 750
    assert cross["discovery"]["acquisition_audit_count"] == 30
    assert cross["discovery"]["source_blockers_count"] == 30

    # Transport compaction must not mask the producing capability state.
    direction = compact["rows"][0]["directions"][0]
    assert direction["code"] == "MODEL_UNAVAILABLE"
    assert direction["terminal_label"] == "MODEL_UNAVAILABLE"
    assert direction["probability_publishable"] is False
    assert direction["can_execute"] is False

    assert compact["reconciliation"]["balanced"] is True
    assert compact["can_execute"] is False

    # COMPACT projection is non-destructive; FULL/internal audit data remains.
    assert len(response["lane_reconciliation"]["PROPS"]["acquisition"]["receipts"]) == 2010
    assert len(response["prop_acquisition"]["receipts"]) == 2010
    assert len(response["cross_sport_discovery_audit"]["rows"]) == 750
    assert len(response["lane_reconciliation"]["PROPS"]["handoff_reconciliation"]["missing_persisted_snapshot_ids"]) == 2010
