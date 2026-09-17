from v17.exact_board_reconciliation import EXACT_BOARD_IDENTITY_MISMATCH
from v17.top10_model_reconciliation import enforce_top10_completion


def _source(line=6.5):
    return [{
        "row_key": "wheeler",
        "event_id": "MLB:PHI:WSH:2026-09-17",
        "sport": "MLB",
        "player": "Zack Wheeler",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": line,
        "direction": "MORE",
    }]


def _response(line=6.5):
    return {
        "ok": True,
        "run_controller_status": "COMPLETE",
        "reconciliation_pass": True,
        "rows": [{
            "row_key": "wheeler",
            "terminal_status": "COMPLETED",
            "model_evaluated": True,
            "result": {
                "prediction": {
                    "event_id": "MLB:PHI:WSH:2026-09-17",
                    "player": "Zack Wheeler",
                    "sport": "MLB",
                    "stat_type": "PITCHER_STRIKEOUTS",
                    "line": line,
                    "direction": "MORE",
                    "calibrated_probability": 0.62,
                    "calibrated_probability_lower_bound": 0.55,
                }
            },
            "can_execute": False,
        }],
        "can_execute": False,
    }


def test_wrong_line_cannot_complete_even_when_probability_package_is_valid():
    out = enforce_top10_completion(_response(line=4.5), _source(line=6.5))
    assert out["ok"] is False
    assert out["run_controller_status"] == "BLOCKED"
    assert out["reconciliation_pass"] is False
    assert out["completion_blocker"] == EXACT_BOARD_IDENTITY_MISMATCH
    audit = out["exact_board_identity_reconciliation"]
    assert audit["balanced"] is False
    assert audit["mismatch_count"] == 1
    assert audit["mismatches"][0]["fields"] == ["line"]
    # The guard blocks publication/completion; it never rewrites sporting probability.
    assert out["rows"][0]["result"]["prediction"]["calibrated_probability"] == 0.62
    assert out["can_execute"] is False


def test_matching_exact_identity_remains_completion_eligible_and_rows_unchanged():
    original = _response(line=6.5)
    original_rows = original["rows"]
    out = enforce_top10_completion(original, _source(line=6.5))
    assert out["ok"] is True
    assert out["reconciliation_pass"] is True
    assert out["completion_blocker"] is None if "completion_blocker" in out else True
    audit = out["exact_board_identity_reconciliation"]
    assert audit["balanced"] is True
    assert audit["mismatch_count"] == 0
    assert audit["request_identities"]["wheeler"]["line"] == 6.5
    assert audit["request_identities"]["wheeler"]["direction"] == "MORE"
    assert out["rows"] == original_rows
    assert out["top10_model_reconciliation"]["balanced"] is True
    assert out["can_execute"] is False


def test_typed_blocker_keeps_row_shape_and_records_exact_request_identity_in_audit():
    response = {
        "rows": [{
            "row_key": "wheeler",
            "terminal_status": "HELD",
            "code": "MODEL_INPUTS_INSUFFICIENT",
            "model_evaluated": False,
            "can_execute": False,
        }],
        "can_execute": False,
    }
    original_rows = list(response["rows"])
    out = enforce_top10_completion(response, _source(line=6.5))
    assert out["top10_model_reconciliation"]["balanced"] is True
    assert out["rows"] == original_rows
    assert out["exact_board_identity_reconciliation"]["request_identities"]["wheeler"] == {
        "event_id": "MLB:PHI:WSH:2026-09-17",
        "player": "Zack Wheeler",
        "sport": "MLB",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 6.5,
        "direction": "MORE",
        "can_execute": False,
    }
