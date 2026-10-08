from __future__ import annotations

from copy import deepcopy
from math import log

import pytest

from v17.nfl_ml_challenger_forward_grader import (
    CAN_EXECUTE,
    ChallengerGradeError,
    grade_settled_challenger,
)


def _shadow():
    return {
        "shadow_id": "test-shadow-2026-atl-no",
        "challenger_id": "NFL_ML_STATIONARY_DECAY_PLATT_COMPOSITE_LB_V1",
        "official_event_id": "2026_04_ATL_NO",
        "prediction_created_at": "2026-10-05T03:17:56Z",
        "event_start_time_utc": "2026-10-06T00:15:00Z",
        "home_team": "New Orleans Saints",
        "away_team": "Atlanta Falcons",
        "selected_participant": "Atlanta Falcons",
        "calibrated_probability": 0.581583343499892,
        "local_wilson_lower": 0.3678191476,
        "bootstrap_q10_lower": 0.406812797,
        "composite_lower_bound": 0.3678191476,
        "source_feature_hash": "a"*64,
        "lifecycle_state": "FORWARD_SHADOW",
        "outcome": None, "graded_at": None, "hit": None, "brier": None, "log_loss": None,
        "can_execute": False, "promotion_authorized": False, "probability_publishable": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def _outcome():
    return {
        "game_id": "2026_04_ATL_NO",
        "season": 2026,
        "week": 4,
        "away_team": "ATL",
        "home_team": "NO",
        "away_score": 45,
        "home_score": 24,
        "home_win": False,
        "tie": False,
        "locked_at": "2026-10-06T18:32:11Z",
        "schedule_snapshot_id": "test-source-snapshot",
        "row_inputs_hash": "b"*64,
        "can_execute": False,
    }


def test_immutable_atl_no_forward_challenger_can_be_prepared_for_grade():
    shadow, outcome = _shadow(), _outcome()
    original = deepcopy(shadow)
    receipt = grade_settled_challenger(shadow, outcome, graded_at="2026-10-08T00:00:00Z")
    assert receipt["status"] == "RESEARCH_GRADE_PREPARED_NOT_PERSISTED"
    assert receipt["official_event_id"] == "2026_04_ATL_NO"
    assert receipt["settlement_game_id"] == outcome["game_id"]
    assert receipt["prediction_fields_unchanged"] is True
    assert shadow == original
    assert receipt["can_execute"] is False
    assert receipt["probability_publishable"] is False
    assert receipt["promotion_authorized"] is False
    assert CAN_EXECUTE is False
    grade = receipt["update_fields"]
    assert grade["lifecycle_state"] == "GRADED"
    assert grade["outcome"] == 1
    assert grade["hit"] is True
    assert grade["brier"] == pytest.approx((1-0.581583343499892)**2)
    assert grade["log_loss"] == pytest.approx(-log(0.581583343499892))
    assert "calibrated_probability" not in grade
    assert "selected_participant" not in grade
    assert "promotion_authorized" not in grade


def test_correctly_grades_a_selected_home_side_loss():
    s=_shadow()
    s["selected_participant"]="New Orleans Saints"
    o=_outcome()
    grade=grade_settled_challenger(s,o,graded_at="2026-10-08T00:00:00Z")
    assert grade["update_fields"]["outcome"] == 0
    assert grade["update_fields"]["hit"] is False
    assert grade["update_fields"]["brier"] == pytest.approx(.581583343499892**2)


@pytest.mark.parametrize(("field","where","new","code"), [
    ("lifecycle_state","s","GRADED","NFL_CHALLENGER_GRADE_SOURCE_STATE_INVALID"),
    ("outcome","s",1,"NFL_CHALLENGER_GRADE_SOURCE_ALREADY_MODIFIED"),
    ("promotion_authorized","s",True,"NFL_CHALLENGER_GRADE_GOVERNANCE_MISMATCH"),
    ("probability_publishable","s",True,"NFL_CHALLENGER_GRADE_GOVERNANCE_MISMATCH"),
    ("can_execute","s",True,"NFL_CHALLENGER_GRADE_GOVERNANCE_MISMATCH"),
    ("terminal_authority","s","UNKNOWN","NFL_CHALLENGER_GRADE_GOVERNANCE_MISMATCH"),
    ("challenger_id","s","wrong-model","NFL_CHALLENGER_GRADE_MODEL_IDENTITY_MISMATCH"),
    ("official_event_id","s","2026_04_ATL_X","NFL_CHALLENGER_GRADE_EVENT_ID_FORMAT_INVALID"),
    ("prediction_created_at","s","2026-10-06T00:15:00Z","NFL_CHALLENGER_GRADE_PREGAME_LEAKAGE"),
    ("calibrated_probability","s",1.4,"NFL_CHALLENGER_GRADE_PROBABILITY_INVALID"),
    ("composite_lower_bound","s",.99,"NFL_CHALLENGER_GRADE_LOWER_BOUND_INVALID"),
    ("game_id","o","2026_04_ATL_TB","NFL_CHALLENGER_GRADE_SETTLEMENT_EVENT_MISMATCH"),
    ("home_team","o","ATL","NFL_CHALLENGER_GRADE_SETTLEMENT_PARTICIPANTS_MISMATCH"),
    ("home_win","o",True,"NFL_CHALLENGER_GRADE_SCORE_WINNER_CONFLICT"),
    ("home_score","o",46,"NFL_CHALLENGER_GRADE_SCORE_WINNER_CONFLICT"),
    ("tie","o",True,"NFL_CHALLENGER_GRADE_NONBINARY_TIE"),
    ("locked_at","o","2026-10-05T00:00:00Z","NFL_CHALLENGER_GRADE_PREMATURE_SETTLEMENT"),
    ("schedule_snapshot_id","o",None,"NFL_CHALLENGER_GRADE_SETTLEMENT_SNAPSHOT_REQUIRED"),
    ("row_inputs_hash","o",None,"NFL_CHALLENGER_GRADE_SOURCE_HASH_REQUIRED"),
    ("can_execute","o",True,"NFL_CHALLENGER_GRADE_SETTLEMENT_EXECUTION_INVALID"),
])
def test_bad_identity_leakage_settlement_and_governance_fail_closed(field,where,new,code):
    s,o=_shadow(),_outcome()
    target=s if where=="s" else o
    target[field]=new
    with pytest.raises(ChallengerGradeError,match=code):
        grade_settled_challenger(s,o,graded_at="2026-10-08T00:00:00Z")


def test_refuses_grade_timestamp_before_final_source_capture():
    with pytest.raises(ChallengerGradeError,match="NFL_CHALLENGER_GRADE_PREMATURE_SETTLEMENT"):
        grade_settled_challenger(_shadow(),_outcome(),graded_at="2026-10-05T00:00:00Z")
