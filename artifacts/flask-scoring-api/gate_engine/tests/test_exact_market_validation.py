"""
test_exact_market_validation.py — Phase 2 Cash Threshold Validation Suite

Verifies that market_gate and classifier correctly enforce pp_threshold cash_threshold
against sportsbook lines, so whole-number PP lines can't be falsely validated by
adjacent lower/higher markets.

Run with:
    cd artifacts/flask-scoring-api
    python -m pytest gate_engine/tests/test_exact_market_validation.py -v
"""
from __future__ import annotations

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine import market_gate
from gate_engine.market_gate import (
    CASH_STATUS_EXACT_VERIFIED,
    CASH_STATUS_NOT_VALIDATED,
    CASH_STATUS_ADJACENT_CONTEXT_ONLY,
    CASH_STATUS_MARKET_UNVERIFIED,
    CASH_STATUS_SOURCE_CONFLICT,
    CASH_STATUS_NO_THRESHOLDS,
    _validate_cash_threshold,
)
from gate_engine.labels import PropLabel
from gate_engine.classifier import classify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(line: float, direction: str, pp_thresholds: dict | None = None) -> dict:
    return {
        "row_id":        "test-row",
        "player":        "Test Player",
        "sport":         "WNBA",
        "prop_type":     "Assists",
        "line":          line,
        "direction":     direction,
        "pp_thresholds": pp_thresholds,
        "gates":         {},
        "blockers":      [],
        "terminal_label": None,
    }


def _pp_thresholds_more(line: float) -> dict:
    """Build pp_thresholds for a MORE bet with given displayed line."""
    whole = (line == int(line))
    if whole:
        return {
            "cash_threshold":  line + 1,
            "push_threshold":  line,
            "loss_threshold":  line - 1,
            "whole_number_line": True,
            "push_possible":   True,
        }
    return {
        "cash_threshold":  line + 0.5,
        "push_threshold":  None,
        "loss_threshold":  line - 0.5,
        "whole_number_line": False,
        "push_possible":   False,
    }


def _pp_thresholds_less(line: float) -> dict:
    whole = (line == int(line))
    if whole:
        return {
            "cash_threshold":  line - 1,
            "push_threshold":  line,
            "loss_threshold":  line + 1,
            "whole_number_line": True,
            "push_possible":   True,
        }
    return {
        "cash_threshold":  line - 0.5,
        "push_threshold":  None,
        "loss_threshold":  line + 0.5,
        "whole_number_line": False,
        "push_possible":   False,
    }


# ---------------------------------------------------------------------------
# _validate_cash_threshold unit tests
# ---------------------------------------------------------------------------

