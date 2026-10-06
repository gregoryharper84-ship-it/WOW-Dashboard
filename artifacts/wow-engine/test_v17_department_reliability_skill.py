from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
RELIABILITY_SKILL = ROOT / ".agents/skills/wow-engineering-reliability/SKILL.md"
CONTRACT = ROOT / "artifacts/wow-engine/WOW_ENGINEERING_RELIABILITY.yaml"
RELEASE_WORKFLOW = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"
TERMINAL_WORKFLOW = ROOT / ".github/workflows/wow-v17-terminal-closure-controller.yml"

DEPARTMENT_SKILLS = (
    ".agents/skills/wow-autonomous-product-qa-engineering-recovery/SKILL.md",
    ".agents/skills/wow-dots-engineering-orchestrator/SKILL.md",
    ".agents/skills/wow-engineering-auditor/SKILL.md",
    ".agents/skills/wow-engineering-implementation-agent/SKILL.md",
    ".agents/skills/wow-engineering-independent-review-agent/SKILL.md",
    ".agents/skills/wow-engineering-lead-agent/SKILL.md",
    ".agents/skills/wow-engineering-qa-verification-agent/SKILL.md",
    ".agents/skills/wow-engineering-release-observability-agent/SKILL.md",
    ".agents/skills/wow-engineering-reporter-agent/SKILL.md",
    ".agents/skills/wow-engineering-research-triage-agent/SKILL.md",
    ".agents/skills/wow-engineering-specialist-subagents/SKILL.md",
    ".agents/skills/wow-engineering-system-architect-agent/SKILL.md",
    ".agents/skills/wow-frontier-intelligence-agent/SKILL.md",
    ".agents/skills/wow-nightly-engineering-autopilot/SKILL.md",
    ".agents/skills/wow-replit-patch-governor/SKILL.md",
    ".agents/skills/wow-season-aware-continuous-improvement/SKILL.md",
)


def test_shared_reliability_skill_is_versioned_and_non_execution():
    text = RELIABILITY_SKILL.read_text()
    assert "WOW_ENGINEERING_RELIABILITY_V1" in text
    assert "V17_TERMINAL_REDUCER" in text
    assert "can_execute=false" in text
    assert "Machine-readable verification receipt" in text
    assert "Connector-write readback gate" in text


def test_every_engineering_department_skill_inherits_reliability_contract():
    for relative in DEPARTMENT_SKILLS:
        text = (ROOT / relative).read_text()
        assert ".agents/skills/wow-engineering-reliability/SKILL.md" in text, relative
        assert "WOW_ENGINEERING_RELIABILITY_V1" in text, relative


def test_machine_contract_requires_typed_receipt_and_evidence_gated_rollback():
    data = yaml.safe_load(CONTRACT.read_text())
    assert data["contract_version"] == "WOW_ENGINEERING_RELIABILITY_V1"
    assert data["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert data["can_execute"] is False
    assert data["terminal_receipt"]["prose_only_evidence_allowed"] is False
    assert data["terminal_receipt"]["require_merge_sha_equals_deployed_sha"] is True
    assert data["rollback"]["single_ambiguous_probe_may_trigger_rollback"] is False
    assert data["post_merge_acceptance_lock"]["name"] == "PROTECTED_MAIN_ACCEPTANCE_LOCK"
    assert data["post_merge_acceptance_lock"]["lower_priority_main_advance_allowed"] is False
    assert data["class_c"]["production_promotion_allowed"] is False


def test_reliability_workflows_parse_and_preserve_machine_evidence_chain():
    release = RELEASE_WORKFLOW.read_text()
    terminal = TERMINAL_WORKFLOW.read_text()
    assert yaml.safe_load(release)["name"] == "wow-v17-release-production-verification-agent"
    assert yaml.safe_load(terminal)["name"] == "wow-v17-terminal-closure-controller"
    assert "id-token: write" in release
    assert "Generate Reliability V1 machine receipt" in release
    assert "Persist Reliability V1 receipt to append-only ledger" in release
    assert "audit-persistence.json" in release
    assert "Upload immutable Reliability V1 evidence artifact" in release
    assert "verification-receipts.json" in terminal
    assert "audit-persistence.json" in terminal
    assert "INVALID_RECEIPT_SCHEMA" in (ROOT / "artifacts/wow-engine/v17/terminal_closure_controller.py").read_text()
