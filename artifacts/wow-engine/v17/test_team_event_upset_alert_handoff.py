from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from v17.team_event_probability_preservation import _attach_upset_alert


def _req(*, home_market: float = 0.62, away_market: float = 0.38):
    return SimpleNamespace(
        sport="MLB",
        home_team="SEA",
        away_team="OAK",
        market_prior={
            "home_probability": home_market,
            "away_probability": away_market,
            "timestamp": "2026-09-06T20:00:00Z",
            "source": "TEST_EXACT_TWO_WAY_NO_VIG",
            "snapshot_id": "market-snapshot-1",
            "book_count": 4,
        },
    )


def _result():
    return {
        "sport": "MLB",
        "calibration_health_status": "PASS",
        "calibrated_home_probability": 0.46,
        "calibrated_home_lower_bound": 0.41,
        "calibrated_home_upper_bound": 0.51,
        "calibrated_away_probability": 0.54,
        "calibrated_away_lower_bound": 0.49,
        "calibrated_away_upper_bound": 0.59,
        "favorite_failure_path_probability_if_modeled": 0.54,
        "largest_favorite_loss_path": "starter collapse + bullpen handoff",
        "underdog_upset_path": "early run cluster",
        "probability_fields_withheld": False,
        "probability_publishable": True,
        "rank_eligible": True,
        "terminal_label": "FINAL_APPROVED",
        "terminal_ceiling": "FINAL_APPROVED",
        "can_execute": False,
    }


def test_handoff_flags_market_favorite_when_model_flips_to_underdog():
    result = _result()
    annotated = _attach_upset_alert(_req(), result)

    assert annotated["upset_alert_status"] == "UPSET_ALERT_MODEL_FLIP"
    assert annotated["upset_alert_severity"] == "HIGH"
    assert annotated["market_favorite"] == "SEA"
    assert annotated["upset_alert_candidate"] == "OAK"
    assert annotated["market_favorite_model_probability"] == 0.46
    assert annotated["upset_candidate_model_probability"] == 0.54


def test_handoff_is_annotation_only_for_probability_and_governance_fields():
    result = _result()
    before = deepcopy(result)
    annotated = _attach_upset_alert(_req(), result)

    for field in (
        "calibrated_home_probability",
        "calibrated_home_lower_bound",
        "calibrated_home_upper_bound",
        "calibrated_away_probability",
        "calibrated_away_lower_bound",
        "calibrated_away_upper_bound",
        "probability_publishable",
        "rank_eligible",
        "terminal_label",
        "terminal_ceiling",
        "can_execute",
    ):
        assert annotated[field] == before[field]
    assert result == before


def test_handoff_uses_market_only_for_favorite_identity_not_severity():
    first = _attach_upset_alert(_req(home_market=0.51, away_market=0.49), _result())
    second = _attach_upset_alert(_req(home_market=0.90, away_market=0.10), _result())

    assert first["market_favorite"] == second["market_favorite"] == "SEA"
    assert first["upset_alert_status"] == second["upset_alert_status"] == "UPSET_ALERT_MODEL_FLIP"
    assert first["upset_alert_severity"] == second["upset_alert_severity"] == "HIGH"
    assert first["upset_candidate_model_probability"] == second["upset_candidate_model_probability"] == 0.54


def test_handoff_marks_alert_unavailable_when_exact_market_identity_missing():
    req = _req()
    req.market_prior = None
    annotated = _attach_upset_alert(req, _result())

    assert annotated["upset_alert_status"] == "UPSET_ALERT_UNAVAILABLE"
    assert annotated["upset_alert_severity"] == "UNAVAILABLE"
    assert annotated["upset_alert_candidate"] is None
    assert annotated["probability_publishable"] is True
    assert annotated["rank_eligible"] is True


def test_handoff_marks_alert_unavailable_if_calibration_not_pass():
    result = _result()
    result["calibration_health_status"] = "HOLD"
    annotated = _attach_upset_alert(_req(), result)

    assert annotated["upset_alert_status"] == "UPSET_ALERT_UNAVAILABLE"
    assert annotated["upset_alert"]["reason_codes"] == ["GOVERNED_CALIBRATION_NOT_PASS"]
    assert annotated["terminal_label"] == "FINAL_APPROVED"


def test_market_tie_does_not_invent_a_favorite():
    annotated = _attach_upset_alert(_req(home_market=0.50, away_market=0.50), _result())
    assert annotated["upset_alert_status"] == "UPSET_ALERT_UNAVAILABLE"
    assert annotated["upset_alert"]["reason_codes"] == ["MARKET_FAVORITE_TIE_UNRESOLVED"]
