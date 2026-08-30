from calibration_publication_api import _collect_blockers, _publication_only
from calibration_publication_lane import blocker_scopes, resolve_lane_separation


def test_forward_shadow_is_calibration_publication_scoped():
    assert blocker_scopes(["FORWARD_SHADOW_NOT_COMPLETED"]) == (
        "CALIBRATION",
        "PUBLICATION",
    )
    assert _publication_only(["FORWARD_SHADOW_NOT_COMPLETED"]) is True


def test_publication_lock_preserves_completed_specialist_research():
    decision = resolve_lane_separation(
        specialist_available=True,
        specialist_name="wow.mlb-pitcher-failure-path-expert",
        specialist_output_complete=True,
        calibration_health_status="BLOCKED",
        governed_probability_capability="UNAVAILABLE",
        blockers=["FORWARD_SHADOW_NOT_COMPLETED"],
        existing_ceiling="FINAL_APPROVED",
    )
    assert decision.specialist_model_capability == "AVAILABLE"
    assert decision.specialist_model_status == "COMPLETED"
    assert decision.calibration_status == "UNKNOWN_OR_BLOCKED"
    assert decision.governed_publishable is False
    assert decision.probability_claim_status == "SPECIALIST_RAW_RESEARCH_ONLY"
    assert decision.terminal_ceiling == "MODEL_QUALIFIED_HOLD"
    assert decision.failed_contract_scope == ("CALIBRATION", "PUBLICATION")


def test_manual_lane_respects_hold_ceiling_and_cap():
    decision = resolve_lane_separation(
        specialist_available=True,
        specialist_name="wow.example-specialist",
        specialist_output_complete=False,
        calibration_health_status="BLOCKED",
        governed_probability_capability="UNAVAILABLE",
        blockers=["FORWARD_SHADOW_NOT_COMPLETED"],
        manual_lane_permitted=True,
        manual_lane_used=True,
        manual_confidence_cap=0.62,
    )
    assert decision.manual_lane_used is True
    assert decision.manual_confidence_cap == 0.62
    assert decision.probability_claim_status == "MANUAL_ESTIMATE_RESEARCH_ONLY"
    assert decision.terminal_ceiling == "MODEL_QUALIFIED_HOLD"
    assert decision.governed_publishable is False


def test_true_specialist_failure_is_model_unavailable():
    decision = resolve_lane_separation(
        specialist_available=False,
        specialist_name="MODEL_UNAVAILABLE",
        specialist_output_complete=False,
        calibration_health_status="BLOCKED",
        governed_probability_capability="UNAVAILABLE",
        blockers=["SPECIALIST_MODEL_UNAVAILABLE"],
    )
    assert decision.specialist_model_capability == "UNAVAILABLE"
    assert decision.probability_claim_status == "MODEL_UNAVAILABLE"
    assert decision.terminal_ceiling == "MODEL_UNAVAILABLE"
    assert decision.governed_publishable is False


def test_unclassified_blocker_cannot_use_publication_bypass():
    assert _publication_only(["FORWARD_SHADOW_NOT_COMPLETED", "SOME_UNKNOWN_FAILURE"]) is False
    assert _publication_only(["SOME_UNKNOWN_FAILURE"]) is False


def test_preflight_transport_failures_are_global_and_cannot_use_publication_bypass():
    for blocker in (
        "GOVERNED_PROBABILITY_PREFLIGHT_UNAVAILABLE",
        "GOVERNED_PROBABILITY_PREFLIGHT_INVALID_RESPONSE",
        "GOVERNED_PROBABILITY_UNAVAILABLE",
    ):
        assert blocker_scopes([blocker]) == ("GLOBAL",)
        assert _publication_only([blocker]) is False


def test_legacy_deployment_not_ready_remains_publication_scoped():
    assert blocker_scopes(["GOVERNED_DEPLOYMENT_NOT_READY"]) == (
        "CALIBRATION",
        "PUBLICATION",
    )
    assert _publication_only(["GOVERNED_DEPLOYMENT_NOT_READY"]) is True


def test_known_publication_lock_plus_global_failure_fails_closed():
    blockers = ["FORWARD_SHADOW_NOT_COMPLETED", "GOVERNED_PROBABILITY_PREFLIGHT_UNAVAILABLE"]
    assert blocker_scopes(blockers) == ("GLOBAL", "CALIBRATION", "PUBLICATION")
    assert _publication_only(blockers) is False


def test_market_failure_scope_does_not_collapse_other_lanes():
    assert blocker_scopes(["MARKET_EXACT_LINE_UNAVAILABLE"]) == ("MARKET",)


def test_blocker_formatter_finds_nested_preflight_reason():
    payload = {
        "evidence": {"status_reason": "FORWARD_SHADOW_NOT_COMPLETED"},
        "blockers": ["CALIBRATION_HEALTH_BLOCKED"],
    }
    assert _collect_blockers(payload) == [
        "FORWARD_SHADOW_NOT_COMPLETED",
        "CALIBRATION_HEALTH_BLOCKED",
    ]
