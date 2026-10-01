from __future__ import annotations

from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "wow-v17-ncaaf-model-maintenance.yml"


def test_model_maintenance_runs_even_when_prop_history_step_fails() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    prop_history = text.index("      - name: Acquire governed NCAAF player-prop history")
    model_maintenance = text.index("      - name: Run governed NCAAF model maintenance")
    assert prop_history < model_maintenance

    model_section = text[model_maintenance : model_maintenance + 250]
    assert "if: always() && github.event_name != 'push'" in model_section
    assert "/internal/v17/ncaaf-model-maintenance" in text


def test_workflow_preserves_nonexecution_governance() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert 'assert payload.get("automatic_certification") is False' in text
    assert 'assert payload.get("automatic_promotion") is False' in text
    assert 'assert payload.get("probability_publishable") is False' in text
