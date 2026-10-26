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
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
        "current_owner": "LLP_TEAM_BETTING_ENGINE",
        "raw_model_probability": .63,
        "calibrated_probability": .61, "calibrated_lower_bound": .55,
        "controlling_specialist": "NFL_GAME_WIN_V17",
        "model_artifact_id": "fitted:v17", "model_timestamp": "2026-10-25T16:00:00Z",
        "terminal_receipt_ref": "receipt:1",
        "probability_receipt_verified": True,
        "terminal_receipt_verified": True, "model_invoked_verified": True,
        "fitted_specialist_certified": True, "calibration_evidence_verified": True,
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
    # A one-market board still requires its exact source ID, not just count=1.
    if "expected_candidate_ids" not in overrides:
        result["expected_candidate_ids"] = (
            ["evt-1"] if result["expected_canonical_count"] == 1 else None
        )
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
        "requested_canonical_count": 1, "verified_terminal_count": 1,
        "scope_evidence_ref": "source:run-1",
        "terminal_receipts_reconciled_verified": True,
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
    {"terminal_receipt_verified": False},
    {"model_invoked_verified": False},
    {"fitted_specialist_certified": False},
    {"calibration_evidence_verified": False},
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
    assert r["candidate_conservation_rate"] is None


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
        [req(expected_canonical_count=27, scope_evidence_ref="source:27-market-transcription",
             expected_candidate_ids=[f"source-row-{i}" for i in range(27)])],
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


