from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION = ROOT / ".agents/skills/wow-engineering-implementation-agent/SKILL.md"
ORCHESTRATOR = ROOT / ".agents/skills/wow-dots-engineering-orchestrator/SKILL.md"


def test_repository_persistence_escalates_across_independent_transports() -> None:
    for path in (IMPLEMENTATION, ORCHESTRATOR):
        text = path.read_text(encoding="utf-8")
        assert "create_blob -> create_tree -> create_commit -> update_ref" in text
        assert "REPOSITORY_WRITE_UNAVAILABLE" in text
        assert "Contents API" in text
        assert "protected" in text


def test_ci_closure_uses_typed_infrastructure_states_and_one_shot_retry() -> None:
    for path in (IMPLEMENTATION, ORCHESTRATOR):
        text = path.read_text(encoding="utf-8")
        for state in (
            "CI_JOB_CANCELLED_BEFORE_START",
            "CI_CAPACITY_STARVATION",
            "CI_REQUIRED_GATE_FAILED",
            "CI_PENDING",
            "CI_GREEN",
        ):
            assert state in text
        assert "one-shot" in text.lower() or "once" in text.lower()
        assert "branch protection" in text.lower()
