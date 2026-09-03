from types import SimpleNamespace

from v17.projected_lineup_scenario_modeling import (
    classify_event_lineup,
    projected_probability_hold,
    validate_scenario_mixture,
)


def _req(home="PROJECTED_HIGH_CONFIDENCE", away="PROJECTED_HIGH_CONFIDENCE"):
    return SimpleNamespace(sport_specific_evidence={"home_lineup_status": home, "away_lineup_status": away})


def _model():
    return {
        "raw_home_probability": 0.70,
        "raw_away_probability": 0.30,
        "calibrated_home_probability": 0.68,
        "calibrated_away_probability": 0.32,
        "calibrated_home_lower_bound": 0.63,
        "calibrated_home_upper_bound": 0.73,
        "calibrated_away_lower_bound": 0.27,
        "calibrated_away_upper_bound": 0.37,
        "probability_fields_withheld": False,
        "probability_publishable": True,
        "can_execute": False,
    }


def test_projected_lineup_preserves_valid_governed_probability_but_not_rank():
    result = projected_probability_hold(_req(), _model(), {"status": "HOLD", "blockers": ["LINEUP_NOT_CONFIRMED"]})
    assert result is not None
    assert result["calibrated_home_probability"] == 0.68
    assert result["calibrated_home_lower_bound"] == 0.63
    assert result["sporting_probability_publishable"] is True
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is False
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["final_refresh_required"] is True
    assert result["can_execute"] is False


def test_confirmed_lineup_does_not_use_projected_hold_adapter():
    assert projected_probability_hold(_req("CONFIRMED", "CONFIRMED"), _model(), {"status": "PASS"}) is None


def test_material_conflict_does_not_publish_projected_probability():
    assert projected_probability_hold(_req("MATERIAL_CONFLICT", "PROJECTED_HIGH_CONFIDENCE"), _model(), {"status": "HOLD"}) is None


def test_missing_numeric_package_still_fails_closed():
    payload = _model()
    payload.pop("calibrated_home_lower_bound")
    assert projected_probability_hold(_req(), payload, {"status": "HOLD"}) is None


def test_lineup_state_precedence():
    assert classify_event_lineup("CONFIRMED", "CONFIRMED") == "CONFIRMED"
    assert classify_event_lineup("PROJECTED_HIGH_CONFIDENCE", "CONFIRMED") == "PROJECTED_HIGH_CONFIDENCE"
    assert classify_event_lineup("PROJECTED_MEDIUM_CONFIDENCE", "CONFIRMED") == "PROJECTED_MEDIUM_CONFIDENCE"
    assert classify_event_lineup("MATERIAL_CONFLICT", "CONFIRMED") == "MATERIAL_CONFLICT"
    assert classify_event_lineup("DATA_UNOBTAINABLE", "CONFIRMED") == "DATA_UNOBTAINABLE"


def test_scenario_weights_are_validated_never_invented():
    assert validate_scenario_mixture(None)["status"] == "NOT_EXPOSED_BY_CONTROLLING_MODEL"
    valid = validate_scenario_mixture([
        {"scenario": "expected", "weight": 0.7},
        {"scenario": "one_material_change", "weight": 0.2},
        {"scenario": "alternate", "weight": 0.1},
    ])
    assert valid["status"] == "PASS"
    assert valid["scenario_n"] == 3
    invalid = validate_scenario_mixture([
        {"scenario": "expected", "weight": 0.8},
        {"scenario": "alternate", "weight": 0.3},
    ])
    assert invalid["status"] == "MODEL_SCENARIO_MIXTURE_INVALID"
