"""Regression for legacy LLP governance memory's V17 authority boundary.

This is a source-contract test, not sporting-probability or production proof.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MEMORY = ROOT / ".agents/memory/llp-governance-reuse.md"
V17_STATUS = ROOT / "docs/wow/GENERATION_STATUS.md"
V17_OVERLAY = ROOT / "docs/wow/LLP-V17-AUTHORITY-OVERLAY-2026-09-10.md"
LEGACY = ROOT / "artifacts/flask-scoring-api/gate_engine/llp_governance.py"
MONEYLINE_ROUTING = (
    ROOT / "artifacts/flask-scoring-api/gate_engine/moneyline/routing_policy.py"
)


def test_memory_documents_v17_terminal_and_safety_boundary():
    text = MEMORY.read_text(encoding="utf-8")
    assert "V17_TERMINAL_REDUCER" in text
    assert "can_execute=false" in text
    assert "LLP_TEAM_BETTING_ENGINE" in text
    assert "legacy local market/classification outcome only" in text
    assert "use `effective_label` as the terminal label" not in text
    assert "model probability" in text
    assert "typed model and Action failures remain distinct" in text


def test_legacy_classifier_is_retained_but_v17_authority_is_separate():
    legacy = LEGACY.read_text(encoding="utf-8")
    routing = MONEYLINE_ROUTING.read_text(encoding="utf-8")
    status = V17_STATUS.read_text(encoding="utf-8")
    overlay = V17_OVERLAY.read_text(encoding="utf-8")
    assert "class LLPLabel(str, Enum)" in legacy
    assert "def run_llp_governance(" in legacy
    assert "get_calibration_ledger, validate_calibration_ledger" in routing
    assert '"terminal_label"' in routing and '"llp_label"' in routing
    assert "CURRENT_GENERATION = V17" in status
    assert "GLOBAL_TERMINAL_AUTHORITY = V17_TERMINAL_REDUCER" in status
    assert "V17_TERMINAL_REDUCER" in overlay
    assert "Missing or stale odds after valid model completion" in overlay
