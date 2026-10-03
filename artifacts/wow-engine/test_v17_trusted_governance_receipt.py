import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = ROOT / ".github" / "scripts" / "verify_engineering_governance_receipt.py"
WORKER = ROOT / ".github" / "workflows" / "wow-v17-chatgpt-engineering-worker.yml"
CLAUDE_WORKER = ROOT / ".github" / "workflows" / "wow-v17-claude-engineering-worker.yml"
GATE = ROOT / ".github" / "workflows" / "wow-v17-trusted-governance-gate.yml"
MORNING_GREEN = ROOT / ".github" / "workflows" / "wow-v17-morning-green-continuation.yml"
RELEASE = ROOT / ".github" / "workflows" / "wow-v17-release-production-verification-agent.yml"
EXISTING_PR_REVIEW = ROOT / ".github" / "workflows" / "wow-v17-existing-pr-governance-review.yml"
EXISTING_PR_VERIFIER = ROOT / ".github" / "scripts" / "verify_existing_pr_governance_receipt.py"


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
        expected_workflow_name=workflow_name,
    )


def test_receipt_workflow_name_must_match_producing_workflow():
    verifier = _load_verifier()
    receipt = _receipt(workflow_name="wow-v17-claude-engineering-worker", branch="claude/engineering/12345-1")
    with pytest.raises(verifier.GovernanceReceiptError, match="receipt workflow name does not match producing workflow"):
        verifier.verify_receipt(
            receipt,
            expected_repository="owner/repo",
            expected_head_sha="abc123",
            expected_head_ref="claude/engineering/12345-1",
            expected_workflow_run_id="12345",
            expected_workflow_name="wow-v17-chatgpt-engineering-worker",
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
    assert 'id: workflow' in gate
    assert 'echo "name=$name" >> "$GITHUB_OUTPUT"' in gate
    assert '--workflow-name "$WORKFLOW_NAME"' in gate
    identity_step = gate.split("- name: Verify trusted workflow identity and completion", 1)[1].split(
        "- name: Download and verify exact-head receipt", 1
    )[0]
    receipt_step = gate.split("- name: Download and verify exact-head receipt", 1)[1]
    assert "WORKFLOW_NAME:" not in identity_step
    assert "WORKFLOW_NAME: ${{ steps.workflow.outputs.name }}" in receipt_step

    assert "Verify trusted exact-head governance receipt" in morning_green
    assert 'protected_main_source=false' in morning_green
    assert '[ "$head_repo" != "$GITHUB_REPOSITORY" ]' in morning_green
    assert "wow-v17-chatgpt-engineering-worker.yml" in morning_green
    assert "wow-v17-claude-engineering-worker.yml" in morning_green
    assert "wow-v17-existing-pr-governance-review.yml" in morning_green
    assert "pull_request_target" in morning_green
    assert "verify_existing_pr_governance_receipt.py" in morning_green
    assert '--workflow-name "$name"' in morning_green
    assert "steps.governance.outputs.approved == 'true'" in morning_green
    assert "GOVERNANCE_REWORK" in morning_green


def test_release_verification_requires_trusted_exact_head_governance_and_exact_merge_sha():
    text = RELEASE.read_text(encoding="utf-8")
    assert "startsWith(github.event.pull_request.head.ref" not in text
    assert "Resolve exact merged identity" in text
    assert 'git merge-base --is-ancestor "$merge_sha" HEAD' in text
    assert "Verify required exact-head CI" in text
    assert '"wow-verify|.github/workflows/wow-verify.yml"' in text
    assert '"wow-engine-verify|.github/workflows/wow-engine-verify.yml"' in text
    assert "Trusted exact-head engineering governance" in text
    assert "/actions/runs?head_sha=${HEAD_SHA}&per_page=100" in text
    assert "Resolve trusted exact-head governance artifact" in text
    assert 'artifact_name="wow-v17-engineering-governance-${HEAD_SHA}"' in text
    assert "Verify governance-producing workflow provenance" in text
    assert 'protected_main_source=false' in text
    assert '[ "$head_repo" = "$GITHUB_REPOSITORY" ]' in text
    assert "wow-v17-existing-pr-governance-review.yml" in text
    assert "pull_request_target" in text
    assert "Download and verify trusted governance receipt" in text
    assert "RELEASE_GOVERNANCE_RECEIPT_CHECKSUM_MISMATCH" in text
    assert "verify_engineering_governance_receipt.py" in text
    assert "verify_existing_pr_governance_receipt.py" in text
    assert '--head-sha "$HEAD_SHA"' in text
    assert '--head-ref "$HEAD_REF"' in text
    assert '--workflow-run-id "$RUN_ID"' in text
    assert '--workflow-name "$WORKFLOW_NAME"' in text
    assert "trusted_handoff_receipt: VERIFIED" in text
