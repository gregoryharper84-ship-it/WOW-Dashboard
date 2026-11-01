from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
STRESS = ROOT / ".github/workflows/wow-v17-scout-persistence-stress.yml"
CAPTAIN = ROOT / ".github/workflows/wow-v17-morning-green-continuation.yml"
CHATGPT_WORKER = ROOT / ".github/workflows/wow-v17-chatgpt-engineering-worker.yml"
CLAUDE_WORKER = ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml"


def test_scout_stress_workflow_is_manual_staging_safe_and_10x_by_default():
    text = STRESS.read_text()
    data = yaml.safe_load(text)
    assert data["name"] == "wow-v17-scout-persistence-stress"
    assert "workflow_dispatch:" in text
    assert 'default: "10"' in text
    assert 'default: "30"' in text
    assert "WOW_SCOUT_STRESS_ALLOWED_HOST" in text
    assert "WOW_SCOUT_STRESS_TOKEN" in text
    assert "production_target_forbidden: true" in text
    assert "production_canary_required: true" in text
    assert "can_execute: false" in text
    assert data["permissions"]["contents"] == "read"


def test_existing_morning_green_workflow_is_merge_captain():
    text = CAPTAIN.read_text()
    assert "Re-verify all protected checks on exact head SHA" in text
    assert "Verify trusted exact-head governance receipt" in text
    assert "Verify Morning-Green diff scope" in text
    assert 'gh pr merge "$PR_NUMBER"' in text
    assert "--match-head-commit" in text
    assert "Morning-Green autonomous merge denied" in text
    assert "can_execute: false" in text


def test_targeted_openai_agent_failure_directly_dispatches_same_lease_claude_fallback():
    text = CHATGPT_WORKER.read_text()
    block = text.split("- name: Dispatch targeted Claude fallback on OpenAI agent failure", 1)[1]
    block = block.split("- name: Write exact-head governance receipt", 1)[0]
    assert "always()" in block
    assert "github.event_name == 'workflow_dispatch'" in block
    assert "inputs.target_incident != ''" in block
    assert "steps.claude_peer_provider.outputs.available == 'true'" in block
    for agent in (
        "lead_agent",
        "triage_agent",
        "specialist_agent",
        "implementation_agent",
        "review_agent",
        "architect_agent",
        "qa_agent",
    ):
        assert f"steps.{agent}.outcome == 'failure'" in block
    assert "gh workflow run wow-v17-claude-engineering-worker.yml" in block
    assert '-f target_incident="$TARGET_INCIDENT"' in block
    assert '-f lease_group="$LEASE_GROUP"' in block
    assert 'fallback_reason="OPENAI_AGENT_STEP_FAILURE_DIRECT_HANDOFF"' in block
    assert "steps.regression.outputs.status" not in block
    assert "steps.review.outputs.decision" not in block


def test_openai_and_claude_writers_share_exact_domain_concurrency_group():
    expected = "group: wow-v17-engineering-domain-${{ inputs.lease_group || 'GLOBAL' }}"
    assert expected in CHATGPT_WORKER.read_text()
    assert expected in CLAUDE_WORKER.read_text()
