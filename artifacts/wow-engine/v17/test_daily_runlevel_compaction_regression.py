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


def test_compact_response_bounds_two_thousand_candidate_run_level_receipts():
    receipts = [_receipt(index) for index in range(2010)]
    missing_ids = [f"missing-{index}" for index in range(2010)]
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
                "acquisition": {
                    "status": "COMPLETED",
                    "attempted": 2010,
                    "hydrated": 2010,
                    "persisted": 2010,
                    "held": 0,
                    "snapshot_write_succeeded": 2010,
                    "snapshot_write_failed": 0,
                    "explicit_prewrite_exclusions": 0,
                    "candidate_source": "TEST_SOURCE",
                    "line_source": "TEST_LINE_SOURCE",
                    "receipts": receipts,
                    "blockers": [],
                    "can_execute": False,
                },
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
        "blockers": [f"blocker-{index}-" + "x" * 120 for index in range(100)],
        "can_execute": False,
    }

    # This reproduces the regression: run-level acquisition/reconciliation data
    # alone can exceed the Action response budget even when only 12 rows score.
    assert serialized_byte_size(response) > CLIENT_RESPONSE_LIMIT_BYTES

    compact = compact_response(response, detail_available=True)
    assert serialized_byte_size(compact) < COMPACT_RESPONSE_BUDGET_BYTES

    lane = compact["lane_reconciliation"]["PROPS"]
    assert lane["canonicalized_count"] == 2010
    assert lane["scored_count"] == 12

    acquisition = lane["acquisition"]
    assert acquisition["receipts_count"] == 2010
    assert acquisition["receipts_inlined"] is False
    assert "receipts" not in acquisition

    handoff = lane["handoff_reconciliation"]
    assert handoff["missing_persisted_snapshot_ids_count"] == 2010
    assert len(handoff["missing_persisted_snapshot_ids"]) == 4
    assert handoff["missing_persisted_snapshot_ids_truncated"] == 2006
    assert handoff["canonical_to_scored_balanced"] is True

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
    assert len(response["lane_reconciliation"]["PROPS"]["handoff_reconciliation"]["missing_persisted_snapshot_ids"]) == 2010
