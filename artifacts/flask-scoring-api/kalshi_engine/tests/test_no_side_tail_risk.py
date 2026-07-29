"""
test_no_side_tail_risk.py
Tests for kalshi_engine/no_side_tail_risk.py
WOW-PATCH-2026-07-29-KALSHI-NO-SIDE-TAIL-RISK-AND-CALIBRATION

Covers:
  TestArchitectureInvariants     — can_execute, capital_allocation, patch_id, rules
  TestComplementSideScan         — Gate 1 bidirectional YES/NO scoring
  TestHighPriceTailRiskGate      — Gate 2 tail-loss metrics
  TestHistoricalZeroFallacy      — Gate 3 probability=0/1 and historical-zero blocks
  TestVolumeIsNotDepth           — Gate 4 depth validation
  TestLaneCeilings               — reviewer-specified label outcomes
  TestCalibrationEntry           — Gate 5 calibration entry builder
"""
from __future__ import annotations

import math
import pytest

import kalshi_engine.no_side_tail_risk as nst

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _book(
    best_yes_bid=0.55,
    best_yes_ask=0.57,
    best_no_bid=0.43,
    best_no_ask=0.45,
    depth_within_1c=80,
    depth_within_2c=200,
    liquidity_grade="B",
) -> dict:
    """Standard normalized book with depth fields."""
    return {
        "best_yes_bid":    best_yes_bid,
        "best_yes_ask":    best_yes_ask,
        "best_no_bid":     best_no_bid,
        "best_no_ask":     best_no_ask,
        "yes_spread":      round(best_yes_ask - best_yes_bid, 4),
        "no_spread":       round(best_no_ask - best_no_bid, 4),
        "mid_price":       round((best_yes_bid + best_yes_ask) / 2, 4),
        "depth_within_1c": depth_within_1c,
        "depth_within_2c": depth_within_2c,
        "depth_at_price":  depth_within_1c,
        "liquidity_grade": liquidity_grade,
        "raw_level_count": 20,
    }


def _high_price_book(best_no_ask=0.92, best_yes_ask=0.08) -> dict:
    """Book for a high-price NO contract (NO trades at ~92¢)."""
    return _book(
        best_yes_bid=0.07,
        best_yes_ask=best_yes_ask,
        best_no_bid=0.91,
        best_no_ask=best_no_ask,
    )


def _book_no_depth() -> dict:
    """Book without depth fields — simulates missing orderbook depth data."""
    b = _book()
    b.pop("depth_within_1c", None)
    b.pop("depth_within_2c", None)
    return b


# ---------------------------------------------------------------------------
# TestArchitectureInvariants
# ---------------------------------------------------------------------------

class TestArchitectureInvariants:

    def test_can_execute_is_false(self):
        assert nst.can_execute is False

    def test_capital_allocation_is_false(self):
        assert nst.capital_allocation is False

    def test_patch_id_is_correct(self):
        assert nst.PATCH_ID == "WOW-PATCH-2026-07-29-KALSHI-NO-SIDE-TAIL-RISK-AND-CALIBRATION"

    def test_patch_status_is_candidate(self):
        assert nst.PATCH_STATUS == "CANDIDATE_FORWARD_TEST_ONLY"

    def test_run_always_returns_can_execute_false(self):
        result = nst.run(0.55, _book())
        assert result["can_execute"] is False
        assert result["capital_allocation"] is False

    def test_run_returns_patch_id(self):
        result = nst.run(0.55, _book())
        assert result["patch_id"] == nst.PATCH_ID

    def test_run_returns_patch_rules_block(self):
        result = nst.run(0.55, _book())
        rules = result["patch_rules"]
        assert rules["FREE_MONEY_LANGUAGE_PROHIBITED"] is True
        assert rules["NO_SIDE_AUTO_EDGE"] is False
        assert rules["HISTORICAL_ZERO_IS_NOT_ZERO_PROBABILITY"] is True
        assert rules["VOLUME_IS_NOT_EXECUTABLE_DEPTH"] is True
        assert rules["HIGH_WIN_RATE_IS_NOT_POSITIVE_EV"] is True
        assert rules["TAIL_LOSS_RATIO_REQUIRED"] is True
        assert rules["PRICE_BUCKET_CALIBRATION_REQUIRED"] is True
        assert rules["FIELD_NORMALIZATION_REQUIRED_FOR_OUTRIGHTS"] is True

    def test_run_with_none_model_probability_returns_unobtainable(self):
        result = nst.run(None, _book())
        assert result["patch_label"] == "KALSHI_DATA_UNOBTAINABLE"
        assert result["can_execute"] is False

    def test_volume_is_not_depth_rule_always_stamped(self):
        result = nst.run(0.55, _book())
        assert result["depth_liquidity"]["volume_is_not_depth_rule"] is True

    def test_historical_non_occurrence_misused_label_value(self):
        assert nst.LABEL_HISTORICAL_NON_OCCURRENCE_MISUSED == "HISTORICAL_NON_OCCURRENCE_MISUSED"


