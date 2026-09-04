from types import SimpleNamespace

from v17 import daily_snapshot_runtime
from v17 import team_event_probability_preservation
from v17.team_event_probability_preservation import _preserve_completed_probability_hold


def test_daily_uses_active_team_event_probability_preservation_wrapper() -> None:
    assert (
        daily_snapshot_runtime.score_team_event_request
        is team_event_probability_preservation.score_team_event_request
    )
    assert (
        daily_snapshot_runtime.TeamEventRequest
        is team_event_probability_preservation.TeamEventRequest
    )


def test_completed_sporting_probability_survives_downstream_governance_hold() -> None:
    req = SimpleNamespace(
        sport_specific_evidence={
            "home_lineup_status": "CONFIRMED",
            "away_lineup_status": "CONFIRMED",
        }
    )
    route = SimpleNamespace(
        requester_host_identity="WOW_BETTING_ENGINE",
        candidate_family="OUTRIGHT_WINNER",
    )
    model_result = {
        "code": "MLB_EVENT_MODEL_PROBABILITY_AVAILABLE",
        "raw_home_probability": 0.61,
        "raw_away_probability": 0.39,
        "calibrated_home_probability": 0.60,
        "calibrated_away_probability": 0.40,
        "calibrated_home_lower_bound": 0.56,
        "calibrated_home_upper_bound": 0.64,
        "calibrated_away_lower_bound": 0.36,
        "calibrated_away_upper_bound": 0.44,
        "probability_fields_withheld": False,
        "probability_publishable": True,
        "can_execute": False,
    }

    result = _preserve_completed_probability_hold(
        req,
        route,
        model_result,
        governance_detail={"status": "HOLD", "blockers": ["MARKET_CONTEXT_INCOMPLETE"]},
    )

    assert result["sporting_probability_completed"] is True
    assert result["sporting_probability_status"] == "COMPLETED_HELD_DOWNSTREAM"
    assert result["probability_fields_withheld"] is False
    assert result["calibrated_home_probability"] == 0.60
    assert result["calibrated_home_lower_bound"] == 0.56
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["can_execute"] is False
