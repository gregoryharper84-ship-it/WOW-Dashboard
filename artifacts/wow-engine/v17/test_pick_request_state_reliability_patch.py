from pick_request_runtime_core import PickRequestRow
from v17.pick_request_state_reliability_patch import (
    IDENTITY_CONFLICT_TERMINAL,
    _durable_status_without_false_publication,
    _isolate_exact_identity_conflicts,
    _manifest_counts,
    _receipt_stage_allowed,
)
from v17.top10_model_reconciliation import enforce_top10_completion


def test_manifest_counts_preserve_ingested_pending_rows_before_finalize():
    rows = (
        [{"terminal_status": "COMPLETED", "model_evaluated": True, "stage_seq": 5}] * 6
        + [{"terminal_status": "HELD", "model_evaluated": False, "stage_seq": 0}] * 4
        + [{"terminal_status": "REJECTED", "model_evaluated": False, "stage_seq": 0}] * 6
        + [{"terminal_status": "PENDING", "model_evaluated": False, "stage_seq": 0}] * 2
    )

    counts = _manifest_counts(rows)

    assert counts == {
        "total_rows": 18,
        "completed_rows": 6,
        "held_rows": 4,
        "rejected_rows": 6,
        "pending_rows": 2,
        "unresolved_rows": 2,
    }


def test_governance_audit_cannot_imply_receipt_without_prediction_id():
    record = {
        "model_evaluated": True,
        "stage_seq": 3,
        "prediction_id": None,
    }

    assert _receipt_stage_allowed(record, "MODEL_COMPUTED") is True
    assert _receipt_stage_allowed(record, "RECEIPT_PERSISTED") is False
    assert _receipt_stage_allowed(record, "GOVERNANCE_AUDITED") is False
    assert _receipt_stage_allowed(record, "PUBLICATION_AUTHORIZED") is False


def test_receipt_backed_row_can_advance_to_governance_audit():
    record = {
        "model_evaluated": True,
        "stage_seq": 3,
        "prediction_id": "11111111-1111-1111-1111-111111111111",
    }

    assert _receipt_stage_allowed(record, "RECEIPT_PERSISTED") is True
    assert _receipt_stage_allowed(record, "GOVERNANCE_AUDITED") is True


def _source_row(row_key: str, event_id: str, player: str) -> PickRequestRow:
    return PickRequestRow(
        row_key=row_key,
        event_id=event_id,
        event_start_time="2026-10-02T00:15:00+00:00",
        sport="NFL",
        player=player,
        stat_type="RUSHING_YARDS",
        line=31.5,
        direction="MORE",
        source_type="PASTED_BOARD",
        platform="PRIZEPICKS",
    )


def _scored(row_key: str, event_id: str, player: str, prediction_id: str) -> dict:
    return {
        "row_key": row_key,
        "terminal_status": "REJECTED",
        "code": "NO_LOW_PROBABILITY",
        "model_evaluated": True,
        "probability_publishable": True,
        "rank_eligible": False,
        "result": {
            "prediction": {
                "prediction_id": prediction_id,
                "event_id": event_id,
                "player": player,
                "sport": "NFL",
                "stat_type": "RUSHING_YARDS",
                "line": 31.5,
                "direction": "MORE",
                "calibrated_probability": 0.49,
                "calibrated_probability_lower_bound": 0.45,
            }
        },
        "can_execute": False,
    }


def test_post_score_event_alias_mismatch_becomes_row_isolated_typed_hold():
    source_rows = [
        _source_row("SR14_MORE", "PIT-CLE-2026-10-01", "Deshaun Watson"),
        _source_row("SR19_MORE", "PHI-ATL-2026-10-01", "Aaron Nola"),
    ]
    outcomes = [
        _scored("SR14_MORE", "2026_04_PIT_CLE", "Deshaun Watson", "pred-nfl"),
        _scored("SR19_MORE", "PHI-ATL-2026-10-01", "Aaron Nola", "pred-mlb"),
    ]

    isolated, mismatches = _isolate_exact_identity_conflicts(source_rows, outcomes)

    assert [item["row_key"] for item in mismatches] == ["SR14_MORE"]
    conflict = isolated[0]
    assert conflict["terminal_status"] == "HELD"
    assert conflict["code"] == IDENTITY_CONFLICT_TERMINAL
    assert conflict["model_evaluated"] is False
    assert conflict["probability_publishable"] is False
    assert conflict["rank_eligible"] is False
    assert "prediction_id" not in conflict
    assert "result" not in conflict
    assert conflict["detail"]["blocker_code"] == "EXACT_BOARD_IDENTITY_MISMATCH"
    assert conflict["detail"]["conflicting_fields"] == ["event_id"]
    assert conflict["detail"]["original_terminal_code"] == "NO_LOW_PROBABILITY"
    assert conflict["detail"]["original_prediction_id"] == "pred-nfl"
    assert conflict["detail"]["scorer_receipt_preserved_for_audit"] is True
    assert isolated[1] == outcomes[1]
    assert all(item["can_execute"] is False for item in isolated)

    reconciled = enforce_top10_completion(
        {
            "rows": isolated,
            "reconciliation_pass": True,
            "run_controller_status": "DEGRADED",
            "can_execute": False,
        },
        source_rows,
    )
    assert reconciled["reconciliation_pass"] is True
    assert reconciled["exact_board_identity_reconciliation"]["balanced"] is True
    assert reconciled["exact_board_identity_reconciliation"]["mismatch_count"] == 0


def test_55_direction_manifest_isolates_one_identity_conflict_without_stranding_other_rows():
    source_rows = [
        _source_row(f"SR{i}_MORE", f"EVENT-{i}", f"Player {i}")
        for i in range(1, 56)
    ]
    outcomes = [
        {
            "row_key": f"SR{i}_MORE",
            "terminal_status": "HELD",
            "code": "NFL_RECENT_GAMES_INSUFFICIENT",
            "model_evaluated": False,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
        for i in range(1, 56)
    ]
    outcomes[13] = _scored(
        "SR14_MORE", "CANONICAL-EVENT-14", "Player 14", "pred-14"
    )

    isolated, mismatches = _isolate_exact_identity_conflicts(source_rows, outcomes)
    reconciled = enforce_top10_completion(
        {
            "rows": isolated,
            "reconciliation_pass": True,
            "run_controller_status": "BLOCKED",
            "can_execute": False,
        },
        source_rows,
    )

    assert len(isolated) == 55
    assert len(mismatches) == 1
    assert isolated[13]["code"] == IDENTITY_CONFLICT_TERMINAL
    assert "prediction_id" not in isolated[13]
    assert isolated[13]["detail"]["original_prediction_id"] == "pred-14"
    assert sum(item["terminal_status"] == "HELD" for item in isolated) == 55
    assert reconciled["reconciliation_pass"] is True
    assert reconciled["exact_board_identity_reconciliation"]["balanced"] is True
    assert reconciled["exact_board_identity_reconciliation"]["mismatch_count"] == 0


def test_held_identity_conflict_cannot_report_governed_publication_authorized():
    record = {
        "stage_seq": 6,
        "terminal_status": "HELD",
        "terminal_code": IDENTITY_CONFLICT_TERMINAL,
        "model_evaluated": False,
        "probability_publishable": False,
    }

    assert (
        _durable_status_without_false_publication(record)
        == "HELD:PROP_EVENT_IDENTITY_CONFLICT"
    )
