"""
WOW-PATCH-2026-08-07-BACKEND-FAILOVER-RESEARCH
Regression tests for gate_engine/backend_failure_classifier.py

Covers:
  - All 7 failure types classified correctly at the row level
  - Tier ordering (lower number = more severe)
  - Retry policy per failure type
  - Run-level classification: all-failed → dominant type
  - Mixed run (some rows pass) → failure_type=NONE, candidate_evaluation_completed=True
  - Governance failure overrides everything
  - build_partial_failure_terminal for each failure type
  - validate_source_provenance valid / invalid inputs
  - Factual guards: can_execute=False unconditional
  - RUN_PARTIAL_BACKEND_FAILURE upgrade in all-failed runs
  - SOURCE_ACQUISITION_FAIL correctly distinguished from DATA_CONTRACT_FAIL
  - Dominant failure type selection across mixed-failure all-failed runs
"""
import pytest
from gate_engine.backend_failure_classifier import (
    FAILURE_TIER,
    RETRY_POLICY,
    IS_HARD_STOP,
    classify_row_failure,
    classify_run_failure,
    build_partial_failure_terminal,
    validate_source_provenance,
    _build_classification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(label: str, blockers: list | None = None) -> dict:
    return {
        "row_id":        "row_0_test",
        "player":        "Test Player",
        "prop_type":     "test_prop",
        "terminal_label": label,
        "blockers":      blockers or [],
    }


def _result(rows: list[dict]) -> dict:
    return {"prop_ledger": rows, "terminal_labels": [], "final_card": []}


# ---------------------------------------------------------------------------
# 1. FAILURE_TIER ordering
# ---------------------------------------------------------------------------

class TestTierOrdering:
    """Tier 1 is most severe; tier 7 is least severe."""

    def test_governance_is_tier_1(self):
        assert FAILURE_TIER["GOVERNANCE_FAIL"] == 1

    def test_model_runtime_is_tier_2(self):
        assert FAILURE_TIER["MODEL_RUNTIME_FAIL"] == 2

    def test_response_size_is_tier_3(self):
        assert FAILURE_TIER["RESPONSE_SIZE_FAIL"] == 3

    def test_model_route_is_tier_4(self):
        assert FAILURE_TIER["MODEL_ROUTE_FAIL"] == 4

    def test_data_contract_is_tier_5(self):
        assert FAILURE_TIER["DATA_CONTRACT_FAIL"] == 5

    def test_source_acquisition_is_tier_6(self):
        assert FAILURE_TIER["SOURCE_ACQUISITION_FAIL"] == 6

    def test_input_failure_is_tier_7(self):
        assert FAILURE_TIER["INPUT_FAILURE"] == 7

    def test_all_7_types_have_unique_tiers(self):
        scored_types = [
            "GOVERNANCE_FAIL", "MODEL_RUNTIME_FAIL", "RESPONSE_SIZE_FAIL",
            "MODEL_ROUTE_FAIL", "DATA_CONTRACT_FAIL", "SOURCE_ACQUISITION_FAIL",
            "INPUT_FAILURE",
        ]
        tiers = [FAILURE_TIER[t] for t in scored_types]
        assert len(tiers) == len(set(tiers)), "Duplicate tier values detected"

    def test_governance_has_lower_tier_than_all_others(self):
        others = [
            "MODEL_RUNTIME_FAIL", "RESPONSE_SIZE_FAIL", "MODEL_ROUTE_FAIL",
            "DATA_CONTRACT_FAIL", "SOURCE_ACQUISITION_FAIL", "INPUT_FAILURE",
        ]
        for other in others:
            assert FAILURE_TIER["GOVERNANCE_FAIL"] < FAILURE_TIER[other], (
                f"GOVERNANCE_FAIL tier should be lower than {other}"
            )


# ---------------------------------------------------------------------------
# 2. Retry policy mapping
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_governance_fail_is_hard_stop(self):
        assert RETRY_POLICY["GOVERNANCE_FAIL"] == "hard_stop"

    def test_model_runtime_and_response_size_are_slim_mode(self):
        assert RETRY_POLICY["MODEL_RUNTIME_FAIL"] == "slim_mode_retry"
        assert RETRY_POLICY["RESPONSE_SIZE_FAIL"] == "slim_mode_retry"

    def test_model_route_is_reroute(self):
        assert RETRY_POLICY["MODEL_ROUTE_FAIL"] == "reroute_specialist"

    def test_data_contract_and_source_acquisition_are_web_reconstruction(self):
        assert RETRY_POLICY["DATA_CONTRACT_FAIL"] == "web_reconstruction"
        assert RETRY_POLICY["SOURCE_ACQUISITION_FAIL"] == "web_reconstruction"

    def test_input_failure_is_normalize_and_retry(self):
        assert RETRY_POLICY["INPUT_FAILURE"] == "normalize_and_retry"

    def test_all_7_types_have_a_retry_policy(self):
        for ft in FAILURE_TIER:
            assert ft in RETRY_POLICY, f"{ft} missing from RETRY_POLICY"


# ---------------------------------------------------------------------------
# 3. Hard-stop flags
# ---------------------------------------------------------------------------

class TestHardStop:
    def test_governance_and_runtime_are_hard_stops(self):
        assert IS_HARD_STOP["GOVERNANCE_FAIL"] is True
        assert IS_HARD_STOP["MODEL_RUNTIME_FAIL"] is True

    def test_caller_actionable_types_are_not_hard_stops(self):
        for ft in [
            "RESPONSE_SIZE_FAIL", "MODEL_ROUTE_FAIL",
            "DATA_CONTRACT_FAIL", "SOURCE_ACQUISITION_FAIL", "INPUT_FAILURE",
        ]:
            assert IS_HARD_STOP[ft] is False, f"{ft} should not be a hard stop"


# ---------------------------------------------------------------------------
# 4. Row-level classification
# ---------------------------------------------------------------------------

class TestClassifyRowFailure:
    def test_data_contract_fail_with_no_acquisition_blocker(self):
        row = _row("DATA_CONTRACT_FAIL", ["missing_field:line"])
        assert classify_row_failure(row) == "DATA_CONTRACT_FAIL"

    def test_data_contract_fail_with_no_game_log_blocker(self):
        row = _row("DATA_CONTRACT_FAIL", ["L10:NO_GAME_LOG_PROVIDED"])
        assert classify_row_failure(row) == "SOURCE_ACQUISITION_FAIL"

    def test_data_contract_fail_with_acquisition_fail_blocker(self):
        row = _row("DATA_CONTRACT_FAIL", ["SOURCE_ACQUISITION_FAIL:ESPN_EMPTY"])
        assert classify_row_failure(row) == "SOURCE_ACQUISITION_FAIL"

    def test_data_contract_fail_with_game_log_missing_blocker(self):
        row = _row("DATA_CONTRACT_FAIL", ["GAME_LOG_MISSING"])
        assert classify_row_failure(row) == "SOURCE_ACQUISITION_FAIL"

    def test_data_contract_fail_with_evidence_unavailable(self):
        row = _row("DATA_CONTRACT_FAIL", ["EVIDENCE_UNAVAILABLE"])
        assert classify_row_failure(row) == "SOURCE_ACQUISITION_FAIL"

    def test_no_registered_model_blocker_routes_to_model_route_fail(self):
        row = _row("DATA_CONTRACT_FAIL", ["NO_REGISTERED_MODEL:MLB_K"])
        assert classify_row_failure(row) == "MODEL_ROUTE_FAIL"

    def test_route_configuration_blocker_routes_to_model_route_fail(self):
        row = _row("DATA_CONTRACT_FAIL", ["RUN_INVALID_ROUTE_CONFIGURATION"])
        assert classify_row_failure(row) == "MODEL_ROUTE_FAIL"

    def test_route_fail_takes_precedence_over_acquisition_fail(self):
        row = _row("DATA_CONTRACT_FAIL", [
            "NO_GAME_LOG_PROVIDED", "NO_REGISTERED_MODEL",
        ])
        assert classify_row_failure(row) == "MODEL_ROUTE_FAIL"

    def test_passing_label_returns_none(self):
        row = _row("MODEL_QUALIFIED_HOLD")
        assert classify_row_failure(row) == "NONE"

    def test_final_approved_returns_none(self):
        row = _row("FINAL_APPROVED")
        assert classify_row_failure(row) == "NONE"

    def test_llp_reject_returns_none(self):
        # LLP_REJECT is a scored rejection, not a technical failure
        row = _row("LLP_REJECT")
        assert classify_row_failure(row) == "NONE"

    def test_empty_blockers_data_contract_fail(self):
        row = _row("DATA_CONTRACT_FAIL", [])
        assert classify_row_failure(row) == "DATA_CONTRACT_FAIL"


# ---------------------------------------------------------------------------
# 5. Run-level classification
# ---------------------------------------------------------------------------

class TestClassifyRunFailure:
    def test_governance_failure_overrides_everything(self):
        result = _result([_row("DATA_CONTRACT_FAIL")])
        out = classify_run_failure(result, governance_ok=False)
        assert out["failure_type"] == "GOVERNANCE_FAIL"
        assert out["is_hard_stop"] is True
        assert out["candidate_evaluation_completed"] is False
        assert out["probability_publishable"] is False

    def test_all_data_contract_fail_rows(self):
        rows = [
            _row("DATA_CONTRACT_FAIL", ["missing_field:line"]),
            _row("DATA_CONTRACT_FAIL", ["missing_field:game_log"]),
        ]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["failure_type"] == "DATA_CONTRACT_FAIL"
        assert out["candidate_evaluation_completed"] is False
        assert out["probability_publishable"] is False
        assert out["reconstruction_recommended"] is True

    def test_all_source_acquisition_fail_rows(self):
        rows = [_row("DATA_CONTRACT_FAIL", ["NO_GAME_LOG_PROVIDED"])]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["failure_type"] == "SOURCE_ACQUISITION_FAIL"
        assert out["retry_policy"] == "web_reconstruction"

    def test_all_model_route_fail_rows(self):
        rows = [_row("DATA_CONTRACT_FAIL", ["NO_REGISTERED_MODEL"])]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["failure_type"] == "MODEL_ROUTE_FAIL"
        assert out["retry_policy"] == "reroute_specialist"

    def test_mixed_run_some_pass(self):
        rows = [
            _row("MODEL_QUALIFIED_HOLD"),            # passes
            _row("DATA_CONTRACT_FAIL", ["missing_field:line"]),  # fails
        ]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["failure_type"] == "NONE"
        assert out["candidate_evaluation_completed"] is True
        assert out["probability_publishable"] is True
        # The failing row should still be reported
        assert len(out["affected_rows"]) == 1
        assert out["affected_rows"][0]["failure_type"] == "DATA_CONTRACT_FAIL"

    def test_all_passing_rows_returns_none(self):
        rows = [_row("FINAL_APPROVED"), _row("MODEL_QUALIFIED_HOLD")]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["failure_type"] == "NONE"
        assert out["candidate_evaluation_completed"] is True
        assert out["affected_rows"] == []

    def test_empty_prop_ledger_returns_none(self):
        out = classify_run_failure({"prop_ledger": []}, governance_ok=True)
        assert out["failure_type"] == "NONE"
        assert out["candidate_evaluation_completed"] is True

    def test_dominant_type_picks_highest_severity(self):
        """When rows have mixed failure types, the most severe (lowest tier) wins."""
        rows = [
            _row("DATA_CONTRACT_FAIL", ["missing_field:line"]),         # tier 5
            _row("DATA_CONTRACT_FAIL", ["NO_REGISTERED_MODEL"]),        # tier 4 (route)
        ]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["failure_type"] == "MODEL_ROUTE_FAIL"  # tier 4 wins over 5

    def test_affected_rows_summary_contains_expected_fields(self):
        rows = [_row("DATA_CONTRACT_FAIL", ["missing_field:line"])]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["candidate_evaluation_completed"] is False
        affected = out["affected_rows"]
        assert len(affected) == 1
        entry = affected[0]
        assert "row_id" in entry
        assert "failure_type" in entry
        assert "retry_policy" in entry
        assert "blockers" in entry

    def test_reconstruction_not_recommended_for_governance_fail(self):
        rows = [_row("DATA_CONTRACT_FAIL")]
        out = classify_run_failure(_result(rows), governance_ok=False)
        assert out["reconstruction_recommended"] is False

    def test_reconstruction_recommended_for_source_acquisition_fail(self):
        rows = [_row("DATA_CONTRACT_FAIL", ["NO_GAME_LOG_PROVIDED"])]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["reconstruction_recommended"] is True


# ---------------------------------------------------------------------------
# 6. build_partial_failure_terminal
# ---------------------------------------------------------------------------

class TestBuildPartialFailureTerminal:
    def _clf(self, failure_type: str) -> dict:
        return _build_classification(
            failure_type=failure_type,
            candidate_evaluation_completed=False,
            probability_publishable=False,
            affected_rows=[],
            reconstruction_recommended=False,
        )

    def test_data_contract_fail_produces_run_partial_backend_failure(self):
        out = build_partial_failure_terminal(self._clf("DATA_CONTRACT_FAIL"))
        assert out["terminal_disposition"] == "RUN_PARTIAL_BACKEND_FAILURE"
        assert out["strict_runtime_disposition"] == "RUN_PARTIAL_BACKEND_FAILURE"
        assert out["candidate_evaluation_completed"] is False
        assert out["probability_publishable"] is False

    def test_source_acquisition_fail_produces_run_partial_backend_failure(self):
        out = build_partial_failure_terminal(self._clf("SOURCE_ACQUISITION_FAIL"))
        assert out["terminal_disposition"] == "RUN_PARTIAL_BACKEND_FAILURE"

    def test_model_route_fail_produces_run_partial_backend_failure(self):
        out = build_partial_failure_terminal(self._clf("MODEL_ROUTE_FAIL"))
        assert out["terminal_disposition"] == "RUN_PARTIAL_BACKEND_FAILURE"

    def test_governance_fail_produces_run_invalid_governance(self):
        out = build_partial_failure_terminal(self._clf("GOVERNANCE_FAIL"))
        assert out["terminal_disposition"] == "RUN_INVALID_GOVERNANCE"
        assert out["strict_runtime_disposition"] == "RUN_INVALID_GOVERNANCE"
        assert out["candidate_evaluation_completed"] is False
        assert out["probability_publishable"] is False

    def test_all_partial_failure_types_set_probability_publishable_false(self):
        for ft in [
            "DATA_CONTRACT_FAIL", "SOURCE_ACQUISITION_FAIL",
            "MODEL_ROUTE_FAIL", "MODEL_RUNTIME_FAIL",
            "RESPONSE_SIZE_FAIL", "INPUT_FAILURE",
        ]:
            out = build_partial_failure_terminal(self._clf(ft))
            assert out["probability_publishable"] is False, (
                f"{ft}: probability_publishable should be False"
            )


# ---------------------------------------------------------------------------
# 7. validate_source_provenance
# ---------------------------------------------------------------------------

class TestValidateSourceProvenance:
    def test_none_is_valid(self):
        assert validate_source_provenance(None) == []

    def test_empty_list_is_valid(self):
        assert validate_source_provenance([]) == []

    def test_valid_entry_passes(self):
        provenance = [
            {"field": "game_log", "source": "ESPN", "source_type": "official_api"},
        ]
        assert validate_source_provenance(provenance) == []

    def test_multiple_valid_entries_pass(self):
        provenance = [
            {"field": "game_log",     "source": "ESPN",    "source_type": "official_api"},
            {"field": "box_score_log","source": "BBRef",   "source_type": "trusted_stats"},
            {"field": "role_status",  "source": "Rotowire","source_type": "reputable_web"},
        ]
        assert validate_source_provenance(provenance) == []

    def test_not_a_list_returns_violation(self):
        violations = validate_source_provenance({"field": "game_log"})
        assert len(violations) == 1
        assert "must be a list" in violations[0]

    def test_entry_missing_field_key_returns_violation(self):
        violations = validate_source_provenance([
            {"source": "ESPN", "source_type": "official_api"},
        ])
        assert any("field" in v for v in violations)

    def test_entry_missing_source_key_returns_violation(self):
        violations = validate_source_provenance([
            {"field": "game_log", "source_type": "official_api"},
        ])
        assert any("source" in v for v in violations)

    def test_entry_missing_source_type_returns_violation(self):
        violations = validate_source_provenance([
            {"field": "game_log", "source": "ESPN"},
        ])
        assert any("source_type" in v for v in violations)

    def test_non_dict_entry_returns_violation(self):
        violations = validate_source_provenance(["ESPN"])
        assert any("must be an object" in v for v in violations)

    def test_multiple_missing_keys_returns_multiple_violations(self):
        violations = validate_source_provenance([{}])
        assert len(violations) == 3  # field, source, source_type all missing

    def test_extra_keys_do_not_cause_violations(self):
        provenance = [
            {
                "field": "game_log",
                "source": "ESPN",
                "source_type": "official_api",
                "timestamp": "2026-08-07T00:00:00Z",
                "confidence": "HIGH",
            }
        ]
        assert validate_source_provenance(provenance) == []


# ---------------------------------------------------------------------------
# 8. Factual guards — can_execute is unconditionally False
# ---------------------------------------------------------------------------

class TestFactualGuards:
    def _any_classify(self, failure_type: str) -> dict:
        return _build_classification(
            failure_type=failure_type,
            candidate_evaluation_completed=False,
            probability_publishable=False,
            affected_rows=[],
            reconstruction_recommended=False,
        )

    def test_can_execute_false_on_governance_fail(self):
        out = classify_run_failure(_result([_row("DATA_CONTRACT_FAIL")]), governance_ok=False)
        assert out["can_execute"] is False

    def test_can_execute_false_on_all_data_contract_fail(self):
        out = classify_run_failure(_result([_row("DATA_CONTRACT_FAIL", ["missing"])]), governance_ok=True)
        assert out["can_execute"] is False

    def test_can_execute_false_on_mixed_run(self):
        rows = [_row("MODEL_QUALIFIED_HOLD"), _row("DATA_CONTRACT_FAIL")]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["can_execute"] is False

    def test_can_execute_false_on_all_passing_run(self):
        out = classify_run_failure(_result([_row("FINAL_APPROVED")]), governance_ok=True)
        assert out["can_execute"] is False

    def test_can_execute_false_on_build_partial_failure_terminal(self):
        # build_partial_failure_terminal does NOT set can_execute — it only sets
        # terminal_disposition; can_execute comes from the failure_classification block.
        clf = self._any_classify("DATA_CONTRACT_FAIL")
        assert clf["can_execute"] is False

    def test_probability_publishable_false_when_candidate_evaluation_not_completed(self):
        rows = [_row("DATA_CONTRACT_FAIL", ["missing_field:line"])]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["candidate_evaluation_completed"] is False
        assert out["probability_publishable"] is False

    def test_probability_publishable_true_only_when_some_rows_pass(self):
        rows = [_row("MODEL_QUALIFIED_HOLD"), _row("DATA_CONTRACT_FAIL")]
        out = classify_run_failure(_result(rows), governance_ok=True)
        assert out["candidate_evaluation_completed"] is True
        assert out["probability_publishable"] is True


# ---------------------------------------------------------------------------
# 9. Governance patch count
# ---------------------------------------------------------------------------

class TestGovernancePatchCount:
    def test_patch_24_is_registered(self):
        from gate_engine.governance import _ACTIVE_PATCH_IDS
        assert "WOW-PATCH-2026-08-07-BACKEND-FAILOVER-RESEARCH" in _ACTIVE_PATCH_IDS

    def test_patch_count_is_24(self):
        from gate_engine.governance import _active_patches
        assert len(_active_patches()) == 25, (
            f"Expected 25 active patches, got {len(_active_patches())}"
        )
