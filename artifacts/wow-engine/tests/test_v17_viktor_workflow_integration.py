"""Ensure Viktor advisory only augments already-admitted worker context."""
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[3]
FILES = [ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
         ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml"]

@pytest.mark.parametrize("path", FILES)
def test_viktor_step_is_after_incident_selection_and_before_first_agent(path):
    content = path.read_text()
    marker = "Attach Viktor audit pointers to admitted incident (advisory only)"
    assert content.count(marker) == 1
    assert content.index("Initialize persistent-worker safety state") < content.index(marker)
    assert content.index(marker) < content.index("Engineering Lead agent")
    assert "if: steps.dispatch.outputs.restoration != ''" in content
    assert '--head-sha "$GITHUB_SHA"' in content
    assert '--incident "$RESTORATION"' in content
    assert "GH_TOKEN: ${{ github.token }}" in content
    assert "--context artifacts/wow-engine/v17/.agent-handoff/restoration-context.json" in content

@pytest.mark.parametrize("path", FILES)
def test_viktor_does_not_create_issue_comment_trigger_or_grant_new_authority(path):
    content = path.read_text()
    assert "\n  issue_comment:" not in content
    assert "cancel-in-progress: false" in content
    assert "wow-v17-engineering-implementation-single-writer" in content
    assert "V17_TERMINAL_REDUCER" in content
    assert "VIKTOR_ADVISORY_NOT_OBSERVED" in content


@pytest.mark.parametrize("path", FILES)
def test_advisory_helper_pinned_from_trusted_workflow_head_before_older_repair_checkout(path):
    content = path.read_text()
    marker = "Pin Viktor advisory helper from trusted dispatch checkout"
    assert content.count(marker) == 1
    assert content.index("Reject stale queued worker before credential mint") < content.index(marker)
    assert content.index(marker) < content.index("Initialize persistent-worker safety state")
    if "Checkout exact existing repair head under fence" in content:
        assert content.index(marker) < content.index("Checkout exact existing repair head under fence")
    assert 'install -m 0600 artifacts/wow-engine/v17/viktor_audit_evidence.py "$RUNNER_TEMP/wow-viktor-audit-evidence.py"' in content
    assert 'python "$RUNNER_TEMP/wow-viktor-audit-evidence.py"' in content
    assert not any(
        line.strip().startswith("artifacts/wow-engine/v17/viktor_audit_evidence.py")
        for line in content.splitlines()
    )


def test_pinned_helper_runs_when_repair_checkout_has_no_helper(tmp_path):
    import json
    import shutil
    import subprocess
    import sys

    pinned = tmp_path / "trusted-helper.py"
    shutil.copyfile(ROOT / "artifacts/wow-engine/v17/viktor_audit_evidence.py", pinned)
    old_checkout = tmp_path / "old-PR-checkout"
    old_checkout.mkdir()
    assert not (old_checkout / "artifacts/wow-engine/v17/viktor_audit_evidence.py").exists()
    comments = tmp_path / "comments.json"
    comments.write_text("[]")
    ticket = tmp_path / "ticket.json"
    ticket.write_text(json.dumps({
        "incident_id": "1950",
        "reproduction_evidence": [],
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False
    }))
    run = subprocess.run([
        sys.executable, str(pinned), "--incident", "1950", "--head-sha", "a" * 40,
        "--comments", str(comments), "--context", str(ticket)
    ], cwd=old_checkout, capture_output=True, text=True, check=True)
    receipt = json.loads(run.stdout)
    assert receipt["status"] == "ADVISORY_ONLY"
    assert receipt["accepted"] == 0
    assert json.loads(ticket.read_text())["can_execute"] is False
