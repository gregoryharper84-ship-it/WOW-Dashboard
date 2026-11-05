"""Prevent privilege escalation or secret/paid-review regressions in the WOW-native workflow."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-native-senior-review.yml"
SCRIPT = ROOT / ".github/scripts/wow_native_senior_review.py"


def test_workflow_is_main_source_only_not_external_vendor():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request_target:" in text
    assert "workflow_dispatch:" in text
    assert "ref: main" in text
    assert "persist-credentials: false" in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "head.repo.full_name == github.repository" in text
    assert " --post-comment" in text or "--post-comment" in text
    assert "CodeRabbit" not in text
    assert "ANTHROPIC" not in text.upper()
    assert "GEMINI" not in text.upper()
    assert "openai.com" not in text.lower()


def test_workflow_has_no_approval_or_deploy_permissions():
    conf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    permissions = conf["permissions"]
    assert permissions == {
        "contents": "read", "pull-requests": "read", "issues": "write"}
    assert "id-token" not in permissions
    assert "deployments" not in permissions
    assert "checks" not in permissions
    assert "actions" not in permissions


def test_workflow_checkout_step_never_uses_untrusted_head():
    conf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = conf["jobs"]["native-review"]["steps"]
    checkout = [s for s in steps if "actions/checkout@" in s.get("uses", "")]
    assert len(checkout) == 1
    assert checkout[0]["with"]["ref"] == "main"
    assert checkout[0]["with"]["persist-credentials"] is False


def test_script_never_executes_or_checks_out_pr_code():
    code = SCRIPT.read_text(encoding="utf-8")
    assert "subprocess." not in code
    assert "os.system(" not in code
    assert "os.exec" not in code
    assert "ref=main" not in code
    assert "LOCAL_OLLAMA_ONLY" not in code or "--local-model" in code
    assert "REVIEW_PACKET_DRIFT" not in code or "run_static_review" in code
