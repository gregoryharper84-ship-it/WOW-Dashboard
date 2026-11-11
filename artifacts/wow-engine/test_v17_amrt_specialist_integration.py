"""AMRT five-agent adapter regression: extends existing roles without new authority.

No network, writer lease acquisition, scoring, approvals, or deployments.
"""
from datetime import datetime, timedelta, timezone

import pytest

from v17.amrt_operational_lanes import evaluate_amrt_specialist
from v17.amrt_specialist_agents import STAGES, AGENT_TO_EXISTING_ROLE


NOW = datetime(2026, 10, 26, 19, 30, tzinfo=timezone.utc)


def incident(**overrides):
    data = {
        "incident_id": "1841",
        "issue_ref": "github:issue/1841",
        "owner_id": "existing-authenticated-worker",
        "lease_owner": "existing-authenticated-worker",
        "lease_epoch": 8,
        "lease_expires_at": (NOW + timedelta(hours=1)).isoformat(),
        "lease_authority": "EXISTING_AUTHENTICATED_ENGINEERING_WORKER",
        "amrt_owned": True,
        "can_execute": False,
        "sport": "NFL",
        "market": "ML",
        "event_id": "canonical-nfl-1",
        "model_id": "governed-nfl-ml",
        "reproduction_ref": "receipt:repro",
        "root_cause_ref": "receipt:root-cause",
        "acceptance_ref": "receipt:acceptance",
        "change_class": "B",
        "changed_contracts": ("publication_adapter",),
        "source_pr_open": False,
        "last_progress_at": (NOW - timedelta(minutes=4)).isoformat(),
        "provider_attempts": 0,
        "authorized_fallback": False,
        "head_sha": "a" * 40,
    }
    data.update(overrides)
    return data


def producer_stages(**overrides):
    records = {s: {"status": "PASS", "evidence_ref": "receipt:" + s} for s in STAGES}
    for key, value in overrides.items():
        records[key]["status"] = value
    return records


@pytest.mark.parametrize("specialist", sorted(AGENT_TO_EXISTING_ROLE))
def test_each_specialist_maps_to_existing_role_without_new_authority(specialist):
    outcome = evaluate_amrt_specialist(
        incident(), specialist=specialist, producer_stages=producer_stages(), now=NOW,
    )
    assert outcome.role == AGENT_TO_EXISTING_ROLE[specialist]
    assert outcome.incident_id == "1841"
    assert outcome.can_execute is False
    assert outcome.terminal_authority == "V17_TERMINAL_REDUCER"
    assert outcome.state != "FIXED_AND_VERIFIED"
    assert any("specialist:" + specialist == ref for ref in outcome.evidence_refs)


def test_unadmitted_incident_never_reaches_specialist():
    result = evaluate_amrt_specialist(
        incident(amrt_owned=False), specialist="MODEL_RECOVERY_COMMANDER",
    )
    assert result.state == "HELD"
    assert "AMRT_INCIDENT_NOT_ADMITTED" in result.reasons


def test_wager_execution_flag_cannot_be_overridden():
    result = evaluate_amrt_specialist(
        incident(can_execute=True), specialist="MODEL_RESTORATION_ENGINEER",
    )
    assert result.state == "HELD"
    assert "AMRT_EXECUTION_INVARIANT_INVALID" in result.reasons


def test_missing_dependency_evidence_fails_closed():
    result = evaluate_amrt_specialist(
        incident(), specialist="MODEL_DEPENDENCY_INVESTIGATOR",
    )
    assert result.state == "HELD"
    assert "PRODUCER_STAGE_EVIDENCE_MISSING" in result.reasons


def test_first_missing_stage_does_not_turn_into_model_unavailable():
    result = evaluate_amrt_specialist(
        incident(), specialist="MODEL_DEPENDENCY_INVESTIGATOR",
        producer_stages=producer_stages(canonical_identity="FAIL"),
    )
    assert result.state == "ROUTED"
    assert result.next_owner == "DATA_INTEGRATION_ENGINEER"
    assert "FIRST_UNPROVEN_DEPENDENCY:canonical_identity:FAIL" in result.reasons


def test_class_c_restoration_requires_model_excellence_not_auto_promotion():
    result = evaluate_amrt_specialist(
        incident(change_class="C"), specialist="MODEL_RESTORATION_ENGINEER",
    )
    assert result.state == "HELD"
    assert result.next_owner == "MODEL_EXCELLENCE"


def test_deadlock_requires_real_timestamp():
    result = evaluate_amrt_specialist(
        incident(), specialist="RECOVERY_ESCALATION_DEADLOCK_BREAKER",
    )
    assert result.state == "HELD"
    assert "TRUSTED_EVALUATION_TIME_MISSING" in result.reasons


def test_expired_writer_cannot_be_stolen_by_deadlock_agent():
    result = evaluate_amrt_specialist(
        incident(lease_expires_at=(NOW - timedelta(seconds=1)).isoformat()),
        specialist="RECOVERY_ESCALATION_DEADLOCK_BREAKER", now=NOW,
    )
    assert result.state == "REENTRY_REQUIRED"
    assert result.next_owner == "ECOSYSTEM_CONDUCTOR"


def test_deadlock_cannot_recommend_a_second_lease_writer():
    result = evaluate_amrt_specialist(
        incident(lease_owner="competing-writer"),
        specialist="RECOVERY_ESCALATION_DEADLOCK_BREAKER", now=NOW,
    )
    assert result.state == "HELD"
    assert result.next_owner == "ECOSYSTEM_CONDUCTOR"
    assert "LEASE_OWNER_IDENTITY_UNVERIFIED" in result.reasons


def test_deadlock_requires_positive_lease_owner_identity():
    result = evaluate_amrt_specialist(
        incident(lease_owner=None),
        specialist="RECOVERY_ESCALATION_DEADLOCK_BREAKER", now=NOW,
    )
    assert result.state == "HELD"
    assert "LEASE_OWNER_IDENTITY_UNVERIFIED" in result.reasons


def test_unknown_specialist_cannot_claim_approval():
    result = evaluate_amrt_specialist(
        incident(), specialist="INDEPENDENT_QA", now=NOW,
    )
    assert result.state == "HELD"
    assert "UNKNOWN_AMRT_SPECIALIST" in result.reasons


def test_regression_plan_does_not_self_certify_qa():
    result = evaluate_amrt_specialist(
        incident(), specialist="MODEL_RECOVERY_REGRESSION_SPECIALIST",
    )
    assert result.state == "ROUTED"
    assert result.role == "CODE_CI_ENGINEER"
    assert "TEST_MATRIX_NOT_YET_EXECUTED" in result.reasons
