"""Tests for honest candidate conservation, measured availability and model closure."""
import pytest

from v17.product_outcome_acceptance import (
    audit_handoffs, model_restoration_acceptance, product_outcome_scorecard,
    valid_numeric_package,
)


def scored(**overrides):
    result = {
        "request_id": "req-1", "candidate_id": "evt-1",
        "sport": "NFL", "market_family": "MONEYLINE",
        "disposition": "SCORED", "eligible": True,
        "current_owner": "LLP_TEAM_BETTING_ENGINE",
        "raw_model_probability": .63,
        "calibrated_probability": .61, "calibrated_lower_bound": .55,
        "controlling_specialist": "NFL_GAME_WIN_V17",
        "model_artifact_id": "fitted:v17", "model_timestamp": "2026-10-25T16:00:00Z",
        "terminal_receipt_ref": "receipt:1",
        "probability_receipt_verified": True,
        "rank_eligible": True, "terminal_rank_eligible_verified": True,
        "event_status": "PREGAME",
        "market_status": "MARKET_DATA_UNOBTAINABLE",
    }
    result.update(overrides)
    return result


def req(**overrides):
    result = {
        "request_id": "req-1", "supported": True,
        "output_delivered_verified": True,
        "expected_canonical_count": 1, "scope_evidence_ref": "source:run-1",
        "provider_attempts": 1, "repair_cycles": 0,
    }
    result.update(overrides)
    return result


def valid_closure():
    sha = "a" * 40
    merge = "b" * 40
    result = {
        "incident_id": "1841",
        "implementation_actor": "eng-app",
        "review_actor": "independent-review",
        "qa_actor": "independent-qa",
        "change_class": "B", "root_cause_evidence": "issue:1841",
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
        "reviewed_head_sha": sha, "ci_sha": sha, "review_sha": sha,
        "authorized_merge_sha": merge, "deployed_sha": merge,
        "action_sha": merge,
        "action_run_id": "run-1", "persistence_run_id": "run-1",
        "reconciliation_run_id": "run-1",
    }
    for k in (
        "focused_tests_pass", "regression_tests_pass", "ci_green",
        "security_authorized", "independent_review_approved",
        "independent_qa_approved", "release_authorized",
        "production_db_identity_verified", "action_acceptance_pass",
        "persistence_readback_pass", "reconciliation_pass",
        "terminal_states_concordant",
    ):
        result[k] = True
    return result


def valid_product():
    result = scored()
    result.update({
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
        "correct_sport_market_route": True, "model_invoked_verified": True,
        "dynamic_calibration_verified": True, "final_refresh_verified": True,
        "full_requested_scope_reconciled": True,
        "sirt_independent_verification": True,
        "run_id": "run-1", "deployed_sha": "b" * 40,
    })
    return result


def test_baseline_reports_full_metrics_without_market_price():
    r = product_outcome_scorecard([req()], [scored()])
    assert r["audit_complete"]
    assert r["candidate_conservation_rate"] == 1
    assert r["product_outcome_completion_rate"] == 1
    assert r["first_pass_completion_rate"] == 1
    assert r["reliable_decision_availability"] == 1
    assert r["can_execute"] is False


def test_hold_is_reconciled_but_not_probability():
    row = scored(
        disposition="HELD", rank_eligible=False,
        current_owner="AMRT", failure_code="MODEL_INPUTS_INSUFFICIENT",
        evidence_ref="source:missing", next_action="hydrate event",
        reentry_trigger="source snapshot available",
    )
    r = product_outcome_scorecard([req(provider_attempts=2)], [row])
    assert r["candidate_conservation_rate"] == 1
    assert r["reliable_decision_availability"] == 0
    assert r["first_pass_completion_rate"] == 0


def test_pending_candidate_prevents_false_completion():
    row = scored(
        disposition="PENDING", rank_eligible=False,
        current_owner="AMRT", failure_code="MODEL_SCORER_FAILED",
        evidence_ref="run:1", next_action="retry safe scorer",
        reentry_trigger="next worker availability",
    )
    r = product_outcome_scorecard([req()], [row])
    assert r["candidate_conservation_rate"] == 0
    assert r["product_outcome_completion_rate"] == 0


@pytest.mark.parametrize("changes", [
    {"calibrated_probability": float("nan")},
    {"calibrated_lower_bound": .79},
    {"raw_model_probability": True},
    {"controlling_specialist": ""},
    {"terminal_receipt_ref": ""},
    {"probability_receipt_verified": False},
])
def test_unproven_scored_row_fails_closed(changes):
    row = scored(**changes)
    assert not valid_numeric_package(row)
    r = product_outcome_scorecard([req()], [row])
    assert not r["audit_complete"]
    assert r["rows_unresolved"] == 1
    assert r["product_outcome_completion_rate"] == 0


def test_live_requires_dedicated_certified_specialist():
    row = scored(event_status="LIVE")
    assert product_outcome_scorecard([req()], [row])["rows_unresolved"] == 1
    row["dedicated_live_specialist_certified"] = True
    assert product_outcome_scorecard([req()], [row])["rows_unresolved"] == 0


def test_duplicate_thesis_never_multiplies_proof():
    rows = [scored(), scored()]
    r = product_outcome_scorecard([req()], rows)
    assert r["rows_unique"] == 1
    assert not r["audit_complete"]
    assert "ROW_1_DUPLICATE_CANONICAL_ID" in r["findings"]


