from pathlib import Path

from v17.host_routing import (
    FullModelActionReceipt,
    LIVE_GPT_ACTION_INVOCATION_BLOCKED,
    LIVE_GPT_ACTION_RESULT_INVALID,
    expected_full_model_operation_id,
    validate_full_model_action_receipt,
)


def test_full_model_prop_without_action_is_invocation_blocked():
    out = validate_full_model_action_receipt(
        FullModelActionReceipt(candidate_family="PLAYER_PROP", action_invoked=False)
    )
    assert out["status"] == LIVE_GPT_ACTION_INVOCATION_BLOCKED
    assert out["scoring_attempted"] is False
    assert out["backend_model_capability"] == "UNKNOWN"
    assert out["rank_eligible"] is False
    assert out["full_model_completed"] is False
    assert out["expected_operation_id"] == "scoreWowV17PickRequest"
    assert out["can_execute"] is False


def test_full_model_moneyline_without_action_is_invocation_blocked():
    out = validate_full_model_action_receipt(
        FullModelActionReceipt(candidate_family="MONEYLINE", action_invoked=False)
    )
    assert out["status"] == LIVE_GPT_ACTION_INVOCATION_BLOCKED
    assert out["scoring_attempted"] is False
    assert out["expected_operation_id"] == "scoreWowV17TeamEventFromWowHost"
    assert out["can_execute"] is False


def test_action_attempt_preserves_backend_model_unavailable():
    out = validate_full_model_action_receipt(
        FullModelActionReceipt(
            candidate_family="MONEYLINE",
            action_invoked=True,
            operation_id="scoreWowV17TeamEventFromWowHost",
            backend_terminal_status="MODEL_UNAVAILABLE",
            backend_model_capability="UNAVAILABLE",
            http_result=200,
        )
    )
    assert out["status"] == "MODEL_UNAVAILABLE"
    assert out["scoring_attempted"] is True
    assert out["backend_model_capability"] == "UNAVAILABLE"
    assert out["full_model_completed"] is True
    assert out["can_execute"] is False


def test_action_attempt_preserves_scorer_failure_not_model_unavailable():
    out = validate_full_model_action_receipt(
        FullModelActionReceipt(
            candidate_family="PLAYER_PROP",
            action_invoked=True,
            operation_id="scoreWowV17PickRequest",
            backend_terminal_status="MODEL_SCORER_FAILED",
            backend_model_capability="AVAILABLE",
            http_result=500,
            exact_error="SCORER_EXCEPTION",
        )
    )
    assert out["status"] == "MODEL_SCORER_FAILED"
    assert out["scoring_attempted"] is True
    assert out["backend_model_capability"] == "AVAILABLE"
    assert out["status"] != "MODEL_UNAVAILABLE"


def test_wrong_operation_cannot_complete_full_model():
    out = validate_full_model_action_receipt(
        FullModelActionReceipt(
            candidate_family="MONEYLINE",
            action_invoked=True,
            operation_id="scoreWowV17PickRequest",
            backend_terminal_status="MODEL_QUALIFIED",
        )
    )
    assert out["status"] == LIVE_GPT_ACTION_RESULT_INVALID
    assert out["scoring_attempted"] is True
    assert out["rank_eligible"] is False
    assert out["full_model_completed"] is False


def test_canonical_operation_mapping_is_lane_specific():
    assert expected_full_model_operation_id("PITCHER_PROP") == "scoreWowV17PickRequest"
    assert expected_full_model_operation_id("ML") == "scoreWowV17TeamEventFromWowHost"


def test_pick_request_keeps_exact_line_in_snapshot_and_score_request():
    """Protect the Gray 3.5 / Bradley 5.5 class of exact-line regressions.

    Adjacent market evidence may be present, but the canonical scoring request
    must use the supplied row.line and the frozen snapshot must fingerprint and
    persist that exact same threshold.
    """
    source = (Path(__file__).parents[1] / "pick_request_runtime.py").read_text()
    assert '"line": float(row.line)' in source
    assert '"line": row.line' in source
    assert 'request_payload["line"]' not in source  # no later line override


def test_adjacent_market_lines_are_not_prop_model_authority():
    source = (Path(__file__).parents[1] / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt").read_text()
    assert "Keep EXACT_LINE, ADJACENT_LINE, and NO_MARKET distinct" in source
    assert "Adjacent sportsbook lines are context only" in source
