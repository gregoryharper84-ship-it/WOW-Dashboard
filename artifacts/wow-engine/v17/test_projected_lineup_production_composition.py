from types import SimpleNamespace

from v17.team_event_probability_preservation import _preserve_completed_probability_hold
from v17_synthetic_self_acceptance import _projected_acceptance_ok


def _req(state="PROJECTED_HIGH_CONFIDENCE"):
    return SimpleNamespace(sport_specific_evidence={"home_lineup_status": state, "away_lineup_status": state})


def _route():
    return SimpleNamespace(requester_host_identity="WOW_BETTING_ENGINE", candidate_family="OUTRIGHT_WINNER")


def _model():
    return {
        "code": "MLB_EVENT_MODEL_PROBABILITY_AVAILABLE",
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


def _projected_payload():
    return _preserve_completed_probability_hold(
        _req(), _route(), _model(), governance_detail={"status": "HOLD", "blockers": ["LINEUP_NOT_CONFIRMED"]}
    )


def test_active_probability_preservation_wrapper_composes_projected_lineup_semantics():
    result = _projected_payload()
    assert result["code"] == "LINEUP_PROJECTED_PROBABILITY_AVAILABLE"
    assert result["sporting_probability_publishable"] is True
    assert result["probability_publishable"] is True
    assert result["rank_eligible"] is False
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["sporting_probability_status"] == "COMPLETED_HELD_LINEUP_CONFIRMATION"
    assert result["final_refresh_required"] is True
    assert result["can_execute"] is False


def test_confirmed_hold_uses_normal_downstream_probability_preservation():
    result = _preserve_completed_probability_hold(
        _req("CONFIRMED"), _route(), _model(), governance_detail={"status": "HOLD", "blockers": ["OTHER_GATE"]}
    )
    assert result["code"] == "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
    assert result["sporting_probability_completed"] is True
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False


def test_production_acceptance_allows_stronger_fail_closed_terminal_from_global_reducer():
    payload = _projected_payload()
    assert _projected_acceptance_ok(payload) is True

    payload["terminal_label"] = "SLATE_PURGE"
    payload["terminal_ceiling"] = "SLATE_PURGE"
    assert _projected_acceptance_ok(payload) is True


def test_production_acceptance_rejects_rank_or_final_approval_before_lineup_refresh():
    payload = _projected_payload()
    payload["rank_eligible"] = True
    assert _projected_acceptance_ok(payload) is False

    payload = _projected_payload()
    payload["terminal_label"] = "FINAL_APPROVED"
    assert _projected_acceptance_ok(payload) is False


def test_production_acceptance_requires_v17_terminal_reducer_authority():
    payload = _projected_payload()
    payload["global_terminal_authority"] = "OTHER_REDUCER"
    assert _projected_acceptance_ok(payload) is False
