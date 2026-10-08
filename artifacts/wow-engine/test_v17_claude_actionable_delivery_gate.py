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
    assert "EXPECTED_HEAD" in gate
    assert "ACTIONABLE_REPAIR_PR_HEAD_MISMATCH" in gate
    assert 'gh pr view "$pr" --repo "$GITHUB_REPOSITORY" --json headRefOid' in gate
    assert "exit 1" in gate
    assert 'steps.lead.outputs.action == \'NO_ACTION\'' not in gate
    assert 'steps.lead.outputs.action == \'VERIFY_RELEASE\'' not in gate


def test_delivery_check_runs_after_pr_attempt():
    source = WORKFLOW.read_text()
    assert source.index("      - name: Open governed engineering PR") < source.index(
        "      - name: Enforce actionable repair delivery"
    )


def _delivery_shell():
    """Extract the actual workflow Bash instead of reimplementing the condition."""
    from textwrap import dedent

    workflow = WORKFLOW.read_text()
    step = workflow.split("      - name: Enforce actionable repair delivery\n", 1)[1].split(
        "      - name: Record team receipt\n", 1
    )[0]
    script = step.split("        run: |\n", 1)[1]
    return dedent(script)


def _run_delivery(tmp_path, *, changed, branch, returned_pr, expected_head="a" * 40, remote_head="a" * 40):
    import os
    import subprocess

    summary = tmp_path / "summary.txt"
    env = dict(os.environ)
    env.update(
        {
            "CHANGED": changed,
            "BRANCH": branch,
            "EXPECTED_HEAD": expected_head,
            "INCIDENT": "1021",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_STEP_SUMMARY": str(summary),
        }
    )
    # Replace only the gh CLI with a deterministic local stub.
    stub = ('gh() { if [ "$2" = "view" ]; then printf "%s\\n" "$STUB_HEAD"; '
            'else printf "%s\\n" "$STUB_PR"; fi; }\\n')
    env["STUB_PR"] = returned_pr
    env["STUB_HEAD"] = remote_head
    return subprocess.run(
        ["bash", "-c", stub + _delivery_shell()],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_repair_without_changes_fails_closed(tmp_path):
    result = _run_delivery(tmp_path, changed="false", branch="", returned_pr="")
    assert result.returncode != 0
    assert "ACTIONABLE_REPAIR_NO_DELIVERABLE" in result.stdout


def test_repair_branch_without_pr_fails_closed(tmp_path):
    result = _run_delivery(tmp_path, changed="true", branch="claude/repair-1021", returned_pr="")
    assert result.returncode != 0
    assert "ACTIONABLE_REPAIR_PR_MISSING" in result.stdout


def test_repair_with_open_pr_passes(tmp_path):
    result = _run_delivery(tmp_path, changed="true", branch="claude/repair-1021", returned_pr="1544")
    assert result.returncode == 0, result.stderr
    assert "PR #1544" in (tmp_path / "summary.txt").read_text()


def test_repair_stale_pr_head_fails_closed(tmp_path):
    result = _run_delivery(
        tmp_path, changed="true", branch="claude/repair-1021",
        returned_pr="1544", remote_head="b" * 40,
    )
    assert result.returncode != 0
    assert "ACTIONABLE_REPAIR_PR_HEAD_MISMATCH" in result.stdout


def test_repair_without_valid_implementation_sha_fails_closed(tmp_path):
    result = _run_delivery(
        tmp_path, changed="true", branch="claude/repair-1021",
        returned_pr="1544", expected_head="missing",
    )
    assert result.returncode != 0
    assert "ACTIONABLE_REPAIR_HEAD_INVALID" in result.stdout
