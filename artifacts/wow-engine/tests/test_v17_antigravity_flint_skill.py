"""Contract assertions for Antigravity's optional Flint skill."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / ".agents" / "skills" / "wow-flint-coding-handoff" / "SKILL.md"


def test_flint_handoff_skill_is_discoverable_and_unprivileged():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: wow-flint-coding-handoff\n")
    for item in (
        "wow-amrt-recovery", "engineering_coding_handoff.py",
        "UNTRUSTED_RESEARCH_HANDOFF_ONLY", "FLINT_HANDOFF_REJECTED",
        "diagnostic_untrusted_data", "source_head_sha", "incident_id",
        "lease_group", "isolated local worktree", "CODEOWNER",
        "independent QA/Release", "V17_TERMINAL_REDUCER", "can_execute=false",
        "not a fitted sports model",
    ):
        assert item.lower() in text.lower()
    assert "FIXED_AND_VERIFIED" in text
    assert "secret transmission" in text.lower()

def test_flint_identity_match_and_rejection_remain_mandatory():
    text = SKILL.read_text(encoding="utf-8")
    fourth = next(line for line in text.splitlines() if line.startswith("4. "))
    identity = "matching incident_id, lease_group, source_head_sha, and can_execute=false"
    refusal = "On FLINT_HANDOFF_REJECTED"
    hold = "stop with a typed hold"
    def enforces_contract(line):
        return identity in line and refusal in line and hold in line and "continue" not in line.lower()
    assert enforces_contract(fourth)
    assert not enforces_contract(fourth.replace(identity, "incident_id, lease_group, source_head_sha"))
    assert not enforces_contract(fourth.replace(hold, "continue"))


