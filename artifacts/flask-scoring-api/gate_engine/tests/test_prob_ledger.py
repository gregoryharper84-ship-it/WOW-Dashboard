"""Tests for Module D: prob_ledger.py"""
import pytest
from gate_engine.prob_ledger import run, SHRINKAGE_THRESHOLD, UNCALIBRATED_EXTRA_HAIRCUT


def _row():
    return {"blockers": [], "gates": {}, "terminal_label": None}


def _ledger(components=None, final_model_prob=0.57, ci="0.53-0.61",
            haircut=0.04, usable=0.53, calibration="CALIBRATED",
            shrinkage_applied=True, shrinkage_baseline="season"):
    return {
        "model_probability_ledger": {
            "components":           components or _default_components(),
            "final_model_prob":     final_model_prob,
            "confidence_interval":  ci,
            "uncertainty_haircut":  haircut,
            "usable_probability":   usable,
            "calibration_status":   calibration,
            "shrinkage_applied":    shrinkage_applied,
            "shrinkage_baseline":   shrinkage_baseline,
        }
    }


def _default_components():
    return [
        {"name": "market_no_vig",    "weight": 0.45, "value": 0.54, "source": "OddsAPI"},
        {"name": "l10_distribution", "weight": 0.30, "value": 0.60, "source": "StatFeed"},
        {"name": "role_usage",       "weight": 0.15, "value": 0.55, "source": "RotoWire"},
        {"name": "l5_trend",         "weight": 0.03, "value": 0.02, "source": "StatFeed"},
    ]


class TestProbLedgerPass:
    def test_valid_ledger_passes(self):
        row = _row()
        result = run(row, _ledger())
        assert result["passed"] is True
        assert result["code"] == "PROB_LEDGER_OK"
        assert result["missing_required"] == []
        assert result["blocked_found"] == []

    def test_calibrated_status_preserved(self):
        row = _row()
        result = run(row, _ledger(calibration="CALIBRATED"))
        assert result["calibration_status"] == "CALIBRATED"

    def test_proxy_only_status_preserved(self):
        row = _row()
        result = run(row, _ledger(calibration="PROXY_ONLY",
                                  components=_default_components()))
        assert result["calibration_status"] == "PROXY_ONLY"


class TestMissingRequiredComponents:
    def test_missing_market_no_vig_fails(self):
        row = _row()
        comps = [c for c in _default_components() if c["name"] != "market_no_vig"]
        result = run(row, _ledger(components=comps))
        assert result["passed"] is False
        assert "market_no_vig" in result["missing_required"]
        assert result["calibration_status"] == "UNCALIBRATED"

    def test_missing_l10_fails(self):
        row = _row()
        comps = [c for c in _default_components() if c["name"] != "l10_distribution"]
        result = run(row, _ledger(components=comps))
        assert "l10_distribution" in result["missing_required"]

    def test_missing_role_usage_fails(self):
        row = _row()
        comps = [c for c in _default_components() if c["name"] != "role_usage"]
        result = run(row, _ledger(components=comps))
        assert "role_usage" in result["missing_required"]

    def test_uncalibrated_penalty_applied(self):
        row = _row()
        comps = [c for c in _default_components() if c["name"] != "market_no_vig"]
        result = run(row, _ledger(components=comps))
        assert result["uncalibrated_penalty"] == UNCALIBRATED_EXTRA_HAIRCUT
        assert result["uncalibrated_kelly_cap"] is not None


class TestNarrativeBlocked:
    def test_narrative_component_blocked(self):
        row = _row()
        comps = _default_components() + [
            {"name": "narrative", "weight": 0.10, "value": 0.65, "source": "analyst"}
        ]
        result = run(row, _ledger(components=comps))
        assert result["passed"] is False
        assert "narrative" in result["blocked_found"]
        assert result["code"] == "NARRATIVE_COMPONENT_BLOCKED"

    def test_zero_weight_narrative_ignored(self):
        row = _row()
        comps = _default_components() + [
            {"name": "narrative", "weight": 0.0, "value": 0.0, "source": "analyst"}
        ]
        result = run(row, _ledger(components=comps))
        assert "narrative" not in result["blocked_found"]


class TestShrinkageRule:
    def test_high_prob_without_shrinkage_fails(self):
        row = _row()
        enr = _ledger(final_model_prob=0.65, shrinkage_applied=False)
        result = run(row, enr)
        assert result["shrinkage_required"] is True
        assert result["passed"] is False

    def test_high_prob_with_shrinkage_passes(self):
        row = _row()
        enr = _ledger(final_model_prob=0.65, shrinkage_applied=True,
                      shrinkage_baseline="season")
        result = run(row, enr)
        assert result["shrinkage_required"] is False

    def test_below_threshold_no_shrinkage_needed(self):
        row = _row()
        enr = _ledger(final_model_prob=0.58, shrinkage_applied=False)
        result = run(row, enr)
        assert result["shrinkage_required"] is False

    def test_exactly_at_threshold_requires_shrinkage(self):
        row = _row()
        enr = _ledger(final_model_prob=SHRINKAGE_THRESHOLD, shrinkage_applied=False)
        result = run(row, enr)
        assert result["shrinkage_required"] is True


class TestConfidenceInterval:
    def test_missing_ci_flagged(self):
        row = _row()
        enr = _ledger(ci=None)
        result = run(row, enr)
        assert result["has_confidence_interval"] is False
        assert result["passed"] is False

    def test_ci_present_ok(self):
        row = _row()
        result = run(row, _ledger(ci="0.53-0.61"))
        assert result["has_confidence_interval"] is True


class TestInfluenceBounds:
    def test_market_over_50pct_violation(self):
        row = _row()
        comps = _default_components()
        for c in comps:
            if c["name"] == "market_no_vig":
                c["weight"] = 0.60
        result = run(row, _ledger(components=comps))
        assert len(result["influence_violations"]) > 0

    def test_l5_trend_over_5pct_violation(self):
        row = _row()
        comps = _default_components()
        for c in comps:
            if c["name"] == "l5_trend":
                c["weight"] = 0.10
        result = run(row, _ledger(components=comps))
        assert any("l5_trend" in v for v in result["influence_violations"])


class TestBlockersAdded:
    def test_violations_add_blockers(self):
        row = _row()
        comps = [c for c in _default_components() if c["name"] != "market_no_vig"]
        run(row, _ledger(components=comps))
        assert any("PROB_LEDGER" in b for b in row["blockers"])
