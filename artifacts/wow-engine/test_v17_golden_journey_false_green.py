"""P0 golden GPT Action acceptance must never turn summary flags into PASS.

These are offline evaluator contracts only. A passing unit test does not
constitute live editor, source, fitted model, or production host attestation.
"""
from copy import deepcopy

import pytest

from test_v17_engineering_effectiveness import _synthetic_golden_evidence
from v17.engineering_effectiveness import evaluate_golden_user_journey


@pytest.mark.parametrize(
    "path,value,code",
    [
        ("action_operation_id", "getWowV17BackendHealth", "FAIL_ACTION_INVOCATION"),
        ("action_receipt", None, "FAIL_ACTION_INVOCATION"),
        ("verification_skipped", True, "FAIL_ACTION_INVOCATION"),
        ("action_receipt_status", "LIVE_GPT_ACTION_RESULT_INVALID", "FAIL_ACTION_INVOCATION"),
        ("source_inventory_evidence", None, "FAIL_PROP_ACQUISITION"),
        ("display_receipt", None, "FAIL_RESPONSE_HANDOFF"),
        ("probability_source", "SPORTSBOOK_IMPLIED", "FAIL_GOVERNANCE"),
        ("llm_substitution", True, "FAIL_GOVERNANCE"),
        ("rows_completed", 0, "FAIL_GOVERNED_SCORING"),
        ("valid_governed_row_count", 2, "FAIL_GOVERNED_SCORING"),
    ],
)
def test_summary_flags_do_not_authorize_missing_or_forged_proof(path, value, code):
    receipt = deepcopy(_synthetic_golden_evidence())
    receipt[path] = value
    outcome = evaluate_golden_user_journey(receipt)
    assert outcome["status"] != "PASS", path
    assert any(part.startswith(code) for part in outcome["blockers"]), outcome


@pytest.mark.parametrize(
    "key,value",
    [
        ("replayed", True),
        ("stale", True),
        ("readback_verified", False),
        ("freshness_verified", False),
        ("immutable_receipt_hash", "not-an-immutable-hash"),
        ("request_id", "a-different-request"),
        ("action_operation_id", "scoreAnotherOperation"),
        ("can_execute", True),
    ],
)
def test_action_receipt_missing_provenance_rejects_pass(key, value):
    receipt = deepcopy(_synthetic_golden_evidence())
    receipt["action_receipt"][key] = value
    result = evaluate_golden_user_journey(receipt)
    assert result["status"] != "PASS"
    assert result["can_execute"] is False


def test_unreadable_page_is_not_masked_as_model_unavailable():
    evidence = _synthetic_golden_evidence()
    evidence["source_ingestion_complete"] = False
    evidence["source_ingestion_failure_code"] = "SOURCE_PAGE_UNREADABLE:4"
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] == "FAIL_SOURCE_INGESTION"
    assert "SOURCE_PAGE_UNREADABLE:4" in result["blockers"]
    assert "FAIL_SOURCE_INGESTION:SOURCE_PAGE_UNREADABLE:4" in result["blockers"]
    assert not any("MODEL_UNAVAILABLE" in x for x in result["blockers"])


@pytest.mark.parametrize(
    "key,value,stage",
    [
        ("scorer_failure_code", "MODEL_SCORER_FAILED", "FAIL_GOVERNED_SCORING"),
        ("persisted_receipt_failure_code", "PERSISTENCE_FAILURE", "FAIL_PERSISTENCE"),
        ("response_handoff_failure_code", "CANONICAL_RESULT_NOT_DISPLAYED", "FAIL_RESPONSE_HANDOFF"),
    ],
)
def test_each_producer_failure_retains_its_typed_code_and_stage(key, value, stage):
    evidence = _synthetic_golden_evidence()
    evidence[key] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] == stage
    assert value in result["blockers"]
    assert f"{stage}:{value}" in result["blockers"]


def test_partial_acquisition_and_false_empty_slate_are_not_passes():
    evidence = _synthetic_golden_evidence(empty=True)
    evidence["source_inventory_evidence"]["empty_slate_verified"] = False
    assert evaluate_golden_user_journey(evidence)["status"] != "PASS"
    nonempty = _synthetic_golden_evidence()
    nonempty["acquired_row_count"] = 0
    assert evaluate_golden_user_journey(nonempty)["status"] != "PASS"


def test_success_requires_every_correlated_receipt():
    assert evaluate_golden_user_journey(_synthetic_golden_evidence())["status"] == "PASS"
    assert evaluate_golden_user_journey(_synthetic_golden_evidence(empty=True))["status"] == "PASS"