# ---------------------------------------------------------------------------
# TestComplementSideScan — Gate 1
# ---------------------------------------------------------------------------

class TestComplementSideScan:

    def test_yes_and_no_edge_both_computed(self):
        result = nst.run(0.60, _book(), side="YES", calibrated_probability_lower_bound=0.62)
        scan = result["complement_side_scan"]
        assert scan["yes_edge"] is not None
        assert scan["no_edge"] is not None

    def test_model_prob_no_is_complement_of_yes(self):
        result = nst.run(0.70, _book(), side="NO")
        scan = result["complement_side_scan"]
        assert scan["model_prob_yes"] == pytest.approx(0.70, abs=1e-4)
        assert scan["model_prob_no"]  == pytest.approx(0.30, abs=1e-4)

    def test_best_qualified_side_chosen_correctly(self):
        # YES at 57¢ with P(YES)=0.70 → raw=0.13; NO at 45¢ with P(NO)=0.30 → raw=-0.15
        # YES has better edge
        result = nst.run(0.70, _book(), side="YES", calibrated_probability_lower_bound=0.72)
        scan = result["complement_side_scan"]
        assert scan["best_qualified_side"] == "YES"

    def test_no_side_is_best_when_no_has_higher_edge(self):
        # NO at 15¢, P(NO)=0.90 → raw=0.75; YES at 85¢, P(YES)=0.10 → raw=-0.75
        book = _book(best_yes_bid=0.14, best_yes_ask=0.16,
                     best_no_bid=0.84, best_no_ask=0.86)
        result = nst.run(0.10, book, side="NO")
        scan = result["complement_side_scan"]
        assert scan["best_qualified_side"] == "NO"

    def test_third_state_flags_void_state(self):
        result = nst.run(0.60, _book(), third_state_probability=0.05)
        scan = result["complement_side_scan"]
        assert scan["void_state_preserved"] is True
        assert scan["complement_valid"] is False
        assert any("THIRD_STATE" in w for w in scan["warnings"])

    def test_binary_market_complement_valid(self):
        result = nst.run(0.60, _book())
        scan = result["complement_side_scan"]
        assert scan["void_state_preserved"] is False
        assert scan["complement_valid"] is True

    def test_outright_market_flags_normalization_required(self):
        result = nst.run(0.12, _book(), is_outright_market=True)
        scan = result["complement_side_scan"]
        assert scan["is_outright_market"] is True
        assert any("FIELD_NORMALIZATION" in w for w in scan["warnings"])

    def test_no_entry_price_yields_none_edges(self):
        book = {}  # no price data
        result = nst.run(0.55, book, side="NO")
        scan = result["complement_side_scan"]
        assert scan["yes_edge"] is None
        assert scan["no_edge"] is None
        assert scan["best_qualified_side"] is None

    def test_breakeven_equals_entry_price(self):
        result = nst.run(0.60, _book(), side="YES")
        scan = result["complement_side_scan"]
        assert scan["yes_breakeven"] == scan["yes_entry_price"]
        assert scan["no_breakeven"]  == scan["no_entry_price"]


# ---------------------------------------------------------------------------
# TestHighPriceTailRiskGate — Gate 2
# ---------------------------------------------------------------------------

