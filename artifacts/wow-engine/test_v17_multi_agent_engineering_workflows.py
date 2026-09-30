from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml"
CLAUDE_WORKER = ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml"
PROVIDER_DISPATCHER = ROOT / ".github/workflows/wow-v17-engineering-provider-dispatcher.yml"
RELEASE = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"
RELEASE_RESUME = ROOT / ".github/workflows/wow-v17-release-resume-agent.yml"
PUSH_HANDOFF = ROOT / ".github/workflows/wow-v17-engineering-push-handoff.yml"
DISPATCH_BRIDGE = ROOT / ".github/workflows/wow-v17-chatgpt-engineering-dispatch-bridge.yml"
FRONTIER = ROOT / ".github/workflows/wow-v17-frontier-intelligence-agent.yml"
CHATGPT_ACTION = ROOT / ".github/actions/wow-chatgpt-agent/action.yml"
CLAUDE_ACTION = ROOT / ".github/actions/wow-claude-agent/action.yml"
CODEX_ENGINEERING_SKILLS = (
    ROOT / ".agents/skills/wow-engineering-reporter-agent/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-lead-agent/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-research-triage-agent/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-specialist-subagents/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-implementation-agent/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-independent-review-agent/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-system-architect-agent/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-qa-verification-agent/SKILL.md",
    ROOT / ".agents/skills/wow-engineering-release-observability-agent/SKILL.md",
)


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _skill_frontmatter(path: Path) -> dict:
    text = path.read_text()
    assert text.startswith("---\n"), f"{path} is missing YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) == 3, f"{path} has malformed YAML frontmatter"
    metadata = yaml.safe_load(parts[1])
    assert isinstance(metadata, dict), f"{path} frontmatter is not a mapping"
    return metadata


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


def test_claude_fallback_has_same_independent_reliability_roles() -> None:
    text = CLAUDE_WORKER.read_text()
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
    assert "No Class C change is authorized" in text
    assert "can_execute=false" in text


def test_worker_denies_policy_changing_implementation_lease() -> None:
    for path in (WORKER, CLAUDE_WORKER):
        text = path.read_text()
        assert "R2-repair-policy" in text
        assert "R3" in text
        assert "implementation lease denied" in text
        assert "No Class C change is authorized" in text


def test_qa_agent_requires_actual_implementation_change() -> None:
    guarded_condition = "steps.impl.outputs.changed == 'true' && steps.regression.outputs.status == '0'"
    assert WORKER.read_text().count(guarded_condition) == 2
    assert CLAUDE_WORKER.read_text().count(guarded_condition) == 2


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


def test_release_resume_agent_has_bounded_read_only_provider_fallback() -> None:
    text = RELEASE_RESUME.read_text()
    assert "wow-chatgpt-agent" in text
    assert "wow-claude-agent" in text
    assert "OPENAI_API_KEY" in text
    assert "ANTHROPIC_API_KEY" in text
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text
    assert "continue-on-error: true" in text
    assert "steps.release_agent_openai.outcome == 'failure'" in text
    assert "claude_available" in text
    assert "Release verification primary failed and no successful read-only fallback receipt was produced." in text
    assert "contents: read" in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text


def test_push_origin_nightly_scan_has_provider_aware_handoff() -> None:
    text = PUSH_HANDOFF.read_text()
    assert 'workflows: ["wow-v17-nightly-engineering-scan"]' in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert "github.event.workflow_run.conclusion != 'cancelled'" in text
    assert "gh workflow run wow-v17-engineering-provider-dispatcher.yml" in text
    assert "OpenAI primary / Anthropic fallback / deterministic survival" in text
    assert "can_execute: false" in text


def test_frontier_agent_is_reliability_preempted_and_experiment_only() -> None:
    text = FRONTIER.read_text()
    assert "FRONTIER_INTELLIGENCE_AGENT" in text
    assert "frontier-gate" in text
    assert "production_change must be false" in text
    assert "Class C requires challenger" in text
    assert "can_execute=false" in text


def test_primary_openai_agent_workflows_remain_openai_only() -> None:
    for path in (WORKER, RELEASE, FRONTIER):
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


