from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".agents/skills/wow-dots-engineering-orchestrator/SKILL.md"


def _text() -> str:
    return SKILL.read_text()


def _frontmatter() -> dict:
    text = _text()
    assert text.startswith("---\n")
    parts = text.split("---", 2)
    assert len(parts) == 3
    metadata = yaml.safe_load(parts[1])
    assert isinstance(metadata, dict)
    return metadata


def test_dots_skill_has_valid_frontmatter() -> None:
    metadata = _frontmatter()
    assert metadata["name"] == "wow-dots-engineering-orchestrator"
    assert metadata["description"].strip()


def test_dots_orchestrator_preserves_v17_authority_and_execution_guards() -> None:
    text = _text()
    for invariant in (
        "runtime_generation=V17_ACTIVE",
        "terminal_authority=V17_TERMINAL_REDUCER",
        "can_execute=false",
        "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true",
        "Exactly one controlling fitted sporting specialist",
    ):
        assert invariant in text
    assert "never originate, estimate, substitute, blend, override, or publish a sporting probability" in text
    assert "may not be rewritten as `MODEL_UNAVAILABLE`" in text


def test_dots_delegates_to_existing_engineering_recovery_team() -> None:
    text = _text()
    assert "wow-autonomous-product-qa-engineering-recovery" in text
    for stage in (
        "REPORTER_INTAKE",
        "RESEARCH_TRIAGE",
        "ENGINEERING",
        "INDEPENDENT_REVIEW",
        "QA_VERIFICATION",
        "RELEASE_OBSERVABILITY",
        "REPORTER_CLOSURE",
    ):
        assert stage in text


def test_dots_continues_queue_after_hard_external_blocker() -> None:
    text = _text()
    assert "BLOCKED_WITH_EXACT_REASON" in text
    assert "continue to the next eligible issue in the same cycle" in text
    assert "Do not let a blocked item cause the whole queue to idle" in text


def test_dots_requires_exact_terminal_disposition_for_every_issue() -> None:
    text = _text()
    for terminal in (
        "FIXED_AND_VERIFIED",
        "PR_CREATED",
        "EXPERIMENT_CREATED",
        "DUPLICATE",
        "NOT_REPRODUCIBLE",
        "BLOCKED_WITH_EXACT_REASON",
        "DEFERRED_WITH_JUSTIFICATION",
    ):
        assert terminal in text
    assert "Never silently drop a discovered issue" in text


def test_dots_platform_mode_is_truthful_and_has_work_surrogate() -> None:
    text = _text()
    assert "DOTS_NATIVE" in text
    assert "WORK_SURROGATE" in text
    assert "Never claim `DOTS_NATIVE` when Dots access has not been proven" in text
    assert "If Dots is not available to the account/workspace, do not wait" in text
