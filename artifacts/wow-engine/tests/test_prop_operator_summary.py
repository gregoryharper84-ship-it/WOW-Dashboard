"""Operator projection must never grant model or execution authority."""
from v17.prop_operator_summary import summarize_prop_routes

STAGES = {
    "DURABLE_FORWARD_EVIDENCE_AUDIT": "PASS",
    "CALIBRATION_CERTIFICATION_READINESS": "PASS",
    "PRODUCTION_REGISTRATION_AUDIT": "PASS",
    "CALIBRATOR_CANDIDATE_EVIDENCE": "PASS",
}
ROUTE = "NFL:PASSING_YARDS"


def route_row(**overrides):
    row = {
        "sport": "NFL", "stat_type": "PASSING_YARDS", "row_kind": "ROUTE_STATUS",
        "lifecycle_status": "CERTIFICATION_REVIEW_REQUIRED",
        "blockers": ["CERTIFICATION_POLICY_REVIEW_REQUIRED", "CANONICAL_ACTION_CANARY_REQUIRED"],
        "production_numerical_authority": False,
        "action_canary_verified": False,
        "model_adapter_registered": True,
        "calibrator_adapter_registered": True,
        "hydration_route_registered": True,
        "can_execute": False,
    }
    row.update(overrides)
    return row


def view(rows, *, stages=None, routes=None):
    return summarize_prop_routes(
        {"route_rows": rows, "can_execute": False},
        requested_routes=routes if routes is not None else [ROUTE],
        stage_statuses=stages if stages is not None else STAGES,
    )


def test_certification_hold_surfaces_one_priority_blocker_without_publishing():
    status = view([route_row()])
    assert status["schema"] == "WOW_PROP_OPERATOR_SUMMARY_V1"
    assert status["registered_observed_n"] == 0
    assert status["held_n"] == 1
    assert status["routes"][0]["route"] == ROUTE
    assert status["routes"][0]["state"] == "HOLD"
    assert status["routes"][0]["first_blocker"] == "CERTIFICATION_POLICY_REVIEW_REQUIRED"
    assert status["routes"][0]["blockers"] == [
        "CERTIFICATION_POLICY_REVIEW_REQUIRED", "CANONICAL_ACTION_CANARY_REQUIRED"
    ]
    assert status["can_execute"] is False
    assert status["probability_publishable"] is False
    assert status["authorizes_model_promotion"] is False


def test_missing_route_is_explicit_not_an_empty_slate():
    status = view([])
    assert status["held_n"] == 1
    assert status["routes"][0]["first_blocker"] == "ROUTE_STATUS_NOT_OBSERVED"


def test_failed_audit_stage_prevents_production_claim_even_if_route_says_registered():
    status = view([
        route_row(lifecycle_status="PRODUCTION_REGISTERED", blockers=[],
                  production_numerical_authority=True, action_canary_verified=True),
    ], stages={**STAGES, "PRODUCTION_REGISTRATION_AUDIT": "FAILED"})
    assert status["registered_observed_n"] == 0
    assert status["routes"][0]["state"] == "HOLD"
    assert status["routes"][0]["first_blocker"] == "AUDIT_STAGE_FAILED:PRODUCTION_REGISTRATION_AUDIT"


def test_duplicate_route_rows_do_not_silently_choose_one():
    status = view([route_row(), route_row()])
    assert status["routes"][0]["first_blocker"] == "ROUTE_STATUS_AMBIGUOUS"


def test_registration_requires_existing_authority_flags_and_empty_blockers():
    safe = route_row(lifecycle_status="PRODUCTION_REGISTERED", blockers=[],
                     production_numerical_authority=True, action_canary_verified=True)
    reported = view([safe])
    assert reported["registered_observed_n"] == 1
    assert reported["routes"][0]["state"] == "REGISTERED_OBSERVED"
    assert reported["probability_publishable"] is False
    for field in ("production_numerical_authority", "action_canary_verified",
                  "model_adapter_registered", "calibrator_adapter_registered",
                  "hydration_route_registered"):
        held = view([route_row(**{**safe, field: False})])
        assert held["routes"][0]["state"] == "HOLD", field
        assert held["registered_observed_n"] == 0


def test_can_execute_true_is_always_held():
    safe = route_row(lifecycle_status="PRODUCTION_REGISTERED", blockers=[],
                     production_numerical_authority=True, action_canary_verified=True,
                     can_execute=True)
    result = view([safe])
    assert result["routes"][0]["first_blocker"] == "EXECUTION_AUTHORITY_VIOLATION"
    assert result["registered_observed_n"] == 0


def test_missing_stage_statuses_fail_closed():
    result = view([route_row()], stages={})
    assert result["routes"][0]["first_blocker"].startswith("AUDIT_STAGE_NOT_VERIFIED:")


def test_no_mutation_of_source_dashboard_or_statuses():
    source = route_row()
    stages = dict(STAGES)
    result = view([source], stages=stages)
    result["routes"][0]["blockers"].append("TAMPER")
    assert source["blockers"] == [
        "CERTIFICATION_POLICY_REVIEW_REQUIRED", "CANONICAL_ACTION_CANARY_REQUIRED"
    ]
    assert stages == STAGES


def test_unknown_requested_route_returns_typed_hold_not_promotion():
    result = view([route_row()], routes=["NBA:POINTS"])
    assert result["routes"][0]["route"] == "NBA:POINTS"
    assert result["routes"][0]["first_blocker"] == "ROUTE_STATUS_NOT_OBSERVED"


def test_invalid_registration_evidence_is_a_hold():
    safe = route_row(lifecycle_status="PRODUCTION_REGISTERED", blockers=[],
                     production_numerical_authority=True, action_canary_verified=True)
    missing_execution_flag = {**safe}
    missing_execution_flag.pop("can_execute")
    status = view([missing_execution_flag])
    assert status["registered_observed_n"] == 0
    assert status["routes"][0]["first_blocker"] == "EXECUTION_AUTHORITY_UNVERIFIED"

    invalid_blockers = {**safe, "blockers": "missing"}
    status = view([invalid_blockers])
    assert status["registered_observed_n"] == 0
    assert status["routes"][0]["first_blocker"] == "ROUTE_BLOCKERS_UNVERIFIED"
