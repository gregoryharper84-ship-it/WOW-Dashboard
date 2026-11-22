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
    assert outcome["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY"), path
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
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
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
    assert evaluate_golden_user_journey(evidence)["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    nonempty = _synthetic_golden_evidence()
    nonempty["acquired_row_count"] = 0
    assert evaluate_golden_user_journey(nonempty)["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")


def test_success_requires_every_correlated_receipt():
    assert evaluate_golden_user_journey(_synthetic_golden_evidence())["status"] == "STRUCTURAL_PASS_ONLY"
    assert evaluate_golden_user_journey(_synthetic_golden_evidence(empty=True))["status"] == "STRUCTURAL_PASS_ONLY"


@pytest.mark.parametrize(
    "key,value",
    [
        ("calibrated_probability", float("nan")),
        ("calibrated_probability_lower_bound", 0.99),
    ("calibrated_probability_lower_bound", 0),
    ("calibrated_probability_lower_bound", -0.00001),
        ("model_artifact_checksum", ""),
        ("controlling_specialist", None),
        ("terminal_authority", "CHATGPT"),
        ("calibration_status", "UNKNOWN"),
        ("probability_publishable", False),
        ("can_execute", True),
    ],
)
def test_invalid_governed_model_package_rejected(key, value):
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["governed_row_receipts"][0][key] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] == "FAIL_GOVERNED_SCORING" or result["status"] == "FAIL_GOVERNANCE"
    assert "FAIL_GOVERNED_SCORING:GOVERNED_ROW_RECEIPT_INVALID" in result["blockers"]


def test_governed_row_count_without_numeric_receipt_cannot_pass():
    evidence = _synthetic_golden_evidence()
    evidence["governed_row_receipts"] = []
    result = evaluate_golden_user_journey(evidence)
    assert "FAIL_GOVERNED_SCORING:GOVERNED_ROW_RECEIPTS_MISSING" in result["blockers"]
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")


@pytest.mark.parametrize("flag,value", [
    ("stale", "false"),
    ("stale", None),
    ("replayed", "unknown"),
    ("replayed", None),
    ("replayed", 0),
])
def test_malformed_or_missing_replay_attestations_are_not_false(flag, value):
    evidence = deepcopy(_synthetic_golden_evidence())
    if value is None:
        evidence["action_receipt"].pop(flag)
    else:
        evidence["action_receipt"][flag] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert any("ACTION_RECEIPT_" in blocker for blocker in result["blockers"])


@pytest.mark.parametrize("value", [
    "not-a-timestamp", "2026-10-15T16:00:00",
    "2100-01-01T00:00:00Z", "2000-01-01T00:00:00Z", None,
])
def test_source_observation_requires_offset_and_precedes_action(value):
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["source_inventory_evidence"]["source_observed_at"] = value
    result = evaluate_golden_user_journey(evidence)
    assert any(code in result["blockers"] for code in (
        "FAIL_PROP_ACQUISITION:SOURCE_TIMESTAMP_NOT_ATTESTED",
        "FAIL_PROP_ACQUISITION:SOURCE_STALE_OR_FUTURE",
    ))


@pytest.mark.parametrize("key,value", [
    ("source_snapshot_id", "other-snapshot"),
    ("request_id", "other-request"),
    ("freshness_verified", False),
])
def test_source_inventory_must_bind_to_the_current_request(key, value):
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["source_inventory_evidence"][key] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert any(b.startswith("FAIL_PROP_ACQUISITION:") for b in result["blockers"])


@pytest.mark.parametrize("value", [
    "not-a-timestamp", "2026-10-15T15:59:00Z",
    "2026-10-15T16:02:00", None,
])
def test_display_timestamp_must_follow_action_with_offset(value):
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["display_receipt"]["displayed_at"] = value
    assert "FAIL_RESPONSE_HANDOFF:DISPLAY_TIMESTAMP_NOT_ATTESTED" in (
        evaluate_golden_user_journey(evidence)["blockers"]
    )


def test_display_receipt_immutable_hash_must_match_action_receipt():
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["display_receipt"]["immutable_receipt_hash"] = "f" * 64
    assert "FAIL_RESPONSE_HANDOFF:DISPLAY_RECEIPT_HASH_MISMATCH" in (
        evaluate_golden_user_journey(evidence)["blockers"]
    )