class TestHighPriceTailRiskGate:

    def test_below_threshold_returns_none(self):
        result = nst.run(0.60, _book(), side="YES", calibrated_probability_lower_bound=0.62)
        assert result["high_price_tail_risk"] is None

    def test_above_threshold_triggers_gate(self):
        book = _high_price_book(best_no_ask=0.92)
        result = nst.run(0.10, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert tr is not None
        assert tr["triggered"] is True

    def test_wins_required_is_correct_at_92c(self):
        # entry=0.92, fee = 0.07 * min(0.92, 0.08) = 0.07 * 0.08 = 0.0056
        # net_profit = 1 - 0.92 - 0.0056 = 0.0744
        # wins_required = ceil(0.92 / 0.0744) = ceil(12.36...) = 13
        book = _high_price_book(best_no_ask=0.92)
        result = nst.run(0.10, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert tr["wins_required_to_recover_one_loss"] == math.ceil(0.92 / tr["net_profit_if_win"])

    def test_loss_to_win_ratio_correct(self):
        book = _high_price_book(best_no_ask=0.92)
        result = nst.run(0.10, book, side="NO")
        tr = result["high_price_tail_risk"]
        expected = round(tr["maximum_loss"] / tr["net_profit_if_win"], 4)
        assert tr["loss_to_win_ratio"] == pytest.approx(expected, abs=1e-3)

    def test_maximum_loss_is_entry_cost(self):
        book = _high_price_book(best_no_ask=0.95)
        result = nst.run(0.05, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert tr["maximum_loss"] == pytest.approx(0.95, abs=1e-4)
        assert tr["entry_cost"]   == pytest.approx(0.95, abs=1e-4)

    def test_fee_adjusted_breakeven_above_entry(self):
        book = _high_price_book(best_no_ask=0.90)
        result = nst.run(0.10, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert tr["fee_adjusted_breakeven"] > tr["entry_cost"]

    def test_extreme_price_bucket_flagged(self):
        book = _high_price_book(best_no_ask=0.97)
        result = nst.run(0.03, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert tr["is_extreme_price_bucket"] is True
        assert tr["price_bucket"] == "95-99c"
        assert any("EXTREME" in w for w in tr["warnings"])

    def test_85c_bucket_not_extreme(self):
        book = _high_price_book(best_no_ask=0.87)
        result = nst.run(0.13, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert tr["is_extreme_price_bucket"] is False
        assert tr["price_bucket"] == "85-89c"

    def test_high_win_rate_warning_always_present(self):
        book = _high_price_book(best_no_ask=0.88)
        result = nst.run(0.12, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert any("HIGH_WIN_RATE_IS_NOT_POSITIVE_EV" in w for w in tr["warnings"])

    def test_price_bucket_correct_for_90c(self):
        book = _high_price_book(best_no_ask=0.92)
        result = nst.run(0.10, book, side="NO")
        tr = result["high_price_tail_risk"]
        assert tr["price_bucket"] == "90-94c"

    def test_fee_detail_overrides_computed_fee(self):
        # Supply a custom fee_detail with fee_per_contract=0.001
        book = _high_price_book(best_no_ask=0.92)
        fee_detail = {"fee_per_contract": 0.001}
        result = nst.run(0.10, book, side="NO", fee_detail=fee_detail)
        tr = result["high_price_tail_risk"]
        assert tr["fee_per_contract"] == pytest.approx(0.001, abs=1e-6)

    def test_internal_fee_formula_isolated(self):
        # _fee_for_price uses FEE_RATE * min(price, 1-price)
        assert nst._fee_for_price(0.92) == pytest.approx(0.07 * 0.08, abs=1e-6)
        assert nst._fee_for_price(0.50) == pytest.approx(0.07 * 0.50, abs=1e-6)
        assert nst._fee_for_price(0.30) == pytest.approx(0.07 * 0.30, abs=1e-6)


# ---------------------------------------------------------------------------
# TestHistoricalZeroFallacy — Gate 3
# ---------------------------------------------------------------------------

class TestHistoricalZeroFallacy:

    def test_probability_zero_without_proof_is_blocked(self):
        result = nst.run(1.0, _book(), side="NO", calibrated_probability_lower_bound=0.80)
        # P(NO) = 1 - 1.0 = 0.0 → blocked
        fallacy = result["historical_zero_analysis"]
        assert fallacy["blocked"] is True
        assert fallacy["label"] == nst.LABEL_HISTORICAL_NON_OCCURRENCE_MISUSED

    def test_probability_one_without_proof_is_blocked(self):
        # P(YES) = 1.0 without logically_certain
        result = nst.run(1.0, _book(), side="YES", calibrated_probability_lower_bound=0.99)
        fallacy = result["historical_zero_analysis"]
        assert fallacy["blocked"] is True
        assert fallacy["label"] == nst.LABEL_HISTORICAL_NON_OCCURRENCE_MISUSED

    def test_probability_zero_with_logically_impossible_allowed(self):
        # P(NO) = 0.0 but logically_impossible=True
        result = nst.run(1.0, _book(), side="NO", logically_impossible=True)
        fallacy = result["historical_zero_analysis"]
        assert fallacy["blocked"] is False
        assert fallacy["label"] is None

    def test_probability_one_with_logically_certain_allowed(self):
        result = nst.run(1.0, _book(), side="YES", logically_certain=True)
        fallacy = result["historical_zero_analysis"]
        assert fallacy["blocked"] is False

    def test_historical_zero_with_nonzero_prob_is_warning_not_block(self):
        result = nst.run(0.85, _book(), side="NO",
                         historical_occurrence_count=0,
                         historical_sample_size=20)
        fallacy = result["historical_zero_analysis"]
        # P(NO) = 0.15 — nonzero, so no block, just a warning
        assert fallacy["blocked"] is False
        assert any("[WARNING]" in r for r in fallacy["reasons"])

    def test_historical_zero_with_zero_probability_is_blocked(self):
        # P(YES)=1.0 → P(NO)=0.0 + historical_occurrence_count=0 → doubly blocked
        result = nst.run(1.0, _book(), side="NO",
                         historical_occurrence_count=0,
                         historical_sample_size=50)
        fallacy = result["historical_zero_analysis"]
        assert fallacy["blocked"] is True

    def test_normal_probability_no_fallacy(self):
        result = nst.run(0.55, _book(), side="YES")
        fallacy = result["historical_zero_analysis"]
        assert fallacy["blocked"] is False
        assert fallacy["label"] is None
        assert fallacy["reasons"] == []

    def test_patch_label_reject_when_fallacy_blocked(self):
        result = nst.run(1.0, _book(), side="NO", calibrated_probability_lower_bound=0.80)
        assert result["patch_label"] == "KALSHI_REJECT_NO_EDGE"

    def test_zero_sample_size_no_trigger(self):
        # historical_sample_size=0 — no meaningful sample; gate should not trigger
        result = nst.run(0.95, _book(), side="NO",
                         historical_occurrence_count=0,
                         historical_sample_size=0)
        fallacy = result["historical_zero_analysis"]
        assert fallacy["blocked"] is False

    def test_side_probability_is_for_intended_side(self):
        # side=NO → side_model_prob = 1 - model_probability
        result = nst.run(0.70, _book(), side="NO")
        fallacy = result["historical_zero_analysis"]
        assert fallacy["side_model_probability"] == pytest.approx(0.30, abs=1e-4)

    def test_side_probability_yes_is_model_probability(self):
        result = nst.run(0.70, _book(), side="YES")
        fallacy = result["historical_zero_analysis"]
        assert fallacy["side_model_probability"] == pytest.approx(0.70, abs=1e-4)


# ---------------------------------------------------------------------------
# TestVolumeIsNotDepth — Gate 4
# ---------------------------------------------------------------------------

class TestVolumeIsNotDepth:

    def test_depth_fields_present_passes(self):
        result = nst.run(0.55, _book())
        depth = result["depth_liquidity"]
        assert depth["depth_validated"] is True
        assert depth["violations"] == []

    def test_missing_depth_fields_fails(self):
        result = nst.run(0.55, _book_no_depth())
        depth = result["depth_liquidity"]
        assert depth["depth_validated"] is False
        assert len(depth["violations"]) >= 1

    def test_volume_without_depth_adds_violation(self):
        result = nst.run(0.55, _book_no_depth(), market_volume=53_000_000)
        depth = result["depth_liquidity"]
        assert any("VOLUME_PRESENTED_WITHOUT_DEPTH" in v for v in depth["violations"])

    def test_volume_with_depth_no_violation(self):
        result = nst.run(0.55, _book(), market_volume=53_000_000)
        depth = result["depth_liquidity"]
        assert depth["violations"] == []
        assert depth["market_volume"] == 53_000_000

    def test_market_volume_not_provided(self):
        result = nst.run(0.55, _book())
        depth = result["depth_liquidity"]
        assert depth["market_volume_provided"] is False
        assert depth["market_volume"] is None

    def test_volume_is_not_depth_rule_always_true(self):
        # Rule flag must be True regardless of whether depth data is present
        r1 = nst.run(0.55, _book())
        r2 = nst.run(0.55, _book_no_depth())
        assert r1["depth_liquidity"]["volume_is_not_depth_rule"] is True
        assert r2["depth_liquidity"]["volume_is_not_depth_rule"] is True

    def test_depth_values_returned(self):
        book = _book(depth_within_1c=120, depth_within_2c=300)
        result = nst.run(0.55, book)
        depth = result["depth_liquidity"]
        assert depth["depth_within_1c"] == 120
        assert depth["depth_within_2c"] == 300


# ---------------------------------------------------------------------------
# TestLaneCeilings — reviewer-specified labels
# ---------------------------------------------------------------------------

class TestLaneCeilings:

    def test_uncalibrated_no_scan_yields_watch(self):
        """No calibrated_lb → KALSHI_WATCH."""
        result = nst.run(0.55, _book(), side="NO",
                         calibrated_probability_lower_bound=None)
        assert result["patch_label"] == "KALSHI_WATCH"
        assert any("UNCALIBRATED" in r for r in result["patch_blocking_reasons"])

    def test_calibrated_but_no_depth_yields_unobtainable(self):
        """Calibrated model + missing depth → KALSHI_DATA_UNOBTAINABLE."""
        result = nst.run(0.55, _book_no_depth(), side="NO",
                         calibrated_probability_lower_bound=0.50)
        assert result["patch_label"] == "KALSHI_DATA_UNOBTAINABLE"

    def test_uncalibrated_and_no_depth_yields_watch(self):
        """Neither calibrated nor depth → KALSHI_WATCH (most conservative)."""
        result = nst.run(0.55, _book_no_depth(), side="NO",
                         calibrated_probability_lower_bound=None)
        assert result["patch_label"] == "KALSHI_WATCH"

    def test_positive_point_edge_but_lb_below_breakeven_yields_reject(self):
        """
        P(NO) = 0.45, NO entry = 0.40 → raw_edge = 0.05 (positive)
        fee ≈ 0.07 * 0.40 = 0.028
        fee_adj_breakeven ≈ 0.428
        calibrated_lb = 0.42 < 0.428 → KALSHI_REJECT_NO_EDGE
        """
        book = _book(best_yes_bid=0.59, best_yes_ask=0.61,
                     best_no_bid=0.39, best_no_ask=0.41)
        # P(YES)=0.55, P(NO)=0.45; NO entry=0.41, fee=0.07*0.41=0.0287, breakeven≈0.4387
        # calibrated_lb=0.42 < 0.4387 → reject
        result = nst.run(0.55, book, side="NO", calibrated_probability_lower_bound=0.42)
        assert result["patch_label"] == "KALSHI_REJECT_NO_EDGE"

    def test_all_gates_pass_yields_research_eligible(self):
        """
        P(YES)=0.60, NO entry≈0.45
        fee=0.07*0.45=0.0315, breakeven≈0.4815
        calibrated_lb=0.55 >> breakeven → KALSHI_SINGLE_RESEARCH_ELIGIBLE
        """
        result = nst.run(0.60, _book(), side="NO",
                         calibrated_probability_lower_bound=0.55)
        assert result["patch_label"] == "KALSHI_SINGLE_RESEARCH_ELIGIBLE"

    def test_fallacy_block_overrides_all_other_gates(self):
        """Historical zero fallacy must override even a valid depth + calibrated lb."""
        result = nst.run(1.0, _book(), side="NO",
                         calibrated_probability_lower_bound=0.99)
        assert result["patch_label"] == "KALSHI_REJECT_NO_EDGE"

    def test_research_eligible_still_not_executable(self):
        """KALSHI_SINGLE_RESEARCH_ELIGIBLE is NOT an execute signal."""
        result = nst.run(0.60, _book(), side="NO",
                         calibrated_probability_lower_bound=0.55)
        assert result["patch_label"] == "KALSHI_SINGLE_RESEARCH_ELIGIBLE"
        assert result["can_execute"] is False
        assert result["capital_allocation"] is False


# ---------------------------------------------------------------------------
# TestCalibrationEntry — Gate 5
# ---------------------------------------------------------------------------

class TestCalibrationEntry:

    def test_entry_fields_populated(self):
        entry = nst.build_calibration_entry(
            market_ticker="KXNBA-LEBRON-POINTS",
            side_yes_no="NO",
            model_probability=0.30,
            calibrated_lb=0.28,
            entry_price=0.72,
            patch_label="KALSHI_WATCH",
            category="sports",
        )
        assert entry["market_ticker"] == "KXNBA-LEBRON-POINTS"
        assert entry["side_yes_no"]   == "NO"
        assert entry["price_bucket"]  == "70-84c"
        assert entry["patch_label"]   == "KALSHI_WATCH"
        assert entry["mode"]          == "paper"
        assert entry["patch_id"]      == nst.PATCH_ID

    def test_entry_embedded_in_run_result(self):
        result = nst.run(0.60, _book(), side="NO",
                         calibrated_probability_lower_bound=0.55,
                         market_ticker="TEST-TICKER")
        cal = result["no_side_calibration_entry"]
        assert cal["market_ticker"] == "TEST-TICKER"
        assert cal["mode"]          == "paper"
        assert cal["patch_id"]      == nst.PATCH_ID

    def test_tail_risk_fields_flattened_into_entry(self):
        book = _high_price_book(best_no_ask=0.92)
        result = nst.run(0.10, book, side="NO",
                         calibrated_probability_lower_bound=0.55,
                         market_ticker="HIGH-PRICE-TEST")
        cal = result["no_side_calibration_entry"]
        assert cal["is_high_price_contract"] is True
        assert cal["loss_to_win_ratio"] is not None
        assert cal["wins_required"] is not None
        assert cal["fee_adjusted_breakeven"] is not None

    def test_no_tail_risk_yields_false_flag(self):
        # Normal-price contract: tail risk should not trigger
        result = nst.run(0.55, _book(), side="NO",
                         calibrated_probability_lower_bound=0.50,
                         market_ticker="NORMAL-PRICE")
        cal = result["no_side_calibration_entry"]
        assert cal["is_high_price_contract"] is False
        assert cal["loss_to_win_ratio"] is None
        assert cal["wins_required"] is None

    def test_price_bucket_mapping(self):
        cases = [
            (0.55, "50-69c"),
            (0.75, "70-84c"),
            (0.87, "85-89c"),
            (0.92, "90-94c"),
            (0.97, "95-99c"),
        ]
        for price, expected_bucket in cases:
            assert nst._price_bucket(price) == expected_bucket, \
                f"price={price} → expected {expected_bucket}"

    def test_side_yes_no_uppercased(self):
        entry = nst.build_calibration_entry("TICKER", side_yes_no="no")
        assert entry["side_yes_no"] == "NO"


# ---------------------------------------------------------------------------
# TestPatchRuleConstants — module-level constants never change
# ---------------------------------------------------------------------------

class TestPatchRuleConstants:

    def test_high_price_threshold_is_85c(self):
        assert nst.HIGH_PRICE_THRESHOLD == pytest.approx(0.85, abs=1e-6)

    def test_extreme_price_threshold_is_95c(self):
        assert nst.EXTREME_PRICE_THRESHOLD == pytest.approx(0.95, abs=1e-6)

    def test_fee_rate_matches_fee_model(self):
        # Must stay in sync with kalshi_engine/fee_model.py FEE_RATE
        from kalshi_engine.fee_model import FEE_RATE
        assert nst._FEE_RATE == FEE_RATE

    def test_label_historical_non_occurrence_misused_in_gate_engine_labels(self):
        from gate_engine.labels import PropLabel
        assert PropLabel.HISTORICAL_NON_OCCURRENCE_MISUSED.value == \
            nst.LABEL_HISTORICAL_NON_OCCURRENCE_MISUSED
