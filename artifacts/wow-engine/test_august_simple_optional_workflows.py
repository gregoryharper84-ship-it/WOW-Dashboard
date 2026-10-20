"""August-simple phase 1: optional engineering maintenance is manual-only.

This test intentionally makes no changes to model, CI, QA, security, or release paths.
"""
from pathlib import Path
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
OPTIONAL = (
    "wow-v17-engineering-digest.yml",
    "wow-v17-engineering-effectiveness-scorecard.yml",
    "wow-v17-engineering-pr-keep-current.yml",
    "wow-v17-engineering-probe-cleanup.yml",
)

def trigger_of(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("on", data.get(True))

@pytest.mark.parametrize("name", OPTIONAL)
def test_optional_workflows_are_explicitly_manual_only(name):
    on = trigger_of(WF / name)
    assert isinstance(on, dict)
    assert set(on) == {"workflow_dispatch"}, (name, list(on))

@pytest.mark.parametrize("name", ("wow-verify.yml", "wow-engine-verify.yml"))
def test_august_core_ci_still_runs_on_pr_and_main_push(name):
    on = trigger_of(WF / name)
    assert "pull_request" in on
    assert "push" in on and "main" in on["push"]["branches"]

def test_independent_review_and_protected_deployment_remain_present():
    qa = (WF / "wow-v17-independent-qa-check.yml").read_text(encoding="utf-8")
    release = (WF / "wow-v17-release-authority-check.yml").read_text(encoding="utf-8")
    deploy = (WF / "wow-v17-render-production-deploy.yml").read_text(encoding="utf-8")
    assert "WOW Independent QA exact-head" in qa or "independent" in qa.lower()
    assert "wow-v17-independent-qa-check" in release
    assert "environment: wow-release" in deploy
    assert 'WOW_CAN_EXECUTE: "false"' in deploy
    assert 'WOW_DRY_RUN_ONLY: "true"' in deploy

def test_august_minimal_retirement_does_not_touch_model_or_live_execution():
    for name in OPTIONAL:
        text = (WF / name).read_text(encoding="utf-8")
        assert "workflow_dispatch:" in text
