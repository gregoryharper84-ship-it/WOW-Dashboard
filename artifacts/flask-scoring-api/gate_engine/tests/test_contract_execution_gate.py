"""
test_contract_execution_gate.py
================================
13 regression tests for WOW-PATCH-2026-07-09-KALSHI-CONTRACT-EXECUTION-OVERLAY.

Tests are self-contained: they construct minimal normalized_book dicts directly
(in decimal 0.0-1.0 form, matching orderbook_normalizer.normalize() output) so
no live Kalshi API calls are required.

Run: pytest gate_engine/tests/test_contract_execution_gate.py -v
"""
from __future__ import annotations

import datetime
import pytest

from kalshi_engine import contract_execution_gate as ceg


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fresh_ts(seconds_ago: float = 30.0) -> str:
    """ISO-8601 UTC timestamp N seconds in the past."""
    ts = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
        seconds=seconds_ago
    )
    return ts.isoformat()


def _book(
    yes_bid: float = 0.60,
    no_bid:  float = 0.38,
    depth:   int   = 150,
) -> dict:
    """
    Minimal normalized_book (decimal 0.0–1.0).
    yes_bid=0.60, no_bid=0.38  →  yes_ask=0.62, no_ask=0.40  →  spread=2¢.
    """
    return {
        "best_yes_bid":   yes_bid,
        "best_no_bid":    no_bid,
        "depth_at_price": depth,
        "liquidity_grade": "B",
    }


# Common kwargs for the "all-good" base scenario.
# Override individual keys in each test as needed.
_BASE: dict = dict(
    series_ticker="KXMLBGAME",
    market_ticker="KXMLBGAME-26JUL10-PHI",
    event_id="PHI-ATL-26JUL10",
    side="YES",
    outcome="Phillies",
    consensus_no_vig_probability=0.620,
    kalshi_orderbook_source="direct_api",
    trading_active=True,
    quantity=1,
    slippage_buffer=0.005,
    fee_multiplier=1.0,
)

# model_probability that comfortably clears the MLB 1.5% edge floor.
# yes_bid=0.60, no_bid=0.38 → yes_ask=0.62 (62¢)
# fee(0.62) = 0.07 × 0.62 × 0.38 × 100 = 1.6492¢
# max_buy   = 0.80 × 100 − 1.5 − 1.6492 − 0.5 = 76.35¢  ≫ 62¢  ✓
_STRONG_MODEL_PROB = 0.80


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Happy path: all gates pass → LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN
# ─────────────────────────────────────────────────────────────────────────────

def test_01_happy_path_returns_playable_limit_only():
    """Direct API + fresh book + clear edge + ask ≤ max_buy + depth ok."""
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB},
        normalized_book=_book(yes_bid=0.60, no_bid=0.38, depth=200),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert result["final_label"] == "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"
    assert result["can_execute"]  is False
    assert result["dry_run_only"] is True
    assert result["would_fill"]   is True
    assert "KALSHI_MAX_BUY_PRICE_FAIL" not in result["blockers"]
    assert "KALSHI_MARKET_ORDER_BANNED" in result["blockers"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Non-direct-api source caps at LLP_WATCH or lower
# ─────────────────────────────────────────────────────────────────────────────

def test_02_screenshot_or_caller_supplied_source_caps_at_watch():
    """Screenshot / operator / synthetic orderbook → LLP_WATCH (or LLP_REJECT)."""
    for bad_source in ("caller_supplied", "screenshot", "operator", "no_ticker", "fetch_failed"):
        result = ceg.evaluate(
            **{**_BASE, "model_probability": _STRONG_MODEL_PROB,
               "kalshi_orderbook_source": bad_source},
            normalized_book=_book(),
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert result["final_label"] in ("LLP_WATCH", "LLP_REJECT"), (
            f"source='{bad_source}' gave unexpected label '{result['final_label']}'"
        )
        assert "KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API" in result["blockers"]
        assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Stale orderbook → KALSHI_ORDERBOOK_STALE
# ─────────────────────────────────────────────────────────────────────────────

def test_03_stale_orderbook_adds_blocker():
    """Orderbook age > 600 s → KALSHI_ORDERBOOK_STALE, label not PLAYABLE_LIMIT_ONLY."""
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB},
        normalized_book=_book(),
        orderbook_fetched_at=_fresh_ts(700),   # 700 s > 600 s stale threshold
    )
    assert "KALSHI_ORDERBOOK_STALE" in result["blockers"]
    assert result["final_label"] in ("LLP_WATCH", "LLP_REJECT")
    assert result["can_execute"] is False


