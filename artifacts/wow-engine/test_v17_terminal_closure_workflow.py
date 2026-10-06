from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-terminal-closure-controller.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_terminal_closure_workflow_is_valid_and_hourly():
    data = yaml.load(_text(), Loader=yaml.BaseLoader)
    assert data["name"] == "wow-v17-terminal-closure-controller"
    assert data["on"]["schedule"] == [{"cron": "17 * * * *"}]
    assert "workflow_dispatch" in data["on"]


def test_terminal_closure_workflow_has_write_authority_only_for_closure_surfaces():
    data = yaml.load(_text(), Loader=yaml.BaseLoader)
    assert data["permissions"] == {
        "actions": "write",
        "checks": "read",
        "contents": "read",
        "issues": "write",
        "pull-requests": "write",
    }


def test_terminal_closure_dispatch_is_whitelisted_and_fail_closed():
    text = _text()
    assert "wow-v17-release-production-verification-agent.yml" in text
    assert "wow-v17-nightly-multiscout.yml|wow-v17-nightly-engineering-scan.yml" in text
    assert "TERMINAL_CONTROLLER_ACCEPTANCE_NOT_WHITELISTED" in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert "V17_TERMINAL_REDUCER" in text


def test_terminal_closure_requires_explicit_autonomous_opt_in():
    text = _text()
    assert "Terminal-Closure-Autonomous: true" in text
    assert "Terminal-Issue:" in text


def test_terminal_closure_workflow_supports_legacy_worker_metadata():
    text = _text()
    assert "Morning-Green-Autonomous: true" in text
    assert "Incident:" in text
    assert "sed -nE 's/^Incident:" in text
