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
