"""WOW GPT host contract: ledger reads and research never stand in for scoring.

Regression for the 2026-11-22 cross-sport output: the live GPT made no
Action call, read the Supabase ledger directly, then built and recommended
"provisional research" slips from hit rates, projections and odds while
0 rows were rank-eligible.
"""
from pathlib import Path

INSTRUCTIONS = Path(__file__).parents[1] / "WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt"


def _text() -> str:
    return INSTRUCTIONS.read_text(encoding="utf-8")


def test_ledger_reads_never_count_as_action_scoring():
    text = _text()
    assert "No score receipt => no Full Model publication. Ledger reads are not scoring." in text


def test_research_evidence_never_becomes_picks_even_when_labeled_provisional():
    text = _text()
    assert (
        "hit rates, narratives, generic reasoning, reconstructed/DFS rates, or market "
        "consensus as governed model probability or as picks, even provisional."
    ) in text


def test_existing_no_pick_and_blocker_contract_preserved():
    text = _text()
    assert "Backend NO_PICK is terminal completion. Never rename failures to manufacture a pick." in text
    assert "otherwise report blockers" in text


def test_instructions_keep_editor_headroom():
    assert len(_text().encode("utf-8")) <= 7500
