"""
test_settlement_reconciliation.py
===================================
15 regression tests for WOW-PATCH-2026-07-10-KALSHI-SETTLEMENT-CLV-RECONCILIATION.

All tests are self-contained: no live Kalshi API calls, no DB, no app.py import.

Shared fixture numbers
----------------------
YES leg:  fill_price=62¢, qty=5, total_fee=8.25¢
          gross_pnl win  = (100−62)×5 = 190¢      net = 190 − 8.25 = 181.75¢
          gross_pnl loss = (0−62)×5   = −310¢     net = −310 − 8.25 = −318.25¢

NO leg:   fill_price=40¢, qty=3, total_fee=5.0¢
          gross_pnl win  = (100−40)×3 = 180¢      net = 180 − 5.0 = 175.0¢
          gross_pnl loss = (0−40)×3   = −120¢     net = −120 − 5.0 = −125.0¢

Run: pytest gate_engine/tests/test_settlement_reconciliation.py -v
"""
from __future__ import annotations

import pytest
import kalshi_engine.settlement_reconciliation as sr
from kalshi_engine.contract_execution_gate import (
    FILL_STATUS_FILLED,
    FILL_STATUS_NO_FILL,
    FILL_STATUS_PARTIAL,
    FILL_STATUS_STALE,
)

# ── Shared base dicts ─────────────────────────────────────────────────────────

_YES_BASE = dict(
    market_ticker="KXMLBGAME-26JUL10-PHI",
    event_id="PHI-ATL-26JUL10",
    side="YES",
    outcome="Phillies",
    fill_status=FILL_STATUS_FILLED,
    calibration_eligible=True,
    hypothetical_fill_price_cents=62.0,
    effective_quantity_filled=5,
    total_fee_cents=8.25,
    settlement_status=sr.SS_SETTLED,
)

_NO_BASE = dict(
    market_ticker="KXMLBGAME-26JUL10-ATL",
    event_id="PHI-ATL-26JUL10",
    side="NO",
    outcome="Braves",
    fill_status=FILL_STATUS_FILLED,
    calibration_eligible=True,
    hypothetical_fill_price_cents=40.0,
    effective_quantity_filled=3,
    total_fee_cents=5.0,
    settlement_status=sr.SS_SETTLED,
)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — FILLED_DRY_RUN YES win
# ─────────────────────────────────────────────────────────────────────────────

