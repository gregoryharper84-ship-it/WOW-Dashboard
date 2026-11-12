"""AMRT decisions: fail-closed proof, independent actors, leases and provider fallback."""
from datetime import datetime, timedelta, timezone

import pytest

from v17.amrt_closure_gate import (
    TEAM, closure_gate, lease_reentry, provider_attempt, specialist_for,
)

A = "a" * 40
B = "b" * 40


def valid_receipt():
    return {
        "incident_id": "1841",
        "implementation_actor": "wow-engineering[bot]",
        "review_actor": "independent-model-review",
        "qa_actor": "independent-qa",
        "change_class": "B",
        "root_cause_evidence": "issue:1841/reproduction",
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "focused_tests_pass": True,
        "regression_tests_pass": True,
        "ci_green": True,
        "security_authorized": True,
        "independent_review_approved": True,
        "independent_qa_approved": True,
        "release_authorized": True,
        "production_db_identity_verified": True,
        "action_acceptance_pass": True,
        "persistence_readback_pass": True,
        "reconciliation_pass": True,
        "terminal_states_concordant": True,
        "reviewed_head_sha": A,
        "ci_sha": A,
        "review_sha": A,
        "authorized_merge_sha": B,
        "deployed_sha": B,
        "action_sha": B,
        "action_run_id": "live-run-1",
        "persistence_run_id": "live-run-1",
        "reconciliation_run_id": "live-run-1",
    }


def test_seven_roles_defined_without_extra_authority():
    assert len(TEAM) == 7
    assert len(set(TEAM)) == 7


@pytest.mark.parametrize("failure,owner", [
    ("EVENT_IDENTITY_COLLISION", "DATA_INTEGRATION_ENGINEER"),
    ("STAGE_PROOF_NOT_EMITTED", "MODEL_RECOVERY_ENGINEER"),
    ("CI_TEST_FAILED", "CODE_CI_ENGINEER"),
    ("DEPLOY_SHA_MISMATCH", "RELEASE_VERIFICATION"),
    ("UNKNOWN_ERROR", "ROOT_CAUSE_INVESTIGATOR"),
])
def test_routes_specialist(failure, owner):
    assert specialist_for(failure) == owner


def test_exact_production_proof_can_close():
    result = closure_gate(valid_receipt())
    assert result.status == "FIXED_AND_VERIFIED"
    assert not result.blockers


@pytest.mark.parametrize("key", [
    "focused_tests_pass", "regression_tests_pass", "independent_review_approved",
    "independent_qa_approved", "release_authorized",
    "production_db_identity_verified", "action_acceptance_pass",
    "persistence_readback_pass", "reconciliation_pass", "terminal_states_concordant",
])
def test_missing_proof_never_closes(key):
    receipt = valid_receipt()
    receipt[key] = None
    result = closure_gate(receipt)
    assert result.status == "BLOCKED_WITH_EXACT_REASON"
    assert key.upper() in result.blockers[0]


def test_live_sha_drift_fails_closed():
    receipt = valid_receipt()
    receipt["deployed_sha"] = A
    assert "EXACT_SHA_MISMATCH" in closure_gate(receipt).blockers


def test_unrelated_action_run_does_not_close():
    receipt = valid_receipt()
    receipt["action_run_id"] = "other-run"
    assert "PRODUCTION_RUN_RECONCILIATION_UNPROVEN" in closure_gate(receipt).blockers


def test_implementer_cannot_self_review_or_qa():
    receipt = valid_receipt()
    receipt["qa_actor"] = receipt["implementation_actor"]
    assert "INDEPENDENT_ACTOR_SEPARATION_UNPROVEN" in closure_gate(receipt).blockers


def test_missing_validation_db_identity_blocks_production_claim():
    receipt = valid_receipt()
    receipt["production_db_identity_verified"] = False
    assert closure_gate(receipt).status != "FIXED_AND_VERIFIED"


def test_class_c_requires_separate_governance():
    receipt = valid_receipt()
    receipt["change_class"] = "C"
    assert "CLASS_C_CERTIFICATION_MISSING" in closure_gate(receipt).blockers
    receipt["model_owner_certified"] = True
    receipt["class_c_governance_approved"] = True
    assert closure_gate(receipt).status == "FIXED_AND_VERIFIED"


def test_unsafe_execution_flag_blocks_closure():
    receipt = valid_receipt()
    receipt["can_execute"] = True
    assert "V17_GOVERNANCE_INVALID" in closure_gate(receipt).blockers


def test_expired_lease_never_claims_owner():
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    case = {"incident_id": "1841", "owner_id": "engineering",
            "lease_epoch": 12, "lease_expires_at": (now - timedelta(minutes=1)).isoformat()}
    result = lease_reentry(case, now=now)
    assert result.status == "REENTRY_REQUIRED"
    assert result.next_owner == "ECOSYSTEM_CONDUCTOR"


def test_active_lease_keeps_owner():
    now = datetime(2026, 10, 18, tzinfo=timezone.utc)
    case = {"incident_id": "1841", "owner_id": "engineering",
            "lease_epoch": 12, "lease_expires_at": (now + timedelta(minutes=1)).isoformat()}
    assert lease_reentry(case, now=now).next_owner == "engineering"


def test_bad_lease_fails_closed():
    now = datetime.now(timezone.utc)
    assert lease_reentry({"incident_id": "1841", "owner_id": "eng", "lease_epoch": 1},
                         now=now).status == "BLOCKED"


def test_bounded_retry_and_provider_fallback():
    assert provider_attempt(1, True, True).status == "FALLBACK_CANDIDATE"
    assert provider_attempt(1, True, False).status == "BLOCKED"
    assert provider_attempt(3, False, True).status == "BLOCKED"