class TestValidateCashThresholdUnit:

    def test_no_thresholds_returns_no_cap(self):
        result = _validate_cash_threshold(5.0, "MORE", None, 4.5)
        assert result["cash_threshold_status"] == CASH_STATUS_NO_THRESHOLDS
        assert result["confidence_cap"] is None
        assert result["substitution_allowed"] is True

    def test_whole_number_more_sportsbook_at_displayed_minus_half_not_validated(self):
        """MORE 5 (cash=6): sportsbook 4.5 → CASH_THRESHOLD_NOT_VALIDATED"""
        thresholds = _pp_thresholds_more(5.0)
        result = _validate_cash_threshold(5.0, "MORE", thresholds, 4.5)
        assert result["cash_threshold_status"] == CASH_STATUS_NOT_VALIDATED
        assert result["exact_market_found"] is False
        assert result["adjacent_market_used"] is True
        assert result["adjacent_market_line"] == 4.5
        assert result["confidence_cap"] == "MODEL_QUALIFIED_HOLD"
        assert result["substitution_allowed"] is False

    def test_whole_number_more_sportsbook_at_displayed_half_is_exact(self):
        """MORE 5 (cash=6): sportsbook 5.5 → EXACT_VERIFIED (|5.5-6|=0.5)"""
        thresholds = _pp_thresholds_more(5.0)
        result = _validate_cash_threshold(5.0, "MORE", thresholds, 5.5)
        assert result["cash_threshold_status"] == CASH_STATUS_EXACT_VERIFIED
        assert result["exact_market_found"] is True
        assert result["confidence_cap"] is None
        assert result["substitution_allowed"] is True

    def test_whole_number_more_sportsbook_at_cash_threshold_exact(self):
        """MORE 5 (cash=6): sportsbook 6.0 → EXACT_VERIFIED (|6-6|=0)"""
        thresholds = _pp_thresholds_more(5.0)
        result = _validate_cash_threshold(5.0, "MORE", thresholds, 6.0)
        assert result["cash_threshold_status"] == CASH_STATUS_EXACT_VERIFIED
        assert result["exact_market_found"] is True

    def test_whole_number_less_sportsbook_at_displayed_plus_half_not_validated(self):
        """LESS 20 (cash=19): sportsbook 20.5 → CASH_THRESHOLD_NOT_VALIDATED"""
        thresholds = _pp_thresholds_less(20.0)
        result = _validate_cash_threshold(20.0, "LESS", thresholds, 20.5)
        assert result["cash_threshold_status"] == CASH_STATUS_NOT_VALIDATED
        assert result["adjacent_market_used"] is True
        assert result["confidence_cap"] == "MODEL_QUALIFIED_HOLD"

    def test_whole_number_less_sportsbook_at_cash_threshold_half_is_exact(self):
        """LESS 20 (cash=19): sportsbook 19.5 → EXACT_VERIFIED (|19.5-19|=0.5)"""
        thresholds = _pp_thresholds_less(20.0)
        result = _validate_cash_threshold(20.0, "LESS", thresholds, 19.5)
        assert result["cash_threshold_status"] == CASH_STATUS_EXACT_VERIFIED
        assert result["exact_market_found"] is True

    def test_half_point_more_sportsbook_at_displayed_is_exact(self):
        """MORE 6.5 (cash=7): sportsbook 6.5 → EXACT_VERIFIED (|6.5-7|=0.5)"""
        thresholds = _pp_thresholds_more(6.5)
        result = _validate_cash_threshold(6.5, "MORE", thresholds, 6.5)
        assert result["cash_threshold_status"] == CASH_STATUS_EXACT_VERIFIED
        assert result["exact_market_found"] is True

    def test_half_point_more_sportsbook_one_below_is_adjacent_context(self):
        """MORE 6.5 (cash=7): sportsbook 6.0 → ADJACENT_CONTEXT_ONLY"""
        thresholds = _pp_thresholds_more(6.5)
        result = _validate_cash_threshold(6.5, "MORE", thresholds, 6.0)
        assert result["cash_threshold_status"] == CASH_STATUS_ADJACENT_CONTEXT_ONLY
        assert result["confidence_cap"] == "MONEY_QUALIFIED_MAX"
        assert result["substitution_allowed"] is False

    def test_no_sportsbook_line_is_market_unverified(self):
        """No sportsbook data → MARKET_UNVERIFIED_EXACT regardless of thresholds"""
        thresholds = _pp_thresholds_more(5.0)
        result = _validate_cash_threshold(5.0, "MORE", thresholds, None)
        assert result["cash_threshold_status"] == CASH_STATUS_MARKET_UNVERIFIED
        assert result["exact_market_found"] is False
        assert result["confidence_cap"] == "MODEL_QUALIFIED_HOLD"

    def test_sportsbook_far_from_both_is_market_unverified(self):
        """Sportsbook 10.5 for MORE 5 → neither near cash(6) nor displayed(5)"""
        thresholds = _pp_thresholds_more(5.0)
        result = _validate_cash_threshold(5.0, "MORE", thresholds, 10.5)
        assert result["cash_threshold_status"] == CASH_STATUS_MARKET_UNVERIFIED
        assert result["confidence_cap"] == "MODEL_QUALIFIED_HOLD"


# ---------------------------------------------------------------------------
# market_gate.run() integration tests
# ---------------------------------------------------------------------------

