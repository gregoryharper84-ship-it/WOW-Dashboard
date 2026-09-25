from __future__ import annotations

from gate_engine import classifier, prop_category_focus
from gate_engine.labels import PropLabel


def _all_pass_row(sport="MLB", prop_type="Hits", line=0.5):
    return {
        "row_id": "focus-test-1",
        "sport": sport,
        "prop_type": prop_type,
        "line": line,
        "blockers": [],
        "gates": {
            "slate_validation": {"passed": True},
            "status_role": {"passed": True},
            "l5_l10_ledger": {"passed": True},
            "market_gate": {"passed": True, "market_status": "MARKET_VERIFIED"},
            "ev_gate": {"passed": True, "money_qualified": True, "edge_score": 0.10},
            "slip_structure": {"passed": True},
            "exposure_gate": {"passed": True},
            "calibration_health": {"passed": True, "grade": "HEALTHY", "ceiling": None},
        },
    }


def test_active_category_is_focus_qualified_and_can_final_approve():
    row = _all_pass_row("MLB", "Hits", 0.5)
    out = classifier.classify(row)
    assert out["focus_state"] == prop_category_focus.FOCUS_QUALIFIED
    assert out["focus_qualified"] is True
    assert out["terminal_label"] == PropLabel.FINAL_APPROVED.value


def test_provisional_category_is_suppressed_from_final_approval():
    row = _all_pass_row("NFL", "Receiving Yards", 70.5)
    out = classifier.classify(row)
    assert out["focus_state"] == prop_category_focus.FOCUS_PROVISIONAL
    assert out["focus_qualified"] is False
    assert out["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value
    assert "CLASSIFIER:PROP_CATEGORY_FOCUS:FOCUS_PROVISIONAL" in out["blockers"]


def test_unknown_category_fails_closed_to_research_only():
    row = _all_pass_row("NHL", "Unknown Experimental Prop", 1.5)
    out = classifier.classify(row)
    assert out["focus_state"] == prop_category_focus.RESEARCH_ONLY
    assert out["terminal_label"] == PropLabel.RESEARCH_INTEREST.value
    assert "CLASSIFIER:PROP_CATEGORY_FOCUS:RESEARCH_ONLY" in out["blockers"]


def test_calibration_suppression_suspends_even_active_category():
    row = _all_pass_row("MLB", "Hits", 0.5)
    row["gates"]["calibration_health"] = {
        "passed": False,
        "grade": "SUPPRESS",
        "ceiling": "LLP_REJECT",
    }
    out = classifier.classify(row)
    assert out["focus_state"] == prop_category_focus.FOCUS_SUSPENDED
    assert out["focus_qualified"] is False
    assert out["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value
    assert "CLASSIFIER:PROP_CATEGORY_FOCUS:FOCUS_SUSPENDED" in out["blockers"]


def test_focus_gate_does_not_override_existing_early_terminal_label():
    row = _all_pass_row("MLB", "Hits", 0.5)
    row["terminal_label"] = PropLabel.SLATE_PURGE.value
    out = classifier.classify(row)
    assert out["terminal_label"] == PropLabel.SLATE_PURGE.value
    assert "focus_state" not in out


def test_focus_gate_does_not_change_probability_fields():
    row = _all_pass_row("NFL", "Receiving Yards", 70.5)
    row["final_model_prob"] = 0.64
    row["calibrated_lower_bound"] = 0.58
    out = classifier.classify(row)
    assert out["final_model_prob"] == 0.64
    assert out["calibrated_lower_bound"] == 0.58


def test_multiple_categories_can_be_focus_qualified_when_registry_has_active_models(monkeypatch):
    def fake_lookup(sport, stat_key, line=None):
        if sport == "TEST" and stat_key.replace(" ", "").upper() in {"A", "B"}:
            return {"status": "ACTIVE", "model_id": f"test_{stat_key.lower()}"}
        return {"status": "NO_REGISTERED_MODEL", "model_id": "NO_REGISTERED_MODEL"}

    monkeypatch.setattr(prop_category_focus.model_registry, "lookup", fake_lookup)
    a = prop_category_focus.evaluate(_all_pass_row("TEST", "A", 1.5))
    b = prop_category_focus.evaluate(_all_pass_row("TEST", "B", 2.5))
    assert a["focus_state"] == prop_category_focus.FOCUS_QUALIFIED
    assert b["focus_state"] == prop_category_focus.FOCUS_QUALIFIED
