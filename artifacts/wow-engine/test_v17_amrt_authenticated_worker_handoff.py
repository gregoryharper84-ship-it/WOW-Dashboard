"""Five-specialist invocation handoff tests. No network, secrets or live worker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from v17.amrt_authenticated_worker_handoff import persist_authenticated_specialist_handoff
from v17.amrt_specialist_agents import AGENT_TO_EXISTING_ROLE, STAGES
from v17.persistent_worker_safety import Lease

NOW = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)
WORKER = "github-actions:987654321:1"
SHA = "c" * 40
AGENTS = tuple(AGENT_TO_EXISTING_ROLE)


def incident(**overrides):
    row = {
        "incident_id": "1841",
        "issue_ref": "github:issue/1841",
        "amrt_owned": True,
        "can_execute": False,
        "owner_id": WORKER,
        "lease_owner": WORKER,
        "lease_authority": "EXISTING_AUTHENTICATED_ENGINEERING_WORKER",
        "severity": "P0",
        "change_class": "B",
        "stage": "INVESTIGATION",
        "typed_failure": "STAGE_PROOF_UNPROVEN",
        "reproduction_ref": "receipt:reproduction",
        "root_cause_ref": "receipt:root_cause",
        "acceptance_ref": "receipt:acceptance",
        "sport": "NFL",
        "market": "ML",
        "event_id": "nfl-event-1",
        "model_id": "NFL_GAME_WIN_V17",
        "changed_contracts": ["publication_adapter"],
        "head_sha": SHA,
        "last_progress_at": (NOW - timedelta(minutes=4)).isoformat(),
        "lease_epoch": 5,
        "lease_expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    row.update(overrides)
    return row


def args(**overrides):
    base = {
        "incident": incident(),
        "specialist": "MODEL_RECOVERY_COMMANDER",
        "lease": Lease("1841", WORKER, 5, (NOW + timedelta(hours=1)).isoformat()),
        "verified_latest_epoch": 5,
        "verified_worker_id": WORKER,
        "observed_at": NOW,
        "head_sha": SHA,
        "supabase_url": "https://example.invalid",
        "service_credential": "unit-only-never-a-real-key",
    }
    base.update(overrides)
    return base


@pytest.fixture
def receipt_writer(monkeypatch):
    calls = []

    def fake_verified_append(url, credential, row):
        assert url == "https://example.invalid"
        assert credential == "unit-only-never-a-real-key"
        calls.append(row)
        return {"receipt_id": "verified-test-receipt", **row}

    monkeypatch.setattr("v17.amrt_receipt_bridge.record_receipt", fake_verified_append)
    return calls


@pytest.mark.parametrize("specialist", AGENTS)
def test_all_five_existing_specialists_reach_canonical_append_only_writer(
    specialist, receipt_writer
):
    stages = {
        name: {"status": "PASS", "evidence_ref": "receipt:" + name}
        for name in STAGES
    }
    got = persist_authenticated_specialist_handoff(
        **args(specialist=specialist, producer_stages=stages)
    )
    assert got["receipt_id"] == "verified-test-receipt"
    assert len(receipt_writer) == 1
    row = receipt_writer[0]
    assert row["role"] == "conductor"
    assert row["worker_run_id"] == WORKER
    assert row["head_sha"] == SHA
    assert row["lease_epoch"] == 5
    assert row["disposition"] in {"IN_PROGRESS", "BLOCKED_WITH_EXACT_REASON"}
    assert row["tests"][0]["amrt_role"] == AGENT_TO_EXISTING_ROLE[specialist]
    assert "specialist:" + specialist in row["tests"][0]["evidence_refs"]
    assert row.get("qa_decision") is None
    assert row.get("release_decision") is None
    assert row["disposition"] != "FIXED_AND_VERIFIED"


@pytest.mark.parametrize("mutated,error", [
    ({"verified_latest_epoch": 4}, "AMRT_STALE_OR_EXPIRED_LEASE"),
    ({"verified_worker_id": "github-actions:777:1"}, "AMRT_CANONICAL_OWNER_OR_WRITER_MISMATCH"),
    ({"lease": Lease("1950", WORKER, 5, (NOW + timedelta(hours=1)).isoformat())},
     "AMRT_CANONICAL_OWNER_OR_WRITER_MISMATCH"),
    ({"lease": Lease("1841", WORKER, 5, (NOW - timedelta(seconds=1)).isoformat())},
     "AMRT_STALE_OR_EXPIRED_LEASE"),
    ({"lease": Lease("1841", WORKER, 5, (NOW + timedelta(hours=1)).replace(tzinfo=None).isoformat())},
     "AMRT_LEASE_TIMESTAMP_INVALID"),
    ({"head_sha": "not-a-real-sha"}, "AMRT_EXACT_HEAD_SHA_MISSING"),
    ({"observed_at": NOW.replace(tzinfo=None)}, "AMRT_OBSERVATION_TIME_UNTRUSTED"),
    ({"specialist": "INDEPENDENT_QA"}, "AMRT_SPECIALIST_UNKNOWN"),
])
def test_untrusted_or_expired_worker_never_writes(mutated, error, receipt_writer):
    with pytest.raises(ValueError, match=error):
        persist_authenticated_specialist_handoff(**args(**mutated))
    assert receipt_writer == []


@pytest.mark.parametrize("changed,error", [
    ({"amrt_owned": False}, "AMRT_INCIDENT_NOT_ADMITTED"),
    ({"can_execute": True}, "AMRT_EXECUTION_INVARIANT_INVALID"),
    ({"owner_id": "someone-else"}, "AMRT_CANONICAL_OWNER_OR_WRITER_MISMATCH"),
    ({"lease_owner": None}, "AMRT_CANONICAL_OWNER_OR_WRITER_MISMATCH"),
    ({"lease_authority": "OWNER_ASSERTED"}, "AMRT_CANONICAL_OWNER_OR_WRITER_MISMATCH"),
    ({"incident_id": "1950"}, "AMRT_CANONICAL_OWNER_OR_WRITER_MISMATCH"),
])
def test_foreign_incident_or_owner_never_writes(changed, error, receipt_writer):
    with pytest.raises(ValueError, match=error):
        persist_authenticated_specialist_handoff(**args(incident=incident(**changed)))
    assert not receipt_writer


def test_class_c_remains_held_and_cannot_be_promoted(receipt_writer):
    result = persist_authenticated_specialist_handoff(**args(
        specialist="MODEL_RESTORATION_ENGINEER",
        incident=incident(change_class="C", changed_contracts=["fitted_probabilities"]),
    ))
    assert result["disposition"] == "BLOCKED_WITH_EXACT_REASON"
    assert result["typed_blocker"] == "CLASS_C_MODEL_CERTIFICATION_REQUIRED"
    assert result["blocker_owner"] == "MODEL_EXCELLENCE"


def test_readback_failure_is_not_reported_as_success(monkeypatch):
    def no_readback(*_args):
        raise RuntimeError("CANONICAL_LEDGER_READBACK_FAILED")

    monkeypatch.setattr("v17.amrt_receipt_bridge.record_receipt", no_readback)
    with pytest.raises(RuntimeError, match="CANONICAL_LEDGER_READBACK_FAILED"):
        persist_authenticated_specialist_handoff(**args())


def test_stale_lease_fence_from_authoritative_worker_is_not_bypassed(receipt_writer):
    with pytest.raises(ValueError, match="AMRT_STALE_OR_EXPIRED_LEASE"):
        persist_authenticated_specialist_handoff(**args(
            lease=Lease("1841", WORKER, 6, (NOW + timedelta(hours=1)).isoformat()),
        ))
    assert not receipt_writer


def test_missing_producer_stages_cannot_create_specialist_attributed_receipt(receipt_writer):
    # The existing adapter returns a generic seven-role HOLD BEFORE the
    # Dependency Investigator is called. It must not be mislabeled as a genuine
    # five-specialist execution/persisted handoff.
    with pytest.raises(ValueError, match="AMRT_SPECIALIST_RECEIPT_INVALID"):
        persist_authenticated_specialist_handoff(**args(
            specialist="MODEL_DEPENDENCY_INVESTIGATOR",
            producer_stages=None,
        ))
    assert receipt_writer == []
