"""AMRT decisions must enter the existing append-only receipt ledger truthfully."""
import pytest

from v17.amrt_operational_lanes import RoleReceipt
from v17.amrt_receipt_bridge import handoff_to_existing_receipt, record_handoff


def role(state="HELD", reasons=("STAGE_PROOF_UNPROVEN",), name="MODEL_RECOVERY_ENGINEER"):
    return RoleReceipt(
        role=name, state=state, incident_id="1841",
        next_owner="MODEL_RECOVERY_ENGINEER",
        reasons=reasons, evidence_refs=("github:1841",),
    )


def make(handoff):
    return handoff_to_existing_receipt(
        handoff, worker_run_id="ci:verified-run", priority="P0", change_class="B",
        head_sha="a" * 40,
    )


def test_blocked_handoff_has_owner_and_reentry_and_is_not_approval():
    row = make(role())
    assert row["disposition"] == "BLOCKED_WITH_EXACT_REASON"
    assert row["typed_blocker"] == "STAGE_PROOF_UNPROVEN"
    assert row["blocker_owner"] == "MODEL_RECOVERY_ENGINEER"
    assert row["revisit_trigger"] == "MODEL_OWNER_EVIDENCE_AVAILABLE"
    assert row.get("can_execute") is not True  # database enforces false
    assert row["role"] == "conductor"
    assert "qa_decision" not in row and "release_decision" not in row
    assert row["tests"][0]["evidence_refs"] == ["github:1841"]


@pytest.mark.parametrize("agent", [
    "BLOCKER_SENTINEL", "ROOT_CAUSE_INVESTIGATOR",
    "MODEL_RECOVERY_ENGINEER", "DATA_INTEGRATION_ENGINEER",
    "CODE_CI_ENGINEER", "INDEPENDENT_QA", "RELEASE_VERIFICATION",
])
def test_every_role_uses_existing_ledger(agent):
    row = make(role(name=agent))
    assert row["tests"][0]["amrt_role"] == agent
    assert row["role"] == "conductor"


@pytest.mark.parametrize("state", ["READY", "ROUTED", "PR_REVIEW"])
def test_routing_never_claims_implementation_or_closure(state):
    row = make(role(state=state, reasons=()))
    assert row["disposition"] == "IN_PROGRESS"
    assert row.get("qa_decision") is None


def test_worker_cannot_self_approve_or_close():
    with pytest.raises(ValueError, match="SELF_CLOSURE"):
        make(role(state="FIXED_AND_VERIFIED"))
    with pytest.raises(ValueError, match="GOVERNANCE"):
        make(RoleReceipt(role="MODEL_RECOVERY_ENGINEER", state="HELD",
                         incident_id="1841", next_owner="ENGINEERING",
                         can_execute=True))


def test_record_handoff_uses_canonical_verified_persistence(monkeypatch):
    calls = []
    def fake_record(url, credential, row):
        calls.append((url, credential, row))
        return {"receipt_id": "verified-from-canonical-ledger", **row}
    monkeypatch.setattr("v17.amrt_receipt_bridge.record_receipt", fake_record)
    result = record_handoff(
        "https://example.invalid", "fake-service-role-for-unit-test", role(),
        worker_run_id="test-run", priority="P0", change_class="B")
    assert result["receipt_id"] == "verified-from-canonical-ledger"
    assert calls[0][2]["disposition"] == "BLOCKED_WITH_EXACT_REASON"
