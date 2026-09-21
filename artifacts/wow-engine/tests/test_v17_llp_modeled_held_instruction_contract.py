from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LLP_EDITOR = ROOT / "LLP_V17_CUSTOM_GPT_INSTRUCTIONS.txt"
LLP_AUTHORITY = ROOT.parent.parent / "LLP-TEAM-BETTING-GPT-INSTRUCTIONS.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_llp_editor_uses_live_capability_and_canonical_team_event_action():
    text = _text(LLP_EDITOR)
    assert "getWowV17Capabilities" in text
    assert "scoreWowV17TeamEventFromWowHost" in text
    assert "Current coverage is runtime state, not a static prompt fact" in text
    assert "Current active `/score-team-event` production implementation has a certified MLB game-win specialist path" not in text
    assert "soccer/MLS is not currently certified by the active repository implementation" not in text


def test_llp_editor_does_not_conflate_rank_gate_with_probability_visibility():
    text = _text(LLP_EDITOR)
    assert "`rank_eligible=false` is a ranking/card-admission gate only" in text
    assert "`OFFICIAL_QUALIFIED`" in text
    assert "`MODELED_HELD`" in text
    assert "`BLOCKED_UNSCORED`" in text
    assert "`NO VERIFIED PLAY` cannot be the sole board verdict" in text
    assert "Never say the actual winner probability is unavailable until lineup lock" in text


def test_llp_authority_contract_preserves_modeled_held_probability():
    text = _text(LLP_AUTHORITY)
    assert "getWowV17Capabilities" in text
    assert "scoreWowV17TeamEventFromWowHost" in text
    assert "rank_eligible=false is a ranking/card-admission state" in text
    assert "MODELED_HELD: display returned governed probability + bounds + exact blocker" in text
    assert "NO VERIFIED PLAY cannot be the sole board verdict" in text
    assert "can_execute=false always" in text