def test_03b_missing_timestamp_is_stale():
    """No orderbook_fetched_at → treated as indefinitely stale."""
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB},
        normalized_book=_book(),
        orderbook_fetched_at=None,
    )
    assert "KALSHI_ORDERBOOK_STALE" in result["blockers"]
    assert result["orderbook_age_seconds"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Empty orderbook → KALSHI_EMPTY_ORDERBOOK + LLP_REJECT
# ─────────────────────────────────────────────────────────────────────────────

def test_04_empty_orderbook_hard_rejects():
    """No YES or NO bids → KALSHI_EMPTY_ORDERBOOK, LLP_REJECT."""
    for empty_book in [
        {"best_yes_bid": None, "best_no_bid": None, "depth_at_price": 0},
        {},
        None,
    ]:
        result = ceg.evaluate(
            **{**_BASE, "model_probability": _STRONG_MODEL_PROB},
            normalized_book=empty_book,
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert "KALSHI_EMPTY_ORDERBOOK" in result["blockers"], (
            f"empty_book={empty_book!r} did not trigger KALSHI_EMPTY_ORDERBOOK"
        )
        assert result["final_label"] == "LLP_REJECT"
        assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Non-trading market → KALSHI_MARKET_NOT_TRADING + LLP_REJECT
# ─────────────────────────────────────────────────────────────────────────────

def test_05_market_not_trading_hard_rejects():
    """trading_active=False → KALSHI_MARKET_NOT_TRADING, LLP_REJECT."""
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB, "trading_active": False},
        normalized_book=_book(),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert "KALSHI_MARKET_NOT_TRADING" in result["blockers"]
    assert result["final_label"] == "LLP_REJECT"
    assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Missing fee model → KALSHI_FEE_MODEL_MISSING
# ─────────────────────────────────────────────────────────────────────────────

def test_06_no_model_probability_triggers_fee_model_missing():
    """model_probability=None → fee cannot be computed → KALSHI_FEE_MODEL_MISSING."""
    result = ceg.evaluate(
        **{**_BASE, "model_probability": None},
        normalized_book=_book(),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert "KALSHI_FEE_MODEL_MISSING" in result["blockers"]
    assert result["estimated_fee_per_contract_cents"] is None
    assert result["max_buy_price_cents"] is None
    assert result["final_label"] in ("LLP_WATCH", "LLP_REJECT")
    assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — Ask above max_buy_price → KALSHI_MAX_BUY_PRICE_FAIL + LLP_REJECT
# ─────────────────────────────────────────────────────────────────────────────

def test_07_ask_above_max_buy_price_hard_rejects():
    """
    yes_bid=0.48, no_bid=0.49 → yes_ask=51¢
    model=0.505, edge_floor=1.5%, fee≈1.75¢, slippage=0.5¢
    max_buy = 50.5 − 1.5 − 1.748 − 0.5 ≈ 46.75¢  <  51¢  → FAIL
    """
    result = ceg.evaluate(
        **{**_BASE, "model_probability": 0.505},
        normalized_book=_book(yes_bid=0.48, no_bid=0.49, depth=100),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert "KALSHI_MAX_BUY_PRICE_FAIL" in result["blockers"]
    assert result["final_label"] == "LLP_REJECT"
    assert result["would_fill"]  is False
    assert result["would_place_limit_at_cents"] is None
    assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 8 — Insufficient depth → KALSHI_DEPTH_INSUFFICIENT
# ─────────────────────────────────────────────────────────────────────────────

def test_08_insufficient_depth_caps_at_watch():
    """
    depth=10, quantity=500 → KALSHI_DEPTH_INSUFFICIENT.
    Depth is a soft cap (can improve) → LLP_WATCH, not LLP_REJECT.
    """
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB, "quantity": 500},
        normalized_book=_book(depth=10),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert "KALSHI_DEPTH_INSUFFICIENT" in result["blockers"]
    assert result["final_label"] in ("LLP_WATCH", "LLP_REJECT")
    assert result["can_execute"] is False


def test_08b_depth_zero_is_insufficient():
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB, "quantity": 1},
        normalized_book=_book(depth=0),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert "KALSHI_DEPTH_INSUFFICIENT" in result["blockers"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 9 — Market-order policy: KALSHI_MARKET_ORDER_BANNED always present
# ─────────────────────────────────────────────────────────────────────────────

def test_09_market_order_banned_is_always_present():
    """
    KALSHI_MARKET_ORDER_BANNED is a permanent policy tag on every response,
    and execution_mode is always LIMIT_ONLY_DRY_RUN.
    """
    scenarios = [
        {"model_probability": _STRONG_MODEL_PROB},   # happy path
        {"model_probability": None},                  # no model
        {"trading_active": False},                    # market closed
        {"kalshi_orderbook_source": "screenshot"},    # bad source
    ]
    for override in scenarios:
        result = ceg.evaluate(
            **{**_BASE, **override},
            normalized_book=_book(),
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert "KALSHI_MARKET_ORDER_BANNED" in result["blockers"], (
            f"KALSHI_MARKET_ORDER_BANNED missing for override={override}"
        )
        assert result["execution_mode"] == "LIMIT_ONLY_DRY_RUN", (
            f"execution_mode was not LIMIT_ONLY_DRY_RUN for override={override}"
        )
        assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 10 — YES / NO ask reconstruction arithmetic
# ─────────────────────────────────────────────────────────────────────────────

def test_10_ask_reconstruction_arithmetic():
    """
    Input  : yes_bid=0.48, no_bid=0.49  (decimal)
    Derived: yes_ask = 100 − 49 = 51¢
             no_ask  = 100 − 48 = 52¢
    """
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB},
        normalized_book={
            "best_yes_bid":   0.48,
            "best_no_bid":    0.49,
            "depth_at_price": 100,
        },
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert result["best_yes_bid_cents"]  == pytest.approx(48.0, abs=0.01)
    assert result["best_no_bid_cents"]   == pytest.approx(49.0, abs=0.01)
    assert result["best_yes_ask_cents"]  == pytest.approx(51.0, abs=0.01)   # 100 − 49
    assert result["best_no_ask_cents"]   == pytest.approx(52.0, abs=0.01)   # 100 − 48
    assert result["spread_cents"]        == pytest.approx( 3.0, abs=0.01)   # 51 − 48


# ─────────────────────────────────────────────────────────────────────────────
# Test 11 — Final label is never LLP_APPROVED or plain LLP_PLAYABLE
# ─────────────────────────────────────────────────────────────────────────────

def test_11_label_never_approved_or_plain_playable():
    """
    The contract gate may only emit:
      LLP_REJECT | LLP_WATCH | LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN
    It must NEVER emit LLP_APPROVED, plain LLP_PLAYABLE, or
    the bare LLP_PLAYABLE_LIMIT_ONLY (without _DRY_RUN suffix).
    """
    _ALLOWED = {"LLP_REJECT", "LLP_WATCH", "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"}
    scenarios = [
        {"model_probability": _STRONG_MODEL_PROB},   # all gates pass
        {"model_probability": 0.99},                  # very strong model
        {"model_probability": None},                  # no model
        {"trading_active": False},                    # closed market
        {"kalshi_orderbook_source": "screenshot"},    # bad source
        {"model_probability": 0.505},                 # thin edge
    ]
    for override in scenarios:
        result = ceg.evaluate(
            **{**_BASE, **override},
            normalized_book=_book(yes_bid=0.48, no_bid=0.49),
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert result["final_label"] in _ALLOWED, (
            f"Unexpected label '{result['final_label']}' for override={override}"
        )
        assert result["final_label"] not in (
            "LLP_APPROVED", "LLP_PLAYABLE", "LLP_PLAYABLE_LIMIT_ONLY"
        ), (
            f"Forbidden label '{result['final_label']}' emitted for override={override}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 12 — can_execute is always False
# ─────────────────────────────────────────────────────────────────────────────

def test_12_can_execute_always_false():
    """can_execute=False unconditionally — even when all gates pass."""
    # Use very strong model + generous book to maximise chance of PLAYABLE_LIMIT_ONLY
    result = ceg.evaluate(
        **{**_BASE, "model_probability": 0.99},
        normalized_book=_book(yes_bid=0.60, no_bid=0.38, depth=1000),
        orderbook_fetched_at=_fresh_ts(10),
    )
    assert result["can_execute"] is False
    # Also verify the label IS playable so we know can_execute isn't just a side-effect
    # of a reject/watch path
    assert result["final_label"] == "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"


def test_12b_can_execute_false_on_every_path():
    """Exhaustive: can_execute=False regardless of gate outcome."""
    scenarios = [
        {"model_probability": 0.99,   "trading_active": True},   # would be PLAYABLE
        {"model_probability": 0.50,   "trading_active": True},   # likely REJECT (price fail)
        {"model_probability": None,   "trading_active": True},   # fee missing
        {"model_probability": 0.99,   "trading_active": False},  # market closed
        {"kalshi_orderbook_source": "caller_supplied"},           # bad source
    ]
    for override in scenarios:
        result = ceg.evaluate(
            **{**_BASE, **override},
            normalized_book=_book(),
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert result["can_execute"] is False, (
            f"can_execute was not False for override={override}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 13 — dry_run_only is always True
# ─────────────────────────────────────────────────────────────────────────────

def test_13_dry_run_only_always_true():
    """dry_run_only=True and execution_rule correct on every response."""
    scenarios = [
        {"model_probability": 0.99},
        {"model_probability": None},
        {"trading_active": False},
        {"kalshi_orderbook_source": "fetch_failed"},
        {},   # bare base
    ]
    for override in scenarios:
        result = ceg.evaluate(
            **{**_BASE, **override},
            normalized_book=_book(),
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert result["dry_run_only"] is True, (
            f"dry_run_only was not True for override={override}"
        )
        assert result["execution_rule"] == (
            "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
        ), f"execution_rule wrong for override={override}"


# ─────────────────────────────────────────────────────────────────────────────
# Test 14 — normalize_label() converts bare LLP_PLAYABLE_LIMIT_ONLY → canonical
# ─────────────────────────────────────────────────────────────────────────────

def test_14_normalize_label_converts_bare_to_dry_run():
    """
    If a renderer or legacy path produces the bare 'LLP_PLAYABLE_LIMIT_ONLY'
    label (without the _DRY_RUN suffix), normalize_label() must silently
    upgrade it to 'LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN' while preserving
    can_execute=False semantics.

    This guard ensures label drift between older callers and the canonical
    gate is caught and corrected automatically.
    """
    _CANONICAL = "LLP_PLAYABLE_LIMIT_ONLY_DRY_RUN"

    # Core: bare label is normalized
    assert ceg.normalize_label("LLP_PLAYABLE_LIMIT_ONLY") == _CANONICAL

    # Idempotent: canonical label passes through unchanged
    assert ceg.normalize_label(_CANONICAL) == _CANONICAL

    # Other labels pass through unchanged
    assert ceg.normalize_label("LLP_REJECT")  == "LLP_REJECT"
    assert ceg.normalize_label("LLP_WATCH")   == "LLP_WATCH"
    assert ceg.normalize_label("GATE_ERROR")  == "GATE_ERROR"

    # Verify the gate itself never emits the bare label directly
    result = ceg.evaluate(
        **{**_BASE, "model_probability": 0.99},
        normalized_book=_book(yes_bid=0.60, no_bid=0.38, depth=1000),
        orderbook_fetched_at=_fresh_ts(10),
    )
    assert result["final_label"] == _CANONICAL, (
        f"Gate emitted '{result['final_label']}' instead of canonical '{_CANONICAL}'"
    )
    assert result["can_execute"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Tests 15–19 — Dry-run fill ledger
# ─────────────────────────────────────────────────────────────────────────────

def test_15_filled_dry_run_ask_at_or_below_max_buy_full_depth():
    """
    Ask ≤ max_buy_price AND depth ≥ quantity → FILLED_DRY_RUN.
    hypothetical_fill_price_cents == executable ask.
    total_fee_cents == fee_per_contract × quantity.
    calibration_eligible == True.
    can_execute == False unconditionally.
    """
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB, "quantity": 5},
        normalized_book=_book(yes_bid=0.60, no_bid=0.38, depth=200),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert result["fill_status"]               == ceg.FILL_STATUS_FILLED
    assert result["effective_quantity_filled"] == 5
    assert result["hypothetical_fill_price_cents"] == result["executable_ask_at_decision_cents"]
    assert result["total_fee_cents"] is not None
    assert result["total_fee_cents"]           == pytest.approx(
        result["fee_per_contract_cents"] * 5, rel=1e-6
    )
    assert result["calibration_eligible"]      is True
    assert result["can_execute"]               is False
    # Settlement-time placeholders must all be None at decision time
    for field in ("closing_price_cents", "settlement_value_cents",
                  "gross_pnl_cents", "net_pnl_after_fees_cents",
                  "clv_cents", "final_result"):
        assert result[field] is None, f"{field} should be None at decision time"


def test_16_no_fill_ask_above_max_buy_price():
    """
    Ask > max_buy_price → LLP_REJECT and fill_status == NO_FILL.
    hypothetical_fill_price_cents must be None.
    total_fee_cents must be None.
    calibration_eligible == False (NO_FILL rows must not enter ROI calc).
    """
    # Force ask above max_buy by using a very low model probability
    result = ceg.evaluate(
        **{**_BASE, "model_probability": 0.40},   # ask ~62¢ but max_buy well below
        normalized_book=_book(yes_bid=0.60, no_bid=0.38, depth=200),
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert "KALSHI_MAX_BUY_PRICE_FAIL"          in result["blockers"]
    assert result["fill_status"]               == ceg.FILL_STATUS_NO_FILL
    assert result["hypothetical_fill_price_cents"] is None
    assert result["total_fee_cents"]           is None
    assert result["effective_quantity_filled"] == 0
    assert result["calibration_eligible"]      is False
    assert result["can_execute"]               is False


def test_17_partial_fill_dry_run_insufficient_depth():
    """
    Ask ≤ max_buy_price BUT available depth < quantity → PARTIAL_FILL_DRY_RUN.
    effective_quantity_filled == available_depth.
    total_fee_cents == fee_per_contract × available_depth.
    calibration_eligible == False.
    """
    result = ceg.evaluate(
        **{**_BASE, "model_probability": _STRONG_MODEL_PROB, "quantity": 10},
        normalized_book=_book(yes_bid=0.60, no_bid=0.38, depth=2),  # only 2 available
        orderbook_fetched_at=_fresh_ts(30),
    )
    assert "KALSHI_DEPTH_INSUFFICIENT"         in result["blockers"]
    assert result["fill_status"]               == ceg.FILL_STATUS_PARTIAL
    assert result["effective_quantity_filled"] == 2
    assert result["hypothetical_fill_price_cents"] == result["executable_ask_at_decision_cents"]
    assert result["total_fee_cents"]           == pytest.approx(
        result["fee_per_contract_cents"] * 2, rel=1e-6
    )
    assert result["calibration_eligible"]      is False
    assert result["can_execute"]               is False


def test_18_no_fill_rows_excluded_from_calibration():
    """
    Exhaustive: any row with fill_status != FILLED_DRY_RUN must have
    calibration_eligible == False, regardless of the final_label path.
    This is the guard that prevents NO_FILL / PARTIAL / STALE rows from
    entering model ROI or hit-rate calculations after settlement.
    """
    scenarios = [
        # (override kwargs, book kwargs, expected fill_status)
        ({"model_probability": 0.40}, {"yes_bid": 0.60, "no_bid": 0.38, "depth": 200},
         ceg.FILL_STATUS_NO_FILL),                              # ask > max_buy
        ({"trading_active": False}, {}, ceg.FILL_STATUS_NO_FILL),   # hard reject
        ({"kalshi_orderbook_source": "screenshot"}, {},
         ceg.FILL_STATUS_STALE),                                # stale wins over partial
        ({"model_probability": _STRONG_MODEL_PROB},
         {"yes_bid": 0.60, "no_bid": 0.38, "depth": 1},
         ceg.FILL_STATUS_PARTIAL),                              # depth < qty=5
    ]
    for override, book_kw, expected_status in scenarios:
        result = ceg.evaluate(
            **{**_BASE, "quantity": 5, **override},
            normalized_book=_book(**book_kw) if book_kw else _book(),
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert result["fill_status"]          == expected_status, (
            f"Expected {expected_status}, got {result['fill_status']} for {override}"
        )
        assert result["calibration_eligible"] is False, (
            f"calibration_eligible must be False for {expected_status} (override={override})"
        )
        assert result["can_execute"]          is False


def test_19_can_execute_false_on_every_ledger_row():
    """
    can_execute must remain False for EVERY fill_status path.
    No ledger entry — even a clean FILLED_DRY_RUN — may set can_execute=True.
    """
    scenarios = [
        # FILLED_DRY_RUN
        ({"model_probability": _STRONG_MODEL_PROB},
         {"yes_bid": 0.60, "no_bid": 0.38, "depth": 100}),
        # PARTIAL_FILL_DRY_RUN
        ({"model_probability": _STRONG_MODEL_PROB},
         {"yes_bid": 0.60, "no_bid": 0.38, "depth": 1}),
        # NO_FILL via max_buy
        ({"model_probability": 0.40},
         {"yes_bid": 0.60, "no_bid": 0.38, "depth": 200}),
        # NO_FILL via reject
        ({"trading_active": False}, {}),
        # INVALID_STALE_BOOK
        ({"kalshi_orderbook_source": "screenshot"}, {}),
    ]
    for override, book_kw in scenarios:
        result = ceg.evaluate(
            **{**_BASE, "quantity": 5, **override},
            normalized_book=_book(**book_kw) if book_kw else _book(),
            orderbook_fetched_at=_fresh_ts(30),
        )
        assert result["can_execute"] is False, (
            f"can_execute was True for fill_status={result['fill_status']} "
            f"override={override}"
        )
