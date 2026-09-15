from v17.daily_response_contract import compact_moneyline_result, compact_row
from v17.team_event_probability_preservation import _annotate_probability_visibility


def _held_probability():
    return {
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "sporting_probability_completed": True,
        "sporting_probability_status": "COMPLETED_HELD_DOWNSTREAM",
        "probability_fields_withheld": False,
        "raw_home_probability": 0.61,
        "raw_away_probability": 0.39,
        "calibrated_home_probability": 0.59,
        "calibrated_home_lower_bound": 0.55,
        "calibrated_home_upper_bound": 0.63,
        "calibrated_away_probability": 0.41,
        "calibrated_away_lower_bound": 0.37,
        "calibrated_away_upper_bound": 0.45,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def test_valid_held_probability_is_visible_but_not_official():
    out = _annotate_probability_visibility(_held_probability())

    assert out["model_probability_available"] is True
    assert out["probability_visibility_status"] == "MODELED_HELD"
    assert out["official_leaderboard_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["rank_eligible"] is False
    assert out["can_execute"] is False


def test_compact_moneyline_keeps_held_numeric_probability_package():
    out = _annotate_probability_visibility(_held_probability())
    compact = compact_moneyline_result(out)

    assert compact["calibrated_home_probability"] == 0.59
    assert compact["calibrated_home_lower_bound"] == 0.55
    assert compact["calibrated_away_probability"] == 0.41
    assert compact["calibrated_away_lower_bound"] == 0.37
    assert compact["model_probability_available"] is True
    assert compact["probability_visibility_status"] == "MODELED_HELD"
    assert compact["official_leaderboard_eligible"] is False
    assert compact["probability_publishable"] is False
    assert compact["rank_eligible"] is False


def test_compact_row_surfaces_visibility_at_row_level():
    result = _annotate_probability_visibility(_held_probability())
    row = compact_row(
        {
            "lane": "MONEYLINE",
            "identity": {"official_event_id": "test-1"},
            "row_status": "HELD",
            "probability_publishable": False,
            "result": result,
        },
        run_id="test-run",
        row_index=0,
        detail_available=True,
    )

    assert row["row_status"] == "HELD"
    assert row["model_probability_available"] is True
    assert row["probability_visibility_status"] == "MODELED_HELD"
    assert row["official_leaderboard_eligible"] is False
    assert row["result_summary"]["calibrated_home_probability"] == 0.59


def test_model_failure_remains_blocked_unscored():
    out = _annotate_probability_visibility(
        {
            "code": "MODEL_SCORER_FAILED",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
    )

    assert out["model_probability_available"] is False
    assert out["probability_visibility_status"] == "BLOCKED_UNSCORED"
    assert out["official_leaderboard_eligible"] is False


def test_official_probability_remains_official_without_gate_weakening():
    out = _annotate_probability_visibility(
        {
            "code": "FINAL_APPROVED",
            "calibrated_probability": 0.67,
            "calibrated_lower_bound": 0.62,
            "probability_publishable": True,
            "rank_eligible": True,
            "can_execute": False,
        }
    )

    assert out["model_probability_available"] is True
    assert out["probability_visibility_status"] == "OFFICIAL_QUALIFIED"
    assert out["official_leaderboard_eligible"] is True
    assert out["probability_publishable"] is True
    assert out["rank_eligible"] is True
    assert out["can_execute"] is False
