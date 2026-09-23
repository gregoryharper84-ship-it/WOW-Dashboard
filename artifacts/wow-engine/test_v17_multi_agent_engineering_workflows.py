from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml"
RELEASE = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"
RELEASE_RESUME = ROOT / ".github/workflows/wow-v17-release-resume-agent.yml"
PUSH_HANDOFF = ROOT / ".github/workflows/wow-v17-engineering-push-handoff.yml"
FRONTIER = ROOT / ".github/workflows/wow-v17-frontier-intelligence-agent.yml"


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


def test_push_origin_nightly_scan_has_a_valid_worker_handoff() -> None:
    text = PUSH_HANDOFF.read_text()
    assert 'workflows: ["wow-v17-nightly-engineering-scan"]' in text
    assert "github.event.workflow_run.event == 'push'" in text
    assert "github.event.workflow_run.conclusion != 'cancelled'" in text
    assert "gh workflow run wow-v17-claude-engineering-worker.yml" in text
    assert "can_execute: false" in text


def test_frontier_agent_is_reliability_preempted_and_experiment_only() -> None:
    text = FRONTIER.read_text()
    assert "FRONTIER_INTELLIGENCE_AGENT" in text
    assert "frontier-gate" in text
    assert "production_change must be false" in text
    assert "Class C requires challenger" in text
    assert "can_execute=false" in text


def test_workflows_parse_as_yaml() -> None:
    assert _load(WORKER)["name"] == "wow-v17-claude-engineering-worker"
    assert _load(RELEASE)["name"] == "wow-v17-release-production-verification-agent"
    assert _load(RELEASE_RESUME)["name"] == "wow-v17-release-resume-agent"
    assert _load(PUSH_HANDOFF)["name"] == "wow-v17-engineering-push-handoff"
    assert _load(FRONTIER)["name"] == "wow-v17-frontier-intelligence-agent"
