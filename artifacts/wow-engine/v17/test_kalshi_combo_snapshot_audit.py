"""Focused regression tests for research-only Kalshi history snapshot audit."""
import importlib.util
from pathlib import Path

import pytest

FILE = Path(__file__).with_name("research") / "kalshi_combo_snapshot_audit.py"
spec = importlib.util.spec_from_file_location("kalshi_combo_snapshot_audit", FILE)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
audit_snapshot = module.audit_snapshot


def test_size_economics_are_reconciled_without_inferring_leg_wins():
    rows = [
        {"underlying_market_count": 2, "entry_cents": 1000, "payout_cents": 1700},
        {"underlying_market_count": 5, "entry_cents": 1000, "payout_cents": 0},
    ]
    out = audit_snapshot(rows)
    assert out["net_before_fees_cents"] == -300
    assert out["by_market_count"]["2"]["net_before_fees_cents"] == 700
    assert out["by_market_count"]["5"]["net_before_fees_cents"] == -1000
    assert out["positive_before_fees_positions"] == 1
    assert out["leg_outcomes"] == "UNVERIFIED_DO_NOT_INFER"
    assert out["missing_settlement_day_rows"] == 2


def test_nonzero_payout_is_not_necessarily_economic_success():
    out = audit_snapshot([
        {"underlying_market_count": 3, "entry_cents": 1000, "payout_cents": 870},
    ])
    assert out["positive_payout_negative_before_fees_rows"] == 1
    assert out["positive_before_fees_positions"] == 0


def test_total_payout_is_not_the_same_as_settlement_payout():
    out = audit_snapshot([
        {"underlying_market_count": 2, "entry_cents": 1000,
         "payout_cents": 1600, "settlement_payout_cents": 0},
    ])
    assert out["total_vs_settlement_payout_mismatch_rows"] == 1
    assert out["positive_before_fees_positions"] == 1
    assert out["leg_outcomes"] == "UNVERIFIED_DO_NOT_INFER"


def test_day_filter_excludes_unknown_dates_rather_than_claiming_daily_results():
    out = audit_snapshot([
        {"underlying_market_count": 2, "entry_cents": 100, "payout_cents": 200},
        {"underlying_market_count": 2, "entry_cents": 100, "payout_cents": 0,
         "settlement_day": "2026-11-15"},
        {"underlying_market_count": 2, "entry_cents": 100, "payout_cents": 150,
         "settlement_day": "2026-11-14"},
    ], target_day="2026-11-15")
    assert out["included_rows"] == 1
    assert out["excluded_from_target_rows"] == 2
    assert out["missing_settlement_day_rows"] == 1
    assert out["net_before_fees_cents"] == -100
    assert out["scope"] == "SOURCE_REPORTED_DAY_NOT_AUTHENTICATED"


@pytest.mark.parametrize("bad", [
    {"underlying_market_count": True, "entry_cents": 100, "payout_cents": 0},
    {"underlying_market_count": 2, "entry_cents": -1, "payout_cents": 0},
    {"underlying_market_count": 2, "entry_cents": 100, "payout_cents": 0,
     "settlement_day": "11/15/26"},
    {"underlying_market_count": 2, "entry_cents": 100, "payout_cents": 0,
     "market_details": [{}]},
])
def test_reject_invalid_or_incomplete_structured_values(bad):
    with pytest.raises(ValueError):
        audit_snapshot([bad])


def test_duplicate_source_ids_are_rejected_not_counted_twice():
    row = {"underlying_market_count": 2, "entry_cents": 100,
           "payout_cents": 200, "position_id": "stable-a"}
    with pytest.raises(ValueError, match="duplicate source position_id"):
        audit_snapshot([row, row])


def test_calibration_and_execution_are_always_closed():
    out = audit_snapshot([])
    assert out["model_feature_eligible"] is False
    assert out["probability_publishable"] is False
    assert out["can_execute"] is False
    assert out["joint_probabilities"] == "NOT_AVAILABLE_DO_NOT_BACKFILL"


def test_unobserved_fees_prevent_any_fee_adjusted_profit_claim():
    out = audit_snapshot([{
        "underlying_market_count": 2, "entry_cents": 1000,
        "payout_cents": 1001,
    }])
    assert out["economics_basis"] == "PAYOUT_MINUS_ENTRY_BEFORE_UNOBSERVED_FEES"
    assert out["fees_observed"] is False
    assert out["net_before_fees_cents"] == 1
    assert out["positive_before_fees_positions"] == 1
    assert "net_cents" not in out
    assert "profitable_positions" not in out
