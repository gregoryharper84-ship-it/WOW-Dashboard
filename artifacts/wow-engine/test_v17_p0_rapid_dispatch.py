from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-p0-rapid-dispatch.yml"


def test_p0_rapid_dispatch_contract() -> None:
    text = WORKFLOW.read_text()
    assert 'cron: "*/15 * * * *"' in text
    assert 'select(.severity == "P0" and .execution_lane == "RAPID")' in text
    assert 'select(.status != "completed")' in text
    assert "wow-v17-engineering-provider-dispatcher.yml" in text
    assert "-f force_provider=auto" in text
    assert 'reason="P0_RAPID_LANE:${INCIDENT}"' in text
    assert "wow-v17-chatgpt-engineering-worker.yml" in text
    assert "wow-v17-claude-engineering-worker.yml" in text
    assert "single_implementation_lease: true" in text
    assert "can_execute: false" in text
    assert "terminal_authority: V17_TERMINAL_REDUCER" in text


def test_p0_rapid_dispatch_yaml_parses() -> None:
    data = yaml.safe_load(WORKFLOW.read_text())
    assert data["name"] == "wow-v17-p0-rapid-dispatch"
    assert data["permissions"]["actions"] == "write"
    assert data["permissions"]["contents"] == "read"
    assert data["permissions"]["issues"] == "read"
