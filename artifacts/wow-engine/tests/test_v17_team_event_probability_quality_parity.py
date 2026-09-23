from __future__ import annotations

import v17.team_event_bridge_runtime as bridge_runtime
import v17.team_event_probability_quality_parity as parity
from v17.team_event_probability_quality_parity import (
    REQUIRED_CALIBRATION_METRICS,
    REQUIRED_DIMENSIONS,
    TARGET_SPORTS,
    assess_probability_quality_evidence,
    normalize_quality_sport,
)


def _complete_evidence():
    return {
        "exact_fitted_specialist": True,
        "server_owned_calibration_artifact": True,
        "calibration_metrics": {
            "brier": 0.20,
            "log_loss": 0.58,
            "ece": 0.04,
            "calibration_intercept": 0.01,
            "calibration_slope": 1.02,
            "max_calibration_bin_gap": 0.08,
        },
        "calibration_health_status": "PASS",
        "calibration_health_quantitative": True,
        "test_used_for_selection": False,
        "cohort_reliability_separate": True,
        "event_specific_uncertainty": True,
        "early_pregame_grade": True,
        "final_pregame_published_grade": True,
        "dominance_or_margin_diagnostic": True,
        "chronological_challenger": True,
        "untouched_holdout": True,
        "typed_failure_preservation": True,
        "can_execute": False,
    }


def test_target_sports_get_identical_quality_contract():
    assert TARGET_SPORTS == ("MLB", "NFL", "NCAAF", "NBA", "WNBA", "NHL")
    contracts = []
    for sport in TARGET_SPORTS:
        result = assess_probability_quality_evidence(sport, _complete_evidence())
        assert result["status"] == "QUALITY_PARITY_EVIDENCE_COMPLETE"
        assert result["can_execute"] is False
        assert result["automatic_promotion"] is False
        assert result["probability_publishable"] is False
        contracts.append(
            (tuple(result["required_dimensions"]), tuple(result["required_calibration_metrics"]))
        )
    assert len(set(contracts)) == 1
    assert contracts[0][0] == REQUIRED_DIMENSIONS
    assert contracts[0][1] == REQUIRED_CALIBRATION_METRICS


def test_cfb_alias_is_ncaaf_not_a_separate_probability_lane():
    assert normalize_quality_sport("CFB") == "NCAAF"
    assert normalize_quality_sport("college_football") == "NCAAF"


def test_pass_label_without_quantitative_metrics_fails_closed():
    evidence = _complete_evidence()
    evidence["calibration_metrics"] = None
    evidence["calibration_health_quantitative"] = False
    result = assess_probability_quality_evidence("WNBA", evidence)
    assert result["status"] == "QUALITY_EVIDENCE_INCOMPLETE"
    assert "QUALITY_PARITY_CALIBRATION_METRICS_MISSING" in result["blockers"]
    assert "QUALITY_PARITY_CALIBRATION_PASS_NOT_QUANTITATIVE" in result["blockers"]


def test_request_supplied_calibration_cannot_substitute_for_server_owned_authority():
    evidence = _complete_evidence()
    evidence["server_owned_calibration_artifact"] = False
    result = assess_probability_quality_evidence("WNBA", evidence)
    assert result["status"] == "QUALITY_EVIDENCE_INCOMPLETE"
    assert "QUALITY_PARITY_SERVER_OWNED_CALIBRATION_UNPROVEN" in result["blockers"]


def test_missing_exact_sport_model_is_not_hidden_by_other_evidence():
    evidence = _complete_evidence()
    evidence["exact_fitted_specialist"] = False
    result = assess_probability_quality_evidence("NBA", evidence)
    assert result["status"] == "MODEL_CAPABILITY_UNPROVEN"
    assert "QUALITY_PARITY_EXACT_FITTED_SPECIALIST_UNPROVEN" in result["blockers"]


def test_cohort_reliability_cannot_substitute_for_event_uncertainty():
    evidence = _complete_evidence()
    evidence["event_specific_uncertainty"] = False
    result = assess_probability_quality_evidence("NHL", evidence)
    assert result["status"] == "QUALITY_EVIDENCE_INCOMPLETE"
    assert "QUALITY_PARITY_EVENT_UNCERTAINTY_UNPROVEN" in result["blockers"]


def test_early_grade_cannot_substitute_for_final_pregame_grade():
    evidence = _complete_evidence()
    evidence["final_pregame_published_grade"] = False
    result = assess_probability_quality_evidence("NFL", evidence)
    assert result["status"] == "QUALITY_EVIDENCE_INCOMPLETE"
    assert "QUALITY_PARITY_FINAL_PREGAME_GRADE_LEDGER_MISSING" in result["blockers"]


def test_test_set_selection_leakage_is_blocking():
    evidence = _complete_evidence()
    evidence["test_used_for_selection"] = True
    result = assess_probability_quality_evidence("MLB", evidence)
    assert result["status"] == "QUALITY_EVIDENCE_INCOMPLETE"
    assert "QUALITY_PARITY_TEST_SELECTION_LEAKAGE" in result["blockers"]


def test_can_execute_must_remain_false():
    evidence = _complete_evidence()
    evidence["can_execute"] = True
    result = assess_probability_quality_evidence("NCAAF", evidence)
    assert "QUALITY_PARITY_CAN_EXECUTE_MUST_BE_FALSE" in result["blockers"]


def test_health_overlay_does_not_confuse_route_readiness_with_quality_parity(monkeypatch):
    base_health = {
        "WNBA": {
            "model_artifact_dependency_satisfied": True,
            "request_scoring_path_ready": True,
            "certification_status": "CERTIFIED",
            "can_execute": False,
        },
        "NBA": {
            "model_artifact_dependency_satisfied": False,
            "request_scoring_path_ready": False,
            "certification_status": "UNAVAILABLE",
            "can_execute": False,
        },
    }
    monkeypatch.setattr(bridge_runtime, "team_event_bridge_health", lambda: base_health)
    monkeypatch.setattr(
        bridge_runtime,
        "_v17_probability_quality_parity_installed",
        False,
        raising=False,
    )

    receipt = parity.install_probability_quality_parity_overlay()
    health = bridge_runtime.team_event_bridge_health()

    assert receipt["status"] == "INSTALLED"
    assert receipt["can_execute"] is False
    assert health["WNBA"]["probability_quality_parity_status"] == "QUALITY_EVIDENCE_INCOMPLETE"
    assert "QUALITY_PARITY_CALIBRATION_METRICS_MISSING" in health["WNBA"]["probability_quality_parity_blockers"]
    assert "QUALITY_PARITY_SERVER_OWNED_CALIBRATION_UNPROVEN" in health["WNBA"]["probability_quality_parity_blockers"]
    assert health["NBA"]["probability_quality_parity_status"] == "MODEL_CAPABILITY_UNPROVEN"
    assert "QUALITY_PARITY_EXACT_FITTED_SPECIALIST_UNPROVEN" in health["NBA"]["probability_quality_parity_blockers"]
    assert health["WNBA"]["probability_quality_required_metrics"] == list(REQUIRED_CALIBRATION_METRICS)
    assert health["WNBA"]["can_execute"] is False
