import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / ".github" / "scripts" / "verify_engineering_governance_receipt.py"
WORKER = ROOT / ".github" / "workflows" / "wow-v17-chatgpt-engineering-worker.yml"
CLAUDE_WORKER = ROOT / ".github" / "workflows" / "wow-v17-claude-engineering-worker.yml"
GATE = ROOT / ".github" / "workflows" / "wow-v17-trusted-governance-gate.yml"
MORNING_GREEN = ROOT / ".github" / "workflows" / "wow-v17-morning-green-continuation.yml"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_engineering_governance_receipt", VERIFIER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _receipt(
    workflow_name: str = "wow-v17-chatgpt-engineering-worker",
    branch: str = "chatgpt/engineering/12345-1",
):
    return {
        "receipt_schema_version": 1,
        "workflow_name": workflow_name,
        "repository": "owner/repo",
        "workflow_run_id": "12345",
        "implementation": {
            "changed": True,
            "branch": branch,
            "head_sha": "abc123",
            "incident_id": "1247",
            "risk_class": "R1",
        },
        "handoff": {
            "lead_dispatch": {"decision": "REPAIR"},
            "triage": {"repairable": True, "incident_id": "1247"},
            "specialist": {"name": "REPOSITORY_GOVERNANCE_AGENT"},
            "engineering": {"changed": True, "incident_id": "1247"},
            "independent_review": {"decision": "PASS"},
            "system_architect": {"decision": "NOT_APPLICABLE"},
            "deterministic_regression": {"status": "0"},
            "qa": {"decision": "PASS"},
        },
        "governance_complete": True,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


@pytest.mark.parametrize(
    ("workflow_name", "branch"),
    [
        ("wow-v17-chatgpt-engineering-worker", "chatgpt/engineering/12345-1"),
        ("wow-v17-claude-engineering-worker", "claude/engineering/12345-1"),
    ],
)
def test_exact_head_receipt_passes_for_each_governed_provider(workflow_name, branch):
    verifier = _load_verifier()
    verifier.verify_receipt(
        _receipt(workflow_name=workflow_name, branch=branch),
        expected_repository="owner/repo",
        expected_head_sha="abc123",
        expected_head_ref=branch,
        expected_workflow_run_id="12345",
    )


def test_untrusted_workflow_identity_fails_closed():
    verifier = _load_verifier()
    receipt = _receipt(workflow_name="forged-engineering-worker")
    with pytest.raises(verifier.GovernanceReceiptError, match="receipt workflow identity mismatch"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("head_sha", "stale-sha", "receipt head SHA mismatch"),
        ("branch", "forged/branch", "receipt branch mismatch"),
    ],
)
def test_stale_or_forged_implementation_identity_fails(field, value, message):
    verifier = _load_verifier()
    receipt = _receipt()
    receipt["implementation"][field] = value
    with pytest.raises(verifier.GovernanceReceiptError, match=message):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )


def test_missing_independent_review_or_qa_fails_closed():
    verifier = _load_verifier()
    receipt = _receipt()
    receipt["handoff"]["independent_review"]["decision"] = "REJECT"
    with pytest.raises(verifier.GovernanceReceiptError, match="independent review did not pass"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )

    receipt = _receipt()
    receipt["handoff"]["qa"]["decision"] = "NOT_RUN"
    with pytest.raises(verifier.GovernanceReceiptError, match="QA verification did not pass"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="chatgpt/engineering/12345-1",
            expected_workflow_run_id="12345",
        )


def test_workflows_use_trusted_exact_head_artifact_not_pr_body_as_authority():
    worker = WORKER.read_text(encoding="utf-8")
    claude_worker = CLAUDE_WORKER.read_text(encoding="utf-8")
    gate = GATE.read_text(encoding="utf-8")
    morning_green = MORNING_GREEN.read_text(encoding="utf-8")

    for governed_worker in (worker, claude_worker):
        assert "wow-v17-engineering-governance-${{ steps.impl.outputs.head_sha }}" in governed_worker
        assert "independent_review: {decision: $review}" in governed_worker
        assert "qa: {decision: $qa}" in governed_worker
        assert 'echo "head_sha=$head_sha" >> "$GITHUB_OUTPUT"' in governed_worker

    assert '--arg workflow_name "wow-v17-chatgpt-engineering-worker"' in worker
    assert '--arg workflow_name "wow-v17-claude-engineering-worker"' in claude_worker

    assert "pull_request_target:" in gate
    assert "ref: main" in gate
    assert 'artifact_name="wow-v17-engineering-governance-${PR_HEAD_SHA}"' in gate
    assert "verify_engineering_governance_receipt.py" in gate
    assert "GOVERNANCE_RECEIPT_CHECKSUM_MISMATCH" in gate
    assert '[ "$head_branch" = "main" ]' in gate
    assert '[ "$head_repo" = "$GITHUB_REPOSITORY" ]' in gate
    assert "wow-v17-chatgpt-engineering-worker.yml" in gate
    assert "wow-v17-claude-engineering-worker.yml" in gate
    assert '[ "$event" = "workflow_dispatch" ]' in gate

    assert "Verify trusted exact-head governance receipt" in morning_green
    assert '[ "$head_branch" != "main" ]' in morning_green
    assert '[ "$head_repo" != "$GITHUB_REPOSITORY" ]' in morning_green
    assert "wow-v17-chatgpt-engineering-worker.yml" in morning_green
    assert "wow-v17-claude-engineering-worker.yml" in morning_green
    assert '[ "$event" = "workflow_dispatch" ] && trusted_worker=true' in morning_green
    assert "steps.governance.outputs.approved == 'true'" in morning_green
    assert "GOVERNANCE_REWORK" in morning_green