@pytest.mark.parametrize("changes", [
    {"requested_canonical_count": 27, "verified_terminal_count": 8},
    {"requested_canonical_count": 27, "verified_terminal_count": 27,
     "scope_evidence_ref": ""},
    {"requested_canonical_count": True},
    {"verified_terminal_count": None},
    {"terminal_receipts_reconciled_verified": False},
])
def test_true_production_model_restoration_requires_reconciled_scope(changes):
    product = {**valid_product(), **changes}
    r = model_restoration_acceptance(valid_closure(), product)
    assert r["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert "PRODUCT_EXACT_SCOPE_RECEIPTS_UNPROVEN" in r["blockers"]


def test_all_27_declared_market_labels_can_be_reconciled_exactly():
    ids = [f"source-row-{i}" for i in range(27)]
    result = product_outcome_scorecard(
        [req(expected_canonical_count=27, expected_candidate_ids=ids)],
        [scored(candidate_id=cid) for cid in ids],
    )
    assert result["audit_complete"]
    assert result["rows_missing_from_declared_scope"] == 0
    assert result["candidate_conservation_rate"] == 1
    assert result["completed_requests"] == 1


def test_false_green_equal_count_cannot_substitute_market_labels():
    # A complete count of 27 is not a complete reconciliation of original IDs.
    declared = [f"source-row-{i}" for i in range(27)]
    rows = [scored(candidate_id=f"substitute-{i}") for i in range(27)]
    result = product_outcome_scorecard(
        [req(expected_canonical_count=27, expected_candidate_ids=declared)], rows,
    )
    assert result["completed_requests"] == 0
    assert result["rows_missing_from_declared_scope"] == 27
    assert result["candidate_conservation_rate"] is None
    assert not result["audit_complete"]
    assert any("IDENTITY_MISMATCH" in issue for issue in result["findings"])


def test_multimarket_count_without_ids_does_not_pass():
    result = product_outcome_scorecard(
        [req(expected_canonical_count=2)],
        [scored(candidate_id="a"), scored(candidate_id="b")],
    )
    assert result["completed_requests"] == 0
    assert result["candidate_conservation_rate"] is None
    assert any("CANONICAL_IDENTITY_SCOPE_UNATTESTED" in issue
               for issue in result["findings"])


@pytest.mark.parametrize("ids", [["a", "a"], ["a", ""], ["a"], [1, 2], "a,b"])
def test_unverifiable_identity_inventory_never_certifies_full_board(ids):
    result = product_outcome_scorecard(
        [req(expected_canonical_count=2, expected_candidate_ids=ids)],
        [scored(candidate_id="a"), scored(candidate_id="b")],
    )
    assert result["completed_requests"] == 0
    assert not result["audit_complete"]


def test_terminal_row_must_have_reducer_provenance_and_no_execution():
    for change in (
        {"terminal_authority": None},
        {"terminal_authority": "SCORER"},
        {"can_execute": True},
        {"can_execute": None},
    ):
        result = product_outcome_scorecard([req()], [scored(**change)])
        assert not result["audit_complete"]
        assert result["rows_unresolved"] == 1
        assert result["completed_requests"] == 0
        assert result["rank_eligible_candidates"] == 0
        assert result["can_execute"] is False


def test_held_row_without_reducer_provenance_is_not_terminal_proof():
    result = product_outcome_scorecard(
        [req()], [scored(disposition="HELD", rank_eligible=False,
            current_owner="AMRT", failure_code="MODEL_UNAVAILABLE",
            evidence_ref="source:unavailable", next_action="restore specialist",
            reentry_trigger="certified model present", terminal_authority="SCORER")],
    )
    assert not result["audit_complete"]
    assert result["rows_unresolved"] == 1
    assert result["completed_requests"] == 0


def test_extra_terminal_rows_cannot_produce_false_full_conservation():
    result = product_outcome_scorecard(
        [req(expected_canonical_count=1)],
        [scored(candidate_id="evt-1"), scored(candidate_id="unexpected")],
    )
    assert not result["audit_complete"]
    assert result["completed_requests"] == 0
    assert result["candidate_conservation_rate"] is None
    assert any("SCOPE_COUNT_MISMATCH:2/1" in issue
               for issue in result["findings"])


def test_single_market_substitution_is_not_a_complete_source_board():
    result = product_outcome_scorecard([req()], [scored(candidate_id="substituted")])
    assert result["completed_requests"] == 0
    assert result["candidate_conservation_rate"] is None
    assert result["rows_missing_from_declared_scope"] == 1
    assert any("IDENTITY_MISMATCH" in issue for issue in result["findings"])


def test_single_market_missing_identity_inventory_fails_closed():
    result = product_outcome_scorecard(
        [req(expected_candidate_ids=None)], [scored()],
    )
    assert result["completed_requests"] == 0
    assert result["candidate_conservation_rate"] is None
    assert any("CANONICAL_IDENTITY_SCOPE_UNATTESTED" in issue
               for issue in result["findings"])


def test_orphan_terminal_row_cannot_inflate_conservation_or_availability():
    result = product_outcome_scorecard([], [scored(request_id="unknown")])
    assert not result["audit_complete"]
    assert result["candidate_conservation_rate"] is None
    assert result["reliable_decision_availability"] is None
    assert result["rank_eligible_candidates"] is None
    assert "ROW_REQUEST_NOT_IN_INVENTORY:unknown" in result["findings"]


def test_unsupported_request_row_cannot_inflate_conservation():
    result = product_outcome_scorecard(
        [req(supported=False)], [scored()],
    )
    assert result["candidate_conservation_rate"] is None
    assert result["rank_eligible_candidates"] is None
    assert result["completed_requests"] == 0
    assert "ROW_REQUEST_NOT_SUPPORTED:req-1" in result["findings"]


def test_unattributed_extra_row_suppresses_apparent_full_conservation():
    result = product_outcome_scorecard(
        [req()], [scored(), scored(candidate_id="")],
    )
    assert result["completed_requests"] == 1  # known request itself is terminal
    assert not result["audit_complete"]  # extra unknown row is not reconciled
    assert result["candidate_conservation_rate"] is None
    assert result["reliable_decision_availability"] is None
    assert result["rank_eligible_candidates"] is None
    assert "ROW_1_IDENTITY_MISSING" in result["findings"]