class TestMarketGateRun:

    def test_whole_number_more_sportsbook_at_adjacent_adds_blocker(self):
        """MORE 5, sportsbook 4.5: gate should add CASH_THRESHOLD_NOT_VALIDATED blocker"""
        row = _row(5.0, "MORE", _pp_thresholds_more(5.0))
        market_gate.run(row, sportsbook_line=4.5)
        gate = row["gates"]["market_gate"]
        assert gate["cash_threshold_status"] == CASH_STATUS_NOT_VALIDATED
        assert any("CASH_THRESHOLD_NOT_VALIDATED" in b for b in row["blockers"])
        assert gate["confidence_cap"] == "MODEL_QUALIFIED_HOLD"

    def test_whole_number_more_sportsbook_at_exact_no_blocker(self):
        """MORE 5, sportsbook 5.5: EXACT_VERIFIED, no cash-threshold blocker"""
        row = _row(5.0, "MORE", _pp_thresholds_more(5.0))
        market_gate.run(row, sportsbook_line=5.5)
        gate = row["gates"]["market_gate"]
        assert gate["cash_threshold_status"] == CASH_STATUS_EXACT_VERIFIED
        assert not any("CASH_THRESHOLD" in b for b in row["blockers"])
        assert gate["confidence_cap"] is None

    def test_market_contradiction_sets_source_conflict_status(self):
        """MARKET_CONTRADICTION (pp < sportsbook within drift threshold) sets cash_status=SOURCE_CONFLICT.
        delta must be in (-0.5, -0.04) range: pp_line=5.0, sportsbook=5.3 → delta=-0.3."""
        row = _row(5.0, "MORE", _pp_thresholds_more(5.0))
        # pp_line=5, sportsbook=5.3 → delta=-0.3 → |delta|=0.3 < 0.5 → MARKET_CONTRADICTION
        market_gate.run(row, sportsbook_line=5.3)
        gate = row["gates"]["market_gate"]
        assert gate["market_status"] == "MARKET_CONTRADICTION"
        assert gate["cash_threshold_status"] == CASH_STATUS_SOURCE_CONFLICT
        assert gate["confidence_cap"] == "MODEL_QUALIFIED_HOLD"

    def test_half_point_exact_market_no_blocker(self):
        """MORE 6.5, sportsbook 6.5: EXACT_VERIFIED, no cash blocker"""
        row = _row(6.5, "MORE", _pp_thresholds_more(6.5))
        market_gate.run(row, sportsbook_line=6.5)
        gate = row["gates"]["market_gate"]
        assert gate["cash_threshold_status"] == CASH_STATUS_EXACT_VERIFIED
        assert gate["confidence_cap"] is None

    def test_half_point_adjacent_adds_adjacent_context_blocker(self):
        """MORE 6.5, sportsbook 6.0: ADJACENT_CONTEXT_ONLY blocker"""
        row = _row(6.5, "MORE", _pp_thresholds_more(6.5))
        market_gate.run(row, sportsbook_line=6.0)
        gate = row["gates"]["market_gate"]
        assert gate["cash_threshold_status"] == CASH_STATUS_ADJACENT_CONTEXT_ONLY
        assert any("ADJACENT_CONTEXT_ONLY" in b for b in row["blockers"])

    def test_no_thresholds_row_no_cash_blocker(self):
        """Row with no pp_thresholds: cash_threshold_status=NO_PP_THRESHOLDS, no cap"""
        row = _row(5.0, "MORE", None)
        market_gate.run(row, sportsbook_line=4.5)
        gate = row["gates"]["market_gate"]
        assert gate["cash_threshold_status"] == CASH_STATUS_NO_THRESHOLDS
        assert gate["confidence_cap"] is None


# ---------------------------------------------------------------------------
# Classifier integration tests (market_gate result → terminal_label)
# ---------------------------------------------------------------------------