def test_01_yes_win_settles_correctly():
    """
    FILLED_DRY_RUN YES win: settlement_value=100, P/L calculated correctly,
    calibration_include=True.
    """
    result = sr.reconcile(
        **_YES_BASE,
        yes_resolved=True,
        closing_price_cents=95.0,
        settled_at="2026-07-10T23:00:00Z",
    )
    assert result["final_result"]              == sr.FR_WIN
    assert result["settlement_value_cents"]    == 100.0
    assert result["gross_pnl_cents"]           == pytest.approx(190.0)
    assert result["net_pnl_after_fees_cents"]  == pytest.approx(181.75)
    assert result["clv_cents"]                 == pytest.approx(33.0)    # 95 − 62
    assert result["calibration_include"]       is True
    assert result["calibration_exclusion_reason"] is None
    assert result["can_execute"]               is False
    assert result["dry_run_only"]              is True
    assert result["settlement_status"]         == sr.SS_SETTLED
    assert sr.BLK_NO_FILL_EXCLUDED            not in result["blockers"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — FILLED_DRY_RUN YES loss
# ─────────────────────────────────────────────────────────────────────────────

def test_02_yes_loss_settles_correctly():
    """
    FILLED_DRY_RUN YES loss: settlement_value=0, P/L negative, calibration_include=True.
    """
    result = sr.reconcile(
        **_YES_BASE,
        yes_resolved=False,
        closing_price_cents=8.0,
    )
    assert result["final_result"]              == sr.FR_LOSS
    assert result["settlement_value_cents"]    == 0.0
    assert result["gross_pnl_cents"]           == pytest.approx(-310.0)
    assert result["net_pnl_after_fees_cents"]  == pytest.approx(-318.25)
    assert result["clv_cents"]                 == pytest.approx(-54.0)   # 8 − 62
    assert result["calibration_include"]       is True
    assert result["can_execute"]               is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — FILLED_DRY_RUN NO win
# ─────────────────────────────────────────────────────────────────────────────

def test_03_no_win_settles_using_no_settlement_value():
    """
    FILLED_DRY_RUN NO win (YES loses → yes_resolved=False).
    settlement_value for NO side = 100.  P/L calculated correctly.
    """
    result = sr.reconcile(
        **_NO_BASE,
        yes_resolved=False,          # NO wins because YES lost
        closing_price_cents=88.0,    # NO closing price (not YES)
    )
    assert result["final_result"]              == sr.FR_WIN
    assert result["settlement_value_cents"]    == 100.0   # NO side won
    assert result["gross_pnl_cents"]           == pytest.approx(180.0)   # (100−40)×3
    assert result["net_pnl_after_fees_cents"]  == pytest.approx(175.0)   # 180 − 5
    assert result["clv_cents"]                 == pytest.approx(48.0)    # 88 − 40
    assert result["calibration_include"]       is True
    assert result["can_execute"]               is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — FILLED_DRY_RUN NO loss
# ─────────────────────────────────────────────────────────────────────────────

def test_04_no_loss_settles_correctly():
    """
    FILLED_DRY_RUN NO loss (YES wins → yes_resolved=True).
    settlement_value for NO side = 0.  P/L negative.
    """
    result = sr.reconcile(
        **_NO_BASE,
        yes_resolved=True,           # YES wins → NO loses
        closing_price_cents=5.0,     # NO closing price
    )
    assert result["final_result"]              == sr.FR_LOSS
    assert result["settlement_value_cents"]    == 0.0     # NO side lost
    assert result["gross_pnl_cents"]           == pytest.approx(-120.0)  # (0−40)×3
    assert result["net_pnl_after_fees_cents"]  == pytest.approx(-125.0)  # −120 − 5
    assert result["clv_cents"]                 == pytest.approx(-35.0)   # 5 − 40
    assert result["calibration_include"]       is True
    assert result["can_execute"]               is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — NO_FILL row
# ─────────────────────────────────────────────────────────────────────────────

def test_05_no_fill_returns_no_fill_excluded():
    """NO_FILL rows must not enter calibration and must return final_result=NO_FILL."""
    result = sr.reconcile(
        market_ticker="KXMLBGAME-26JUL10-PHI",
        fill_status=FILL_STATUS_NO_FILL,
        calibration_eligible=False,
        yes_resolved=True,
        settlement_status=sr.SS_SETTLED,
    )
    assert result["final_result"]       == sr.FR_NO_FILL
    assert result["calibration_include"] is False
    assert result["gross_pnl_cents"]    is None
    assert result["net_pnl_after_fees_cents"] is None
    assert sr.BLK_NO_FILL_EXCLUDED     in result["blockers"]
    assert result["can_execute"]        is False
    assert result["dry_run_only"]       is True


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — PARTIAL_FILL_DRY_RUN row
# ─────────────────────────────────────────────────────────────────────────────

def test_06_partial_fill_excluded_from_calibration():
    """PARTIAL_FILL_DRY_RUN rows must return PARTIAL_FILL_EXCLUDED and calibration_include=False."""
    result = sr.reconcile(
        market_ticker="KXMLBGAME-26JUL10-PHI",
        fill_status=FILL_STATUS_PARTIAL,
        calibration_eligible=False,
        hypothetical_fill_price_cents=62.0,
        effective_quantity_filled=2,
        total_fee_cents=3.30,
        yes_resolved=True,
        settlement_status=sr.SS_SETTLED,
    )
    assert result["final_result"]       == sr.FR_PARTIAL
    assert result["calibration_include"] is False
    assert sr.BLK_PARTIAL_EXCLUDED     in result["blockers"]
    assert result["gross_pnl_cents"]    is None
    assert result["can_execute"]        is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — INVALID_STALE_BOOK row
# ─────────────────────────────────────────────────────────────────────────────

def test_07_invalid_stale_book_excluded():
    """INVALID_STALE_BOOK rows must return final_result=INVALID, calibration_include=False."""
    result = sr.reconcile(
        market_ticker="KXMLBGAME-26JUL10-PHI",
        fill_status=FILL_STATUS_STALE,
        calibration_eligible=False,
        yes_resolved=True,
        settlement_status=sr.SS_SETTLED,
    )
    assert result["final_result"]       == sr.FR_INVALID
    assert result["calibration_include"] is False
    assert sr.BLK_INVALID_BOOK_EXCLUDED in result["blockers"]
    assert result["gross_pnl_cents"]    is None
    assert result["can_execute"]        is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Unsettled market
# ─────────────────────────────────────────────────────────────────────────────

def test_08_unsettled_market_returns_unsettled():
    """
    Markets that are OPEN or CLOSED_UNSETTLED must return UNSETTLED with
    calibration_include=False.  Covers both sub-states.
    """
    for status in (sr.SS_OPEN, sr.SS_CLOSED_UNSETTLED):
        result = sr.reconcile(
            **{**_YES_BASE, "settlement_status": status},
            yes_resolved=None,
        )
        assert result["final_result"]        == sr.FR_UNSETTLED, f"failed for {status}"
        assert result["calibration_include"]  is False
        assert result["settlement_status"]   == status
        assert sr.BLK_NOT_SETTLED           in result["blockers"]
        assert result["gross_pnl_cents"]     is None
        assert result["can_execute"]         is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — Settlement data unobtainable
# ─────────────────────────────────────────────────────────────────────────────

def test_09_settlement_data_unobtainable():
    """
    DATA_UNOBTAINABLE settlement_status must add KALSHI_SETTLEMENT_DATA_UNOBTAINABLE
    and return calibration_include=False.
    """
    result = sr.reconcile(
        **{**_YES_BASE, "settlement_status": sr.SS_UNOBTAINABLE},
        yes_resolved=None,
    )
    assert result["settlement_status"]         == sr.SS_UNOBTAINABLE
    assert result["final_result"]              == sr.FR_INVALID
    assert result["calibration_include"]       is False
    assert sr.BLK_SETTLEMENT_UNOBTAINABLE    in result["blockers"]
    assert result["can_execute"]               is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 10 — Missing closing price blocks CLV but not P&L
# ─────────────────────────────────────────────────────────────────────────────

def test_10_missing_closing_price_blocks_clv_not_pnl():
    """
    When closing_price_cents is None, CLV fields must be None and
    KALSHI_CLV_FIELD_MISSING must be in blockers.  P&L computation
    (gross_pnl, net_pnl) must still complete if settlement value is known.
    calibration_include remains True (CLV is informational, not a gate).
    """
    result = sr.reconcile(
        **_YES_BASE,
        yes_resolved=True,
        closing_price_cents=None,    # <-- no closing price
    )
    assert result["clv_cents"]                 is None
    assert result["clv_percent"]               is None
    assert sr.BLK_CLV_MISSING                in result["blockers"]
    # P&L must still be present
    assert result["gross_pnl_cents"]           == pytest.approx(190.0)
    assert result["net_pnl_after_fees_cents"]  == pytest.approx(181.75)
    assert result["final_result"]              == sr.FR_WIN
    # Missing CLV alone does not disqualify from calibration
    assert result["calibration_include"]       is True
    assert result["can_execute"]               is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 11 — CLV uses YES closing price for YES rows
# ─────────────────────────────────────────────────────────────────────────────

def test_11_clv_yes_uses_yes_closing_price():
    """
    For YES rows, closing_price_cents is the YES closing price.
    clv_cents = YES_closing − hypothetical_fill_price.
    """
    yes_closing = 90.0   # YES contract closed at 90¢
    result = sr.reconcile(
        **_YES_BASE,
        yes_resolved=True,
        closing_price_cents=yes_closing,
    )
    expected_clv = yes_closing - _YES_BASE["hypothetical_fill_price_cents"]  # 90 − 62 = 28
    assert result["clv_cents"]   == pytest.approx(expected_clv)
    assert result["side"]        == "YES"
    assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 12 — CLV uses NO closing price for NO rows
# ─────────────────────────────────────────────────────────────────────────────

def test_12_clv_no_uses_no_closing_price():
    """
    For NO rows, closing_price_cents is the NO closing price (NOT 100 − YES price).
    clv_cents = NO_closing − hypothetical_NO_fill_price.
    """
    no_closing = 75.0    # NO contract closed at 75¢
    result = sr.reconcile(
        **_NO_BASE,
        yes_resolved=False,          # NO wins
        closing_price_cents=no_closing,
    )
    expected_clv = no_closing - _NO_BASE["hypothetical_fill_price_cents"]  # 75 − 40 = 35
    assert result["clv_cents"]   == pytest.approx(expected_clv)
    assert result["side"]        == "NO"
    # Verify it did NOT accidentally use the YES-complement (100 − 75 = 25)
    assert result["clv_cents"]   != pytest.approx(100.0 - no_closing - _NO_BASE["hypothetical_fill_price_cents"])
    assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 13 — Missing fill price / quantity / fee → cannot enter calibration
# ─────────────────────────────────────────────────────────────────────────────

def test_13_missing_price_fields_block_calibration():
    """
    If any of hypothetical_fill_price_cents, effective_quantity_filled, or
    total_fee_cents is missing/zero, calibration_include must be False and
    KALSHI_SETTLEMENT_FIELD_MISSING must appear in blockers.
    """
    combos = [
        # missing fill price
        {"hypothetical_fill_price_cents": None, "effective_quantity_filled": 5, "total_fee_cents": 8.25},
        # zero quantity
        {"hypothetical_fill_price_cents": 62.0, "effective_quantity_filled": 0, "total_fee_cents": 8.25},
        # missing fee
        {"hypothetical_fill_price_cents": 62.0, "effective_quantity_filled": 5, "total_fee_cents": None},
    ]
    for override in combos:
        result = sr.reconcile(
            market_ticker="KXMLBGAME-26JUL10-PHI",
            event_id="PHI-ATL-26JUL10",
            side="YES",
            fill_status=FILL_STATUS_FILLED,
            calibration_eligible=True,
            settlement_status=sr.SS_SETTLED,
            yes_resolved=True,
            closing_price_cents=90.0,
            **override,
        )
        assert result["calibration_include"]  is False, f"calibration_include should be False for {override}"
        assert sr.BLK_FIELD_MISSING          in result["blockers"], f"blocker missing for {override}"
        assert result["can_execute"]          is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 14 — can_execute is False in all reconciliation outputs
# ─────────────────────────────────────────────────────────────────────────────

def test_14_can_execute_false_in_all_paths():
    """
    can_execute must be False unconditionally across every fill_status and
    settlement_status path.  No reconciliation output may ever set it True.
    """
    scenarios = [
        # (fill_status, settlement_status, yes_resolved)
        (FILL_STATUS_FILLED,  sr.SS_SETTLED,           True),
        (FILL_STATUS_FILLED,  sr.SS_SETTLED,           False),
        (FILL_STATUS_FILLED,  sr.SS_OPEN,              None),
        (FILL_STATUS_FILLED,  sr.SS_CLOSED_UNSETTLED,  None),
        (FILL_STATUS_FILLED,  sr.SS_VOID,              None),
        (FILL_STATUS_FILLED,  sr.SS_UNOBTAINABLE,      None),
        (FILL_STATUS_NO_FILL, sr.SS_SETTLED,           True),
        (FILL_STATUS_PARTIAL, sr.SS_SETTLED,           True),
        (FILL_STATUS_STALE,   sr.SS_SETTLED,           True),
    ]
    for fill_st, settle_st, resolved in scenarios:
        result = sr.reconcile(
            market_ticker="KXMLBGAME-26JUL10-PHI",
            fill_status=fill_st,
            calibration_eligible=(fill_st == FILL_STATUS_FILLED),
            hypothetical_fill_price_cents=62.0,
            effective_quantity_filled=5,
            total_fee_cents=8.25,
            settlement_status=settle_st,
            yes_resolved=resolved,
        )
        assert result["can_execute"] is False, (
            f"can_execute was True for fill_status={fill_st}, "
            f"settlement_status={settle_st}, yes_resolved={resolved}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 15 — dry_run_only is True in all reconciliation outputs
# ─────────────────────────────────────────────────────────────────────────────

def test_15_dry_run_only_true_in_all_paths():
    """
    dry_run_only must be True unconditionally.  Every reconciliation output
    must preserve the DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS rule.
    """
    for fill_st in (FILL_STATUS_FILLED, FILL_STATUS_NO_FILL, FILL_STATUS_PARTIAL, FILL_STATUS_STALE):
        result = sr.reconcile(
            market_ticker="KXMLBGAME-26JUL10-PHI",
            fill_status=fill_st,
            hypothetical_fill_price_cents=62.0,
            effective_quantity_filled=5,
            total_fee_cents=8.25,
            settlement_status=sr.SS_SETTLED,
            yes_resolved=True,
        )
        assert result["dry_run_only"]   is True, f"dry_run_only was False for fill_status={fill_st}"
        assert result["execution_rule"] == "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
        assert result["can_execute"]    is False
