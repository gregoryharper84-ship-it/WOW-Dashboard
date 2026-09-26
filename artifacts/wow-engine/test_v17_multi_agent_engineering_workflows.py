from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml"
RELEASE = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"
RELEASE_RESUME = ROOT / ".github/workflows/wow-v17-release-resume-agent.yml"
PUSH_HANDOFF = ROOT / ".github/workflows/wow-v17-engineering-push-handoff.yml"
DISPATCH_BRIDGE = ROOT / ".github/workflows/wow-v17-chatgpt-engineering-dispatch-bridge.yml"
FRONTIER = ROOT / ".github/workflows/wow-v17-frontier-intelligence-agent.yml"
CHATGPT_ACTION = ROOT / ".github/actions/wow-chatgpt-agent/action.yml"
LEGACY_CLAUDE_PATHS = (
    ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml",
    ROOT / ".github/workflows/wow-v17-claude-engineering-dispatch-bridge.yml",
    ROOT / ".github/actions/wow-claude-agent/action.yml",
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_multi_agent_worker_has_independent_reliability_roles() -> None:
    text = WORKER.read_text()
    for identity in (
        "ENGINEERING_LEAD_AGENT",
        "RESEARCH_TRIAGE_AGENT",
        "ENGINEERING_AGENT",
        "INDEPENDENT_REVIEW_AGENT",
        "SYSTEM_ARCHITECT_AGENT",
        "QA_VERIFICATION_AGENT",
    ):
        assert identity in text
    assert "single implementation lease" in text
    assert "Morning-Green-Autonomous: true" in text
    assert "Pre-PR agent gates" in text


def test_worker_denies_policy_changing_implementation_lease() -> None:
    text = WORKER.read_text()
    assert "R2-repair-policy" in text
    assert "R3" in text
    assert "implementation lease denied" in text
    assert "No Class C change is authorized" in text


def test_qa_agent_requires_actual_implementation_change() -> None:
    text = WORKER.read_text()
    guarded_condition = "steps.impl.outputs.changed == 'true' && steps.regression.outputs.status == '0'"
    assert text.count(guarded_condition) == 2


def test_release_agent_cannot_merge_or_deploy() -> None:
    text = RELEASE.read_text()
    assert "RELEASE_OBSERVABILITY_AGENT" in text
    assert "Do not edit, merge, deploy" in text
    assert "PRODUCTION_VERIFIED" in text
    assert "can_execute=false" in text


def test_release_resume_agent_owns_unfinished_release_verification() -> None:
    text = RELEASE_RESUME.read_text()
    assert "RELEASE_OBSERVABILITY_AGENT" in text
    assert "DEPLOYED_PENDING_VERIFY" in text
    assert "MERGED_PENDING_DEPLOY" in text
    assert "PR_CREATED" in text
    assert "return PENDING instead of manufacturing closure" in text
    assert "can_execute=false" in text


def test_release_resume_agent_fails_closed_on_missing_openai_auth() -> None:
    text = RELEASE_RESUME.read_text()
    assert "Verify OpenAI authentication" in text
    assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in text
    assert "OPENAI_API_KEY is required for the release verification agent." in text
    assert "if: steps.priority.outputs.release_pending == 'true'" in text


def test_push_origin_nightly_scan_has_a_valid_worker_handoff() -> None:
    text = PUSH_HANDOFF.read_text()
    assert 'workflows: ["wow-v17-nightly-engineering-scan"]' in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert "github.event.workflow_run.conclusion != 'cancelled'" in text
    assert "gh workflow run wow-v17-chatgpt-engineering-worker.yml" in text
    assert "can_execute: false" in text


def test_frontier_agent_is_reliability_preempted_and_experiment_only() -> None:
    text = FRONTIER.read_text()
    assert "FRONTIER_INTELLIGENCE_AGENT" in text
    assert "frontier-gate" in text
    assert "production_change must be false" in text
    assert "Class C requires challenger" in text
    assert "can_execute=false" in text


def test_active_agent_workflows_are_openai_chatgpt_only() -> None:
    for path in (WORKER, RELEASE, RELEASE_RESUME, FRONTIER):
        text = path.read_text()
        assert "wow-chatgpt-agent" in text
        assert "OPENAI_API_KEY" in text
        assert "wow-claude-agent" not in text
        assert "ANTHROPIC_API_KEY" not in text
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in text

    action = CHATGPT_ACTION.read_text()
    assert "openai/codex-action@v1" in action
    assert "permission_profile" in action
    assert "safety-strategy: unprivileged-user" in action
    assert 'allow-bots: "true"' in action


def test_legacy_claude_engineering_entrypoints_are_removed() -> None:
    for path in LEGACY_CLAUDE_PATHS:
        assert not path.exists()
    bridge = DISPATCH_BRIDGE.read_text()
    assert "wow-v17-chatgpt-engineering-worker.yml" in bridge
    assert "OpenAI/ChatGPT" in bridge


def test_workflows_parse_as_yaml() -> None:
    assert _load(WORKER)["name"] == "wow-v17-chatgpt-engineering-worker"
    assert _load(RELEASE)["name"] == "wow-v17-release-production-verification-agent"
    assert _load(RELEASE_RESUME)["name"] == "wow-v17-release-resume-agent"
    assert _load(PUSH_HANDOFF)["name"] == "wow-v17-engineering-push-handoff"
    assert _load(DISPATCH_BRIDGE)["name"] == "wow-v17-chatgpt-engineering-dispatch-bridge"
    assert _load(FRONTIER)["name"] == "wow-v17-frontier-intelligence-agent"
    assert _load(CHATGPT_ACTION)["name"] == "WOW ChatGPT Agent Runner"