@pytest.mark.parametrize("key,value", [
    ("calibrated_probability", 0),
    ("calibrated_probability", 1),
    ("calibrated_probability", True),
    ("model_artifact_checksum", "not-a-checksum"),
    ("model_artifact_checksum", "sha256:abcd"),
])
def test_strict_model_package_domain_and_digest_identity(key, value):
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["governed_row_receipts"][0][key] = value
    outcome = evaluate_golden_user_journey(evidence)
    assert outcome["status"] == "FAIL_GOVERNED_SCORING"
    assert "FAIL_GOVERNED_SCORING:GOVERNED_ROW_RECEIPT_INVALID" in outcome["blockers"]


def test_old_but_ordered_source_and_action_receipts_cannot_claim_current_health():
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["source_inventory_evidence"]["source_observed_at"] = "2000-01-01T00:00:00Z"
    evidence["action_receipt"]["invoked_at"] = "2000-01-01T00:01:00Z"
    evidence["display_receipt"]["displayed_at"] = "2000-01-01T00:02:00Z"
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert "FAIL_PROP_ACQUISITION:SOURCE_STALE_OR_FUTURE" in result["blockers"]
    assert "FAIL_ACTION_INVOCATION:ACTION_STALE_OR_FUTURE" in result["blockers"]
    assert "FAIL_RESPONSE_HANDOFF:DISPLAY_STALE_OR_FUTURE" in result["blockers"]


def test_fresh_synthetic_evidence_is_only_structurally_valid_not_production_verified():
    evidence = deepcopy(_synthetic_golden_evidence())
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] == "STRUCTURAL_PASS_ONLY"
    assert result["production_verified"] is False
    assert result["independent_qa_verified"] is False


@pytest.mark.parametrize("value", [True, False, None, "1", "0001", "9" * 5000, -1])
def test_untrusted_inventory_count_never_accepts_bool_or_unbounded_integer(value):
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["source_inventory_evidence"]["acquired_row_count"] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert any(s.startswith("FAIL_PROP_ACQUISITION") for s in result["blockers"])


@pytest.mark.parametrize("value", [None, True, "false", 0, 1])
def test_verification_skipped_must_be_explicit_false(value):
    evidence = deepcopy(_synthetic_golden_evidence())
    if value is None:
        evidence.pop("verification_skipped")
    else:
        evidence["verification_skipped"] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert "FAIL_ACTION_INVOCATION:VERIFICATION_SKIPPED" in result["blockers"]


def test_very_large_digit_string_is_typed_denial_not_exception():
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["rows_in"] = "9" * 5000
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert "FAIL_GOVERNED_SCORING:ROW_RECONCILIATION_MISMATCH" in result["blockers"]


@pytest.mark.parametrize("value", [10**1000, -(10**1000)])
@pytest.mark.parametrize("key", ["calibrated_probability", "calibrated_probability_lower_bound"])
def test_unbounded_probability_integers_are_typed_holds_not_overflows(key, value):
    evidence = deepcopy(_synthetic_golden_evidence())
    evidence["governed_row_receipts"][0][key] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] == "FAIL_GOVERNED_SCORING"
    assert "FAIL_GOVERNED_SCORING:GOVERNED_ROW_RECEIPT_INVALID" in result["blockers"]


@pytest.mark.parametrize("value", [None, [], "string", 7, False])
def test_non_object_receipts_fail_closed_before_any_field_access(value):
    result = evaluate_golden_user_journey(value)
    assert result["status"] == "FAIL_ACTION_INVOCATION"
    assert result["blockers"] == ["FAIL_ACTION_INVOCATION:NON_OBJECT_RECEIPT"]
    assert result["production_verified"] is False
    assert result["independent_qa_verified"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None, {}, []])
def test_malformed_llm_substitution_attestation_never_structurally_passes(value):
    evidence = deepcopy(_synthetic_golden_evidence())
    if value is None:
        evidence.pop("llm_substitution", None)
    else:
        evidence["llm_substitution"] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert "FAIL_GOVERNANCE:UNFITTED_PROBABILITY_SUBSTITUTION" in result["blockers"]


@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, []])
def test_empty_slate_claim_requires_real_boolean(value):
    evidence = deepcopy(_synthetic_golden_evidence())
    if value is None:
        evidence.pop("empty_slate_proven", None)
    else:
        evidence["empty_slate_proven"] = value
    result = evaluate_golden_user_journey(evidence)
    assert result["status"] not in ("PASS", "STRUCTURAL_PASS_ONLY")
    assert "FAIL_PROP_ACQUISITION:EMPTY_SLATE_ATTESTATION_INVALID" in result["blockers"]
