"""Tests for Module C: payout_context.py"""
import pytest
from gate_engine.payout_context import run, POWER_BREAKEVEN, FLEX_BREAKEVEN, MARGINAL_THRESHOLD
from gate_engine.labels import PropLabel


def _row():
    return {"blockers": [], "gates": {}, "terminal_label": None}


def _enr(fmt="3-pick Power", model_prob=0.70, haircut=0.04, usable=None, sportsbook_edge=None):
    ctx = {
        "intended_format":    fmt,
        "model_probability":  model_prob,
        "uncertainty_haircut": haircut,
    }
    if usable is not None:
        ctx["usable_probability"] = usable
    if sportsbook_edge is not None:
        ctx["sportsbook_edge"] = sportsbook_edge
    return {"payout_context": ctx}


class TestPositiveEV:
    def test_high_prob_3pick_power_positive(self):
        row = _row()
        result = run(row, _enr("3-pick Power", model_prob=0.72, haircut=0.04, usable=0.68))
        assert result["passed"] is True
        assert result["payout_slip_label"] == "POSITIVE_EV"
        assert result["blocked_from_slip"] is False

    def test_ev_gap_computed_correctly(self):
        row = _row()
        result = run(row, _enr("2-pick Power", model_prob=0.65, haircut=0.04, usable=0.61))
        expected_gap = round(0.61 - POWER_BREAKEVEN["2-pick Power"], 4)
        assert abs(result["ev_gap"] - expected_gap) < 0.001

    def test_4pick_power_passes_with_sufficient_prob(self):
        row = _row()
        result = run(row, _enr("4-pick Power", model_prob=0.74, haircut=0.03, usable=0.71))
        assert result["payout_slip_label"] == "POSITIVE_EV"


class TestNegativeEV:
    def test_low_prob_3pick_power_negative(self):
        row = _row()
        result = run(row, _enr("3-pick Power", model_prob=0.62, haircut=0.04, usable=0.58))
        assert result["passed"] is False
        assert result["payout_slip_label"] == "NEGATIVE_EV"
        assert result["blocked_from_slip"] is True
        assert row["terminal_label"] == PropLabel.MARKET_QUALIFIED_BUT_SLIP_NEGATIVE.value

    def test_negative_ev_adds_blocker(self):
        row = _row()
        run(row, _enr("3-pick Power", model_prob=0.60, haircut=0.04, usable=0.56))
        assert any("PAYOUT_CONTEXT" in b for b in row["blockers"])

    def test_2pick_power_just_below_breakeven_negative(self):
        row = _row()
        breakeven = POWER_BREAKEVEN["2-pick Power"]
        result = run(row, _enr("2-pick Power", usable=breakeven - 0.01))
        assert result["payout_slip_label"] == "NEGATIVE_EV"


class TestMarginalEV:
    def test_just_above_breakeven_is_marginal(self):
        row = _row()
        breakeven = POWER_BREAKEVEN["3-pick Power"]
        result = run(row, _enr("3-pick Power", usable=breakeven + 0.01))
        assert result["payout_slip_label"] == "MARGINAL_EV"
        assert result["passed"] is True
        assert result["blocked_from_slip"] is False

    def test_at_marginal_threshold_boundary(self):
        row = _row()
        breakeven = POWER_BREAKEVEN["2-pick Power"]
        result = run(row, _enr("2-pick Power", usable=breakeven + MARGINAL_THRESHOLD))
        assert result["payout_slip_label"] == "MARGINAL_EV"


class TestFormatPending:
    def test_format_pending_not_blocked(self):
        row = _row()
        result = run(row, _enr(fmt="FORMAT_PENDING"))
        assert result["passed"] is True
        assert result["payout_slip_label"] == "FORMAT_PENDING"
        assert result["blocked_from_slip"] is False

    def test_empty_format_is_format_pending(self):
        row = _row()
        result = run(row, _enr(fmt=""))
        assert result["payout_slip_label"] == "FORMAT_PENDING"


class TestUnverifiedFormat:
    def test_unknown_format_is_unverified(self):
        row = _row()
        result = run(row, _enr(fmt="7-pick Power"))
        assert result["payout_slip_label"] == "UNVERIFIED"
        assert result["blocked_from_slip"] is True
        assert result["ceiling"] == PropLabel.MODEL_QUALIFIED_HOLD.value if "ceiling" in result else True


class TestUnusable:
    def test_missing_model_prob_is_unusable(self):
        row = _row()
        enr = {"payout_context": {"intended_format": "3-pick Power", "uncertainty_haircut": 0.04}}
        result = run(row, enr)
        assert result["payout_slip_label"] == "UNUSABLE"
        assert result["blocked_from_slip"] is True


class TestStraightBet:
    def test_positive_sportsbook_edge_passes(self):
        row = _row()
        result = run(row, _enr("Straight bet", sportsbook_edge=0.035))
        assert result["passed"] is True
        assert result["payout_slip_label"] == "POSITIVE_EV"

    def test_negative_sportsbook_edge_fails(self):
        row = _row()
        result = run(row, _enr("Straight bet", sportsbook_edge=-0.03))
        assert result["passed"] is False
        assert result["payout_slip_label"] == "NEGATIVE_EV"

    def test_missing_sportsbook_edge_is_unverified(self):
        row = _row()
        result = run(row, _enr("Straight bet"))
        assert result["payout_slip_label"] == "UNVERIFIED"


class TestFlexFormats:
    def test_3pick_flex_positive(self):
        row = _row()
        result = run(row, _enr("3-pick Flex", usable=0.58))
        assert result["passed"] is True
        assert result["payout_slip_label"] in ("POSITIVE_EV", "MARGINAL_EV")

    def test_5pick_flex_negative(self):
        row = _row()
        from gate_engine.payout_context import FLEX_BREAKEVEN
        breakeven = FLEX_BREAKEVEN["5-pick Flex"]
        result = run(row, _enr("5-pick Flex", usable=breakeven - 0.05))
        assert result["payout_slip_label"] == "NEGATIVE_EV"


class TestUsableProbDerived:
    def test_usable_derived_from_model_and_haircut(self):
        row = _row()
        result = run(row, _enr("3-pick Power", model_prob=0.70, haircut=0.05, usable=None))
        assert result["usable_probability"] is not None
        assert abs(result["usable_probability"] - 0.65) < 0.001
