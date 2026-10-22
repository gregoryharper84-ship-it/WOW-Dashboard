"""Regression contract for the cross-team WOW Engineering operating mission.

Documentation only: no deployment, trading, probability or permission changes.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCTRINE = ROOT / "docs/wow/engineering/WOW_ENGINEERING_MISSION_DOCTRINE.md"
REFERENCING_DOCS = (
    ROOT / "CLAUDE.md",
    ROOT / "docs/wow/engineering/WOW_AMRT_RECOVERY_TEAM.md",
    ROOT / "docs/wow/engineering/WOW_SIRT_INDEPENDENT_ASSURANCE.md",
    ROOT / "artifacts/wow-engine/v17/WOW_ECOSYSTEM_CONDUCTOR.md",
)


def test_engineering_mission_is_linked_by_all_operating_contracts() -> None:
    doctrine = DOCTRINE.read_text(encoding="utf-8")
    assert "**DEBUG. DISSOLVE. DEPLOY.**" in doctrine
    for doc in REFERENCING_DOCS:
        text = doc.read_text(encoding="utf-8")
        assert "DEBUG. DISSOLVE. DEPLOY." in text, doc
        assert "WOW_ENGINEERING_MISSION_DOCTRINE.md" in text, doc


def test_engineering_mission_preserves_v17_safety_and_scope() -> None:
    doctrine = DOCTRINE.read_text(encoding="utf-8")
    for required in (
        "custom_gpt_identity=WOW_BETTING_ENGINE",
        "runtime_generation=V17_ACTIVE",
        "terminal_authority=V17_TERMINAL_REDUCER",
        "can_execute=false",
        "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true",
        "Exactly one registered governed fitted specialist",
        "No odds-implied, narrative, generic LLM, or pregame-for-live probability substitution",
        "without weakening controls",
    ):
        if required == "without weakening controls":
            assert required in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        else:
            assert required in doctrine, required


def test_definition_of_done_requires_actual_production_verification() -> None:
    doctrine = DOCTRINE.read_text(encoding="utf-8")
    for required in (
        "FIXED_AND_VERIFIED",
        "BLOCKED_WITH_EXACT_REASON",
        "live deployment SHA/ID",
        "actual acceptance and persistence/reconciliation receipts",
        "independent verification",
        "No background daemon exists merely because a document describes one",
    ):
        assert required in doctrine, required
