"""Required backend CI wrapper for QA content-ledger recovery and re-roll policy."""
from pathlib import Path
import subprocess
import sys

import yaml

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".github/scripts/wow_qa_ledger_recovery.py"
POLICY = REPO / ".github/scripts/wow_qa_reroll_policy.py"
WORKFLOW = REPO / ".github/workflows/wow-v17-qa-ledger-recovery.yml"
REVIEW_WORKFLOW = REPO / ".github/workflows/wow-v17-simple-security-review.yml"
TEST_DIR = REPO / ".github/scripts/tests"


def test_ledger_recovery_negative_suite_runs_on_pr_head():
    assert SCRIPT.is_file() and POLICY.is_file() and WORKFLOW.is_file()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(TEST_DIR),
         "-p", "test_wow_qa_ledger_recovery.py", "-v"],
        cwd=REPO, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "Ran 28 tests" in result.stderr, result.stderr


def test_recovery_workflow_is_qa_app_only_and_never_required_pass():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    code = SCRIPT.read_text(encoding="utf-8")
    policy = POLICY.read_text(encoding="utf-8")
    loaded = yaml.load(workflow, Loader=yaml.BaseLoader)
    assert loaded["on"]["workflow_run"]["workflows"] == ["wow-v17-simple-security-review"]
    assert "pull_request" not in loaded["on"]
    assert "pull_request_target" not in loaded["on"]
    assert loaded["concurrency"]["group"] == "wow-independent-security-qa-ledger-writer"
    assert loaded["concurrency"]["cancel-in-progress"] == "false"
    assert loaded["jobs"]["recover-orphaned-ledger"]["environment"] == "wow-qa"
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "ref: main" in workflow
    assert "persist-credentials: false" in workflow
    assert "permission-checks: write" in workflow
    assert "steps.qa.outputs.token" in workflow
    assert "github.event.pull_request" not in workflow
    assert "WOW_QA_EXPERIMENTAL_PR_REJECT_BUDGET" not in workflow
    assert "LEDGER_RECOVERY_PASS_FORBIDDEN" in code
    assert "ALLOWED_RECOVERY_DECISIONS = frozenset({\"HOLD\", \"REJECT\"})" in code
    assert "can_execute=false" in workflow
    assert 'EXPERIMENTAL_ENV = "WOW_QA_EXPERIMENTAL_PR_REJECT_BUDGET"' in policy
    review = REVIEW_WORKFLOW.read_text(encoding="utf-8")
    assert "environment: wow-qa" in review
    for use in loaded["jobs"]["recover-orphaned-ledger"]["steps"]:
        if "uses" in use:
            assert "@" in use["uses"] and use["uses"].rsplit("@", 1)[-1]
            pin = use["uses"].rsplit("@", 1)[-1]
            assert len(pin) == 40
    for step in loaded["jobs"]["recover-orphaned-ledger"]["steps"]:
        script = step.get("run")
        if isinstance(script, str):
            assert "${{" not in script
