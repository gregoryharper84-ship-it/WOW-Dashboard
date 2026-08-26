import json
from pathlib import Path

from gate_engine.full_board_confidence import (
    FULL_BOARD_CONFIDENCE_PASS,
    audit_full_board_confidence,
)


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "task305_structural_61_prizepicks_2026_08_08.json"
)


def _observed_terminal_row(compact_row):
    pool_id, player, stat_key, line, team, opponent, venue, offer_type = compact_row
    handoff_code = (
        "1IP_EVENT_TREE_INPUT_INCOMPLETE"
        if stat_key == "1IP_PITCHES_THROWN"
        else "MODEL_GAME_LOG_INCOMPLETE"
    )
    # The pre-fix structural run established that all 61 pipeline rows reached
    # an explicit terminal incomplete handoff.  Two alternate-threshold rows
    # were separately rejected by structure logic; that terminal constraint
    # must not change the confidence-accounting semantics.
    terminal_label = (
        "REJECT_ALTERNATE_THRESHOLD_DUPLICATE"
        if pool_id in {"PP-0330", "PP-0331"}
        else "DATA_CONTRACT_FAIL"
    )
    return {
        "canonical_selection_id": pool_id,
        "player": player,
        "stat_key": stat_key,
        "line": line,
        "team": team,
        "opponent": opponent,
        "venue": venue,
        "offer_type": offer_type,
        "terminal_label": terminal_label,
        "model_probability_handoff": {
            "status": "INCOMPLETE",
            "code": handoff_code,
        },
        "candidate_evaluation_completed": False,
        "raw_model_probability": None,
        "calibration_status": "UNAVAILABLE",
        "probability_publishable": False,
        "can_execute": False,
    }


def test_task305_real_board_structural_confidence_reconciliation():
    fixture = json.loads(FIXTURE.read_text())
    assert fixture["fixture_role"] == "STRUCTURAL_ACCEPTANCE_NOT_ORIGINAL_2026_08_25_INCIDENT_REPLAY"
    assert fixture["expected_shape"] == {
        "rows_in": 61,
        "pitcher_strikeouts_rows": 32,
        "first_inning_pitches_rows": 29,
    }

    rows = [_observed_terminal_row(row) for row in fixture["rows"]]
    assert len(rows) == 61
    assert sum(row["stat_key"] == "PITCHER_STRIKEOUTS" for row in rows) == 32
    assert sum(row["stat_key"] == "1IP_PITCHES_THROWN" for row in rows) == 29

    result = audit_full_board_confidence(
        rows,
        discovered_count=61,
        reconciliation_passed=True,
    )

    assert result["status"] == FULL_BOARD_CONFIDENCE_PASS
    assert result["terminal_rows_seen"] == 61
    assert result["model_eligible_rows"] == 61
    assert result["confidence_accounted_rows"] == 61
    assert result["confidence_categories"]["NO_CONFIDENCE"] == 61
    assert result["unaccounted_ids"] == []
    assert result["publishable_modeled_confidence_rows"] == 0
    assert result["optimizer_allowed"] is False
    assert result["promising_count_claim_allowed"] is False
    assert result["can_execute"] is False