class TestClassifierCashThresholdEnforcement:

    def _make_approved_gates(self, cash_threshold_status: str,
                              confidence_cap: str | None) -> dict:
        """Return a gates dict that would reach FINAL_APPROVED but for the cash cap."""
        return {
            "slate_validation": {"passed": True},
            "status_role":      {"passed": True},
            "l5_l10_ledger":    {"passed": True},
            "market_gate": {
                "passed":               True,
                "market_status":        "MARKET_VERIFIED",
                "cash_threshold_status": cash_threshold_status,
                "confidence_cap":       confidence_cap,
                "substitution_allowed": confidence_cap is None,
            },
            "ev_gate": {
                "passed":         True,
                "money_qualified": True,
                "edge_score":     0.12,
            },
            "slip_structure":   {"passed": True},
            "exposure_gate":    {"passed": True},
            "outlier_gate":     {"any_flag": False},
        }

    def _row_with_gates(self, gates: dict) -> dict:
        return {
            "row_id":         "test",
            "terminal_label": None,
            "data_status":    "RETRIEVED",
            "blockers":       [],
            "gates":          gates,
        }

    def test_exact_verified_can_reach_final_approved(self):
        gates = self._make_approved_gates(CASH_STATUS_EXACT_VERIFIED, None)
        row = self._row_with_gates(gates)
        classify(row)
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value

    def test_cash_threshold_not_validated_capped_to_model_qualified(self):
        """Whole-number MORE 5 with sportsbook 4.5 → MODEL_QUALIFIED_HOLD"""
        gates = self._make_approved_gates(CASH_STATUS_NOT_VALIDATED, "MODEL_QUALIFIED_HOLD")
        row = self._row_with_gates(gates)
        classify(row)
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value
        assert any("MARKET_CASH_CAP" in b for b in row["blockers"])

    def test_market_unverified_capped_to_model_qualified(self):
        """No matching sportsbook market → MODEL_QUALIFIED_HOLD"""
        gates = self._make_approved_gates(CASH_STATUS_MARKET_UNVERIFIED, "MODEL_QUALIFIED_HOLD")
        row = self._row_with_gates(gates)
        classify(row)
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_adjacent_context_only_capped_to_money_qualified(self):
        """Half-point adjacent market → MONEY_QUALIFIED (not FINAL_APPROVED)"""
        gates = self._make_approved_gates(CASH_STATUS_ADJACENT_CONTEXT_ONLY, "MONEY_QUALIFIED_MAX")
        row = self._row_with_gates(gates)
        classify(row)
        assert row["terminal_label"] == PropLabel.MONEY_QUALIFIED.value
        assert any("MARKET_CASH_CAP" in b for b in row["blockers"])

    def test_source_conflict_in_cash_status_capped_to_model_qualified(self):
        gates = self._make_approved_gates(CASH_STATUS_SOURCE_CONFLICT, "MODEL_QUALIFIED_HOLD")
        row = self._row_with_gates(gates)
        classify(row)
        assert row["terminal_label"] == PropLabel.MODEL_QUALIFIED_HOLD.value

    def test_no_pp_thresholds_legacy_row_still_reaches_final_approved(self):
        """Legacy rows without pp_thresholds: NO_PP_THRESHOLDS, no cap → FINAL_APPROVED"""
        gates = self._make_approved_gates(CASH_STATUS_NO_THRESHOLDS, None)
        row = self._row_with_gates(gates)
        classify(row)
        assert row["terminal_label"] == PropLabel.FINAL_APPROVED.value

    def test_reject_labels_not_touched_by_cash_cap(self):
        """Pre-set REJECT_NO_EDGE rows are never upgraded by cash threshold logic"""
        gates = self._make_approved_gates(CASH_STATUS_NOT_VALIDATED, "MODEL_QUALIFIED_HOLD")
        row = self._row_with_gates(gates)
        row["terminal_label"] = PropLabel.REJECT_NO_EDGE.value
        classify(row)
        assert row["terminal_label"] == PropLabel.REJECT_NO_EDGE.value

    def test_duplicate_exposure_block_not_touched_by_cash_cap(self):
        gates = self._make_approved_gates(CASH_STATUS_NOT_VALIDATED, "MODEL_QUALIFIED_HOLD")
        row = self._row_with_gates(gates)
        row["terminal_label"] = PropLabel.DUPLICATE_EXPOSURE_BLOCK.value
        classify(row)
        assert row["terminal_label"] == PropLabel.DUPLICATE_EXPOSURE_BLOCK.value
