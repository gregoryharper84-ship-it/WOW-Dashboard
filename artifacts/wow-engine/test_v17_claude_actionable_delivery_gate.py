"""Source-level regression for fail-closed Claude engineering repair delivery.

This test verifies the protected worker's guard without invoking providers,
altering source governance, or making a real PR.
"""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/wow-v17-claude-engineering-worker.yml"


def test_actionable_repair_must_have_branch_and_pr():
    source = WORKFLOW.read_text()
    gate = source.split("      - name: Enforce actionable repair delivery\n", 1)[1].split(
        "      - name: Record team receipt\n", 1
    )[0]
    assert "if: always() && steps.lead.outputs.action == 'REPAIR'" in gate
    assert 'if [ "$CHANGED" != "true" ] || [ -z "$BRANCH" ]; then' in gate
    assert "ACTIONABLE_REPAIR_NO_DELIVERABLE" in gate
    assert 'gh pr list --repo "$GITHUB_REPOSITORY" --state open --base main --head "$BRANCH"' in gate
    assert 'if [ -z "$pr" ]; then' in gate
    assert "ACTIONABLE_REPAIR_PR_MISSING" in gate
    assert "exit 1" in gate
    assert 'steps.lead.outputs.action == \'NO_ACTION\'' not in gate
    assert 'steps.lead.outputs.action == \'VERIFY_RELEASE\'' not in gate


def test_delivery_check_runs_after_pr_attempt():
    source = WORKFLOW.read_text()
    assert source.index("      - name: Open governed engineering PR") < source.index(
        "      - name: Enforce actionable repair delivery"
    )
