"""Cross-layer QA identity regression run by the existing engineering CI matrix.

This is a read-only contract test. It does not publish a check, admit a writer,
approve a PR or alter V17 terminal/release control.
"""
from pathlib import Path

import pytest

from v17 import engineering_recovery_planner as planner
from v17 import independent_release_checks as release

ROOT = Path(__file__).resolve().parents[2]
QA_WORKFLOW = ROOT / ".github/workflows/wow-v17-independent-qa-check.yml"
QA_SOURCE = ROOT / ".github/scripts/independent_source_review_receipt.py"
QA_CERTIFIER = ROOT / ".github/workflows/wow-v17-existing-pr-governance-review.yml"


@pytest.mark.parametrize("display_title", [
    "wow-v17-independent-qa-check",
    "wow-v17-independent-qa-check event=workflow_run pr=AUTO",
    "visible title can change without changing signed workflow identity",
])
def test_qa_identity_is_stable_across_display_names(display_title):
    identity = release._source_review_module
    assert identity._is_qa_workflow_run({
        "name": display_title,
        "workflow_id": identity.WORKFLOW_ID,
        "path": identity.WORKFLOW_PATH,
    })


@pytest.mark.parametrize("workflow_id,path", [
    (None, ".github/workflows/wow-v17-independent-qa-check.yml"),
    (True, ".github/workflows/wow-v17-independent-qa-check.yml"),
    (1, ".github/workflows/wow-v17-independent-qa-check.yml"),
    (380562324, ".github/workflows/untrusted.yml"),
])
def test_qa_unknown_or_foreign_identity_fails_closed(workflow_id, path):
    identity = release._source_review_module
    assert not identity._is_qa_workflow_run({
        "name": identity.WORKFLOW_NAME,
        "workflow_id": workflow_id,
        "path": path,
    })


def test_qa_producers_and_verifiers_share_immutable_identity_contract():
    text = QA_WORKFLOW.read_text(encoding="utf-8")
    verifier = QA_SOURCE.read_text(encoding="utf-8")
    assert text.count("--argjson workflow_id 380562324") == 2
    assert text.count("workflow_id:$workflow_id") == 2
    assert verifier.count("_is_qa_workflow_run(run)") >= 4
    assert 'run.get("name") == WORKFLOW_NAME' not in verifier


def test_unrelated_check_completions_do_not_consume_certifier_runner():
    workflow = QA_CERTIFIER.read_text(encoding="utf-8")
    assert "github.event.check_run.app.id == 5245853" in workflow
    assert "github.event.check_run.name == 'WOW Release Authority exact-head'" in workflow
    assert "github.event.check_run.conclusion == 'success'" in workflow
    # The runner still re-reads the real App check, PR association and SHA.
    assert 'live_check=$(gh api "/repos/${GITHUB_REPOSITORY}/check-runs/${CHECK_RUN_ID}")' in workflow
    assert "RELEASE_CHECK_PR_TARGET_NOT_UNIQUE" in workflow


def test_independent_ownership_and_actual_codeowner_approval_have_distinct_reentry():
    original = planner.plan_recovery(
        pr_number=2125, findings=["QA_TRUST_ROOT_OWNER_AUTHORED"],
        attempts=2, is_wow_engineering_pr=True,
    )
    approval = planner.plan_recovery(
        pr_number=2125, findings=["QA_TRUST_ROOT_OWNER_APPROVAL_MISSING"],
        attempts=2, is_wow_engineering_pr=True,
    )
    assert original["required_actor"] == "INDEPENDENT_ENGINEERING_AUTHOR"
    assert approval["required_actor"] == "ACTUAL_CODEOWNER"
    assert original["attempts"] == approval["attempts"] == 2
    assert original["can_execute"] is approval["can_execute"] is False