def test_unattributed_hold_cannot_be_counted_as_closed():
    row = scored(disposition="HELD", rank_eligible=False, failure_code="MODEL_UNAVAILABLE")
    r = product_outcome_scorecard([req()], [row])
    assert r["rows_unresolved"] == 1
    assert not r["audit_complete"]


def test_empty_snapshot_not_green_or_divided_by_zero():
    r = product_outcome_scorecard([], [])
    assert r["candidate_conservation_rate"] is None
    assert r["product_outcome_completion_rate"] is None
    assert r["reliable_decision_availability"] is None


def test_no_candidates_must_be_explicitly_attested():
    assert product_outcome_scorecard([req()], [])["completed_requests"] == 0
    assert product_outcome_scorecard(
        [req(expected_canonical_count=0, no_current_candidates_verified=True)], [],
    )["completed_requests"] == 1


def test_unknown_scope_not_reported_as_full_availability():
    r = product_outcome_scorecard([req()], [scored(eligible=None)])
    assert r["reliable_decision_availability"] is None
    assert not r["audit_complete"]


def test_claimed_qualified_hold_cannot_pass():
    row = scored(disposition="HELD", current_owner="AMRT",
                 failure_code="MODEL_UNAVAILABLE", evidence_ref="manifest:missing",
                 next_action="register model", reentry_trigger="certification")
    r = product_outcome_scorecard([req()], [row])
    assert r["rows_unresolved"] == 1


def test_handoff_needs_owner_evidence_and_reentry():
    good = dict(incident_id="1841", current_owner="AMRT",
                next_owner="SIRT", state="HELD", evidence_ref="issue:1841",
                next_action="verify exact action", reentry_trigger="new LIVE SHA")
    assert audit_handoffs([good])["valid"]
    assert not audit_handoffs([{**good, "next_action": ""}])["valid"]
    assert not audit_handoffs([{**good, "state": "FIXED_AND_VERIFIED"}])["valid"]
    assert audit_handoffs([{**good, "state": "FIXED_AND_VERIFIED",
                            "independent_production_closure_verified": True}])["valid"]


def test_complete_production_and_model_proof_is_required():
    result = model_restoration_acceptance(valid_closure(), valid_product())
    assert result["status"] == "FIXED_AND_VERIFIED"
    assert result["blockers"] == ()


@pytest.mark.parametrize("closure_change,product_change,code", [
    ({"independent_qa_approved": False}, {}, "INDEPENDENT_QA_APPROVED"),
    ({}, {"dynamic_calibration_verified": False}, "DYNAMIC_CALIBRATION"),
    ({}, {"full_requested_scope_reconciled": False}, "FULL_REQUESTED_SCOPE"),
    ({}, {"sirt_independent_verification": False}, "SIRT_INDEPENDENT"),
    ({}, {"run_id": "other"}, "PRODUCT_ACTION_RUN_MISMATCH"),
    ({}, {"deployed_sha": "c" * 40}, "PRODUCT_DEPLOYMENT_SHA_MISMATCH"),
    ({}, {"calibrated_lower_bound": None}, "NUMERIC_PACKAGE"),
    ({}, {"event_status": "LIVE"}, "LIVE_MODEL_UNCERTIFIED"),
])
def test_model_repair_must_not_false_close(closure_change, product_change, code):
    closure = {**valid_closure(), **closure_change}
    product = {**valid_product(), **product_change}
    r = model_restoration_acceptance(closure, product)
    assert r["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert any(code in item for item in r["blockers"])


def test_historical_27_market_partial_replay_never_claims_full_board():
    # Exact scope known independently: eight returned holds/rows cannot close 27.
    rows = [scored(candidate_id=f"source-row-{i}") for i in range(8)]
    result = product_outcome_scorecard(
        [req(expected_canonical_count=27, scope_evidence_ref="source:27-market-transcription")],
        rows,
    )
    assert result["rows_missing_from_declared_scope"] == 19
    assert result["rows_unresolved"] == 19
    assert result["candidate_conservation_rate"] == pytest.approx(8 / 27)
    assert result["product_outcome_completion_rate"] == 0
    assert result["first_pass_completion_rate"] == 0
    assert not result["audit_complete"]
    assert any("SCOPE_COUNT_MISMATCH:8/27" in issue for issue in result["findings"])


@pytest.mark.parametrize("bad_count", [None, True, -1, 1.5, "27"])
def test_supported_scope_requires_verified_integer_denominator(bad_count):
    result = product_outcome_scorecard(
        [req(expected_canonical_count=bad_count)], [scored()],
    )
    assert result["completed_requests"] == 0
    assert result["candidate_conservation_rate"] is None
    assert any("SCOPE_UNATTESTED" in issue for issue in result["findings"])


def test_empty_source_reference_cannot_authorize_full_board():
    result = product_outcome_scorecard(
        [req(scope_evidence_ref="")], [scored()],
    )
    assert result["completed_requests"] == 0
    assert not result["audit_complete"]


def test_duplicate_canonical_row_prevents_request_completion():
    result = product_outcome_scorecard([req()], [scored(), scored()])
    assert result["completed_requests"] == 0
    assert not result["audit_complete"]


def test_scored_row_missing_proof_invalidates_entire_request_completion():
    result = product_outcome_scorecard(
        [req()], [scored(calibrated_probability=float("nan"))],
    )
    assert result["completed_requests"] == 0


def test_zero_market_empty_source_must_be_explicitly_attested():
    result = product_outcome_scorecard(
        [req(expected_canonical_count=0, no_current_candidates_verified=False)], [],
    )
    assert result["completed_requests"] == 0
