from datetime import datetime, timezone
import hashlib
import json

import pytest
from pydantic import ValidationError

from v17.receipt_schema import (
    CONTRACT_VERSION,
    VerificationReceipt,
    expected_sentinel_signature,
    schema_hash,
    validate_terminal_receipt,
)


PR_HEAD = "a" * 40
MERGE = "b" * 40
DIGEST = hashlib.sha256(b"response").hexdigest()
TRACE = hashlib.sha256(b"trace").hexdigest()


def _receipt(**overrides) -> VerificationReceipt:
    payload = {
        "contract_version": CONTRACT_VERSION,
        "issue_id": 1127,
        "pr_number": 1437,
        "exact_head_sha": PR_HEAD,
        "merge_sha": MERGE,
        "deployed_render_sha": MERGE,
        "method": "GET",
        "route_tested": "/health/live",
        "schema_hash": schema_hash(),
        "http_status": 200,
        "dry_run_header_present": True,
        "can_execute_header_false": True,
        "raw_response_digest": DIGEST,
        "execution_trace_digest": TRACE,
        "timestamp_utc": datetime.now(timezone.utc),
        "sentinel_workflow": "wow-v17-release-production-verification-agent",
        "sentinel_workflow_run_id": 123,
        "sentinel_signature": "sha256:" + ("0" * 64),
        "audit_artifact_name": "wow-v17-verification-receipt-" + MERGE,
    }
    payload.update(overrides)
    unsigned = VerificationReceipt.model_validate(payload)
    if "sentinel_signature" not in overrides:
        payload["sentinel_signature"] = expected_sentinel_signature(unsigned)
    return VerificationReceipt.model_validate(payload)


def _errors(receipt: VerificationReceipt):
    return validate_terminal_receipt(
        receipt,
        expected_issue_id=1127,
        expected_pr_number=1437,
        expected_pr_head_sha=PR_HEAD,
        expected_merge_sha=MERGE,
    )


def test_valid_machine_receipt_passes():
    assert _errors(_receipt()) == []


def test_nonexistent_or_unmounted_route_is_blocked_by_allowlist():
    receipt = _receipt(route_tested="/definitely-not-a-real-production-route")
    assert "ACCEPTANCE_ROUTE_NOT_ALLOWLISTED" in _errors(receipt)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("dry_run_header_present", False, "DRY_RUN_HEADER_MISSING"),
        ("can_execute_header_false", False, "CAN_EXECUTE_FALSE_HEADER_MISSING"),
    ],
)
def test_safety_invariant_cheater_receipts_are_blocked(field, value, expected):
    receipt = _receipt(**{field: value})
    assert expected in _errors(receipt)


def test_deployed_sha_one_commit_different_is_blocked():
    receipt = _receipt(deployed_render_sha="c" * 40)
    assert "DEPLOYED_SHA_NOT_EXACT_MERGE" in _errors(receipt)


def test_schema_hash_tampering_is_blocked():
    receipt = _receipt(schema_hash="0" * 64)
    assert "RECEIPT_SCHEMA_HASH_MISMATCH" in _errors(receipt)


def test_signature_tampering_is_blocked():
    receipt = _receipt(sentinel_signature="sha256:" + ("f" * 64))
    assert "SENTINEL_SIGNATURE_MISMATCH" in _errors(receipt)


def test_prose_only_evidence_cannot_parse_as_receipt():
    with pytest.raises((ValidationError, json.JSONDecodeError)):
        VerificationReceipt.model_validate_json("I ran the test and it passed")