def test_claude_runtime_is_isolated_to_fallback_worker() -> None:
    worker = CLAUDE_WORKER.read_text()
    action = CLAUDE_ACTION.read_text()
    dispatcher = PROVIDER_DISPATCHER.read_text()
    assert "wow-claude-agent" in worker
    assert "ANTHROPIC_API_KEY" in worker
    assert "CLAUDE_CODE_OAUTH_TOKEN" in worker
    assert "anthropics/claude-code-base-action@16bc61eeac6dfaad1e3617aee9aefa59fce7c9be" in action
    assert "OPENAI_API_KEY" not in worker
    assert "OPENAI_API_KEY" in dispatcher
    assert "ANTHROPIC_API_KEY" in dispatcher


def test_claude_runner_prefers_api_key_then_fails_over_to_oauth() -> None:
    action = CLAUDE_ACTION.read_text()
    assert "id: claude_api" in action
    assert "if: inputs.anthropic_api_key != ''" in action
    assert "id: claude_oauth" in action
    assert "if: inputs.claude_code_oauth_token != '' && (inputs.anthropic_api_key == '' || steps.claude_api.outcome == 'failure')" in action
    assert action.count("continue-on-error: true") >= 2
    assert "steps.claude_api.outputs.structured_output || steps.claude_oauth.outputs.structured_output" in action
    assert "api_outcome=${API_OUTCOME:-not-run}, oauth_outcome=${OAUTH_OUTCOME:-not-run}" in action
    assert "claude_args: ${{ steps.config.outputs.claude_args }}" in action
    assert 'show_full_output: "false"' in action


def test_provider_dispatcher_has_typed_failover_and_survival() -> None:
    text = PROVIDER_DISPATCHER.read_text()
    assert "OPENAI_API_QUOTA_EXCEEDED" not in text
    assert "engineering_provider_failover.py classify" in text
    assert "engineering_provider_failover.py circuit-breaker" in text
    assert "wow-v17-chatgpt-engineering-worker.yml" in text
    assert "wow-v17-claude-engineering-worker.yml" in text
    assert "Deterministic survival checks" in text
    assert "Surface degraded provider state" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "can_execute: false" in text


def test_chatgpt_action_uses_explicit_schema_file_for_unprivileged_codex() -> None:
    action = CHATGPT_ACTION.read_text()
    assert "Materialize structured output schema" in action
    assert "output-schema-file: ${{ steps.schema.outputs.schema_file }}" in action
    assert "output-schema: ${{ inputs.output_schema }}" not in action
    assert 'chmod 0644 "$schema_file"' in action
    assert "Clean up structured output schema" in action


def test_active_codex_engineering_skills_have_valid_frontmatter() -> None:
    for path in CODEX_ENGINEERING_SKILLS:
        metadata = _skill_frontmatter(path)
        assert metadata.get("name") == path.parent.name
        description = metadata.get("description")
        assert isinstance(description, str) and description.strip()


def test_control_issue_bridge_uses_provider_dispatcher() -> None:
    bridge = DISPATCH_BRIDGE.read_text()
    assert "wow-v17-engineering-provider-dispatcher.yml" in bridge
    assert "OpenAI primary / Anthropic fallback / deterministic survival" in bridge
    assert "can_execute: false" in bridge


def test_workflows_parse_as_yaml() -> None:
    assert _load(WORKER)["name"] == "wow-v17-chatgpt-engineering-worker"
    assert _load(CLAUDE_WORKER)["name"] == "wow-v17-claude-engineering-worker"
    assert _load(PROVIDER_DISPATCHER)["name"] == "wow-v17-engineering-provider-dispatcher"
    assert _load(RELEASE)["name"] == "wow-v17-release-production-verification-agent"
    assert _load(RELEASE_RESUME)["name"] == "wow-v17-release-resume-agent"
    assert _load(PUSH_HANDOFF)["name"] == "wow-v17-engineering-push-handoff"
    assert _load(DISPATCH_BRIDGE)["name"] == "wow-v17-chatgpt-engineering-dispatch-bridge"
    assert _load(FRONTIER)["name"] == "wow-v17-frontier-intelligence-agent"
    assert _load(CHATGPT_ACTION)["name"] == "WOW ChatGPT Agent Runner"
    assert _load(CLAUDE_ACTION)["name"] == "WOW Claude Agent Runner"
