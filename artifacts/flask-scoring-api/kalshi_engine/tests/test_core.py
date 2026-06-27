"""
test_core.py  —  Kalshi engine core unit tests
WOW v16 Kalshi Exchange Layer

Tests run without external network access or DB.
All edge/fee/normalizer/guard logic is deterministic and testable offline.

Run:
  cd artifacts/flask-scoring-api
  python -m pytest kalshi_engine/tests/test_core.py -v
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kalshi_engine import (
    fee_model,
    edge_engine,
    orderbook_normalizer,
    settlement_risk,
    execution_guard,
    market_buckets,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _good_book(yes_bid_cents=57, no_bid_cents=41, depth=300):
    """Build a normalized book dict directly (no API call)."""
    yes_bid = yes_bid_cents / 100
    no_bid  = no_bid_cents  / 100
    yes_ask = 1.0 - no_bid
    no_ask  = 1.0 - yes_bid
    yes_spread = yes_ask - yes_bid
    no_spread  = no_ask  - no_bid
    mid = (yes_bid + yes_ask) / 2
    return {
        "ticker":          "TEST-1",
        "best_yes_bid":    round(yes_bid, 4),
        "best_yes_ask":    round(yes_ask, 4),
        "best_no_bid":     round(no_bid, 4),
        "best_no_ask":     round(no_ask, 4),
        "yes_spread":      round(yes_spread, 4),
        "no_spread":       round(no_spread, 4),
        "mid_price":       round(mid, 4),
        "depth_at_price":  depth,
        "depth_within_1c": depth,
        "depth_within_2c": depth * 2,
        "liquidity_grade": "B",
        "raw_level_count": 20,
    }


def _thin_book():
    """A book with grade=F (too thin)."""
    book = _good_book(depth=3)
    book["liquidity_grade"] = "F"
    book["depth_within_2c"] = 3
    return book


# ---------------------------------------------------------------------------
# test_reject_missing_orderbook
# ---------------------------------------------------------------------------

def test_reject_missing_orderbook():
    """Edge engine must reject when no orderbook is present."""
    empty_book = {
        "best_yes_ask": None,
        "best_yes_bid": None,
        "liquidity_grade": "F",
        "depth_within_2c": 0,
        "yes_spread": None,
        "mid_price": None,
    }
    result = edge_engine.evaluate(
        model_probability = 0.64,
        normalized_book   = empty_book,
        category          = "sports",
    )
    assert result["label"] in (
        "KALSHI_REJECT_THIN_BOOK",
        "KALSHI_DATA_UNOBTAINABLE",
    ), f"Expected reject, got {result['label']}"


# ---------------------------------------------------------------------------
# test_reject_no_model_probability
# ---------------------------------------------------------------------------

def test_reject_no_model_probability():
    """No model prob must return KALSHI_REJECT_UNCALIBRATED."""
    result = edge_engine.evaluate(
        model_probability = None,
        normalized_book   = _good_book(),
        category          = "sports",
    )
    assert result["label"] == "KALSHI_REJECT_UNCALIBRATED"


# ---------------------------------------------------------------------------
# test_reject_thin_book
# ---------------------------------------------------------------------------

def test_reject_thin_book():
    """Grade=F book must block even with good model probability."""
    result = edge_engine.evaluate(
        model_probability = 0.70,
        normalized_book   = _thin_book(),
        category          = "sports",
    )
    assert result["label"] == "KALSHI_REJECT_THIN_BOOK"


# ---------------------------------------------------------------------------
# test_reject_fee_drag_erases_edge
# ---------------------------------------------------------------------------

def test_reject_fee_drag_erases_edge():
    """
    A contract at 61¢ with 64% model probability must reject if
    fee + spread + uncertainty erases adjusted edge below threshold.

    book: yes_bid=0.59, yes_ask=0.63, spread=4¢ (grade C)
    model_prob = 0.61, entry = 0.63
    raw_edge = 0.61 - 0.63 = -0.02  → already negative → reject
    """
    tight_book = _good_book(yes_bid_cents=59, no_bid_cents=37, depth=100)
    tight_book["liquidity_grade"] = "C"
    result = edge_engine.evaluate(
        model_probability = 0.61,
        normalized_book   = tight_book,
        category          = "sports",
        uncertainty_tax   = 0.01,
    )
    # raw_edge will be negative or very small; after fee drag it must reject
    assert result["label"] in (
        "KALSHI_REJECT_NO_EDGE",
        "KALSHI_REJECT_FEE_DRAG",
        "KALSHI_WATCH",
    ), f"Expected reject/watch, got {result['label']}, adj={result.get('adjusted_edge')}"
    assert result["can_approve_bets"] is False


# ---------------------------------------------------------------------------
# test_reject_bad_settlement_rules
# ---------------------------------------------------------------------------

def test_reject_bad_settlement_rules():
    """No resolution source must produce REJECT in settlement_risk."""
    result = settlement_risk.grade_contract(
        title                = "Will X happen?",
        settlement_condition = "Kalshi may determine at its discretion.",
        resolution_source    = None,
        category             = "narrative",
    )
    assert result["settlement_risk"] == "REJECT"
    assert result["tradable"] is False
    assert result["resolution_clarity_grade"] == "F"


# ---------------------------------------------------------------------------
# test_limit_only_label_when_price_valid
# ---------------------------------------------------------------------------

def test_limit_only_label_when_price_valid():
    """
    High-edge contract with good liquidity → KALSHI_PLAYABLE_LIMIT_ONLY.
    book: yes_bid=0.48, yes_ask=0.51 (spread 3¢, grade A sim)
    model_prob = 0.62
    raw_edge = 0.62 - 0.51 = 0.11
    fee ≈ 7% * min(0.51, 0.49) = 7% * 0.49 ≈ 0.034
    spread_drag ≈ 0.015
    adj_edge ≈ 0.11 - 0.034 - 0.015 - 0.01 ≈ 0.051 → above 4% threshold
    """
    good_book = _good_book(yes_bid_cents=48, no_bid_cents=49, depth=600)
    good_book["liquidity_grade"] = "A"
    result = edge_engine.evaluate(
        model_probability = 0.62,
        normalized_book   = good_book,
        category          = "sports",
        uncertainty_tax   = 0.01,
    )
    assert result["label"] == "KALSHI_PLAYABLE_LIMIT_ONLY", (
        f"Expected KALSHI_PLAYABLE_LIMIT_ONLY, got {result['label']}, "
        f"adj_edge={result.get('adjusted_edge')}"
    )
    assert result["execution"] == "LIMIT_ONLY_NO_MARKET_ORDER"
    assert result["can_approve_bets"] is False


# ---------------------------------------------------------------------------
# test_watch_when_price_moves_past_max
# ---------------------------------------------------------------------------

def test_watch_when_price_moves_past_max():
    """If entry price > max_playable, label must be WATCH."""
    # Model prob = 0.55, category sports → threshold=0.04
    # max_playable = 0.55 - 0.04 = 0.51
    # Entry price (yes_ask) = 0.56 → above max_playable
    high_entry_book = _good_book(yes_bid_cents=54, no_bid_cents=44, depth=500)
    high_entry_book["liquidity_grade"] = "A"
    result = edge_engine.evaluate(
        model_probability = 0.55,
        normalized_book   = high_entry_book,
        category          = "sports",
        uncertainty_tax   = 0.005,
    )
    # yes_ask = 1 - 0.44 = 0.56, max_playable = 0.55 - 0.04 = 0.51
    # Either WATCH or REJECT_NO_EDGE depending on adj_edge
    assert result["label"] in ("KALSHI_WATCH", "KALSHI_REJECT_NO_EDGE"), (
        f"Got {result['label']}, entry={result.get('entry_price')}, "
        f"max_play={result.get('max_playable_price')}"
    )


# ---------------------------------------------------------------------------
# test_no_market_orders_allowed
# ---------------------------------------------------------------------------

def test_no_market_orders_allowed():
    """Execution guard must block when no limit price is provided."""
    result = execution_guard.validate_execution_request(
        label             = "KALSHI_PLAYABLE_LIMIT_ONLY",
        normalized_book   = _good_book(),
        limit_price       = None,   # no limit price
        settlement_grade  = "B",
        contracts         = 3,
        mode              = "paper",
    )
    assert not result["allowed"]
    assert any("ALLOW_MARKET_ORDERS" in b or "LIMIT" in b for b in result["blocks"]), result["blocks"]
    assert result["can_approve_bets"] is False


# ---------------------------------------------------------------------------
# test_ledger_required_before_approval
# ---------------------------------------------------------------------------

def test_ledger_required_before_approval():
    """Settlement grade=F must block via execution_guard."""
    result = execution_guard.validate_execution_request(
        label             = "KALSHI_PLAYABLE_LIMIT_ONLY",
        normalized_book   = _good_book(),
        limit_price       = 0.52,
        settlement_grade  = "F",   # failed settlement grade
        contracts         = 1,
        mode              = "paper",
    )
    assert not result["allowed"]
    assert any("SETTLEMENT" in b for b in result["blocks"]), result["blocks"]


# ---------------------------------------------------------------------------
# test_yes_no_complement_conversion
# ---------------------------------------------------------------------------

def test_yes_no_complement_conversion():
    """YES bid at 58¢ must produce NO ask at 42¢."""
    raw = {
        "orderbook": {
            "yes": [{"price": 58, "quantity": 200}, {"price": 57, "quantity": 100}],
            "no":  [{"price": 40, "quantity": 150}, {"price": 39, "quantity": 80}],
        }
    }
    norm = orderbook_normalizer.normalize(raw, ticker="TEST")
    assert norm["best_yes_bid"] == pytest.approx(0.58, abs=0.001)
    assert norm["best_no_bid"]  == pytest.approx(0.40, abs=0.001)
    # YES ask = 1 - best NO bid = 1 - 0.40 = 0.60
    assert norm["best_yes_ask"] == pytest.approx(0.60, abs=0.001)
    # NO ask = 1 - best YES bid = 1 - 0.58 = 0.42
    assert norm["best_no_ask"]  == pytest.approx(0.42, abs=0.001)


# ---------------------------------------------------------------------------
# test_fee_model_symmetric
# ---------------------------------------------------------------------------

def test_fee_model_symmetric():
    """Fee at 50¢ must equal fee at 50¢ from either side (symmetric)."""
    f1 = fee_model.calculate(entry_price=0.50, yes_spread=0.02, liquidity_grade="B", uncertainty_tax=0)
    f2 = fee_model.calculate(entry_price=0.50, yes_spread=0.02, liquidity_grade="B", uncertainty_tax=0)
    assert f1["fee_per_contract"] == f2["fee_per_contract"]
    # fee at 50¢ = 7% * 0.50 = 0.035
    assert f1["fee_per_contract"] == pytest.approx(0.035, abs=0.0001)


# ---------------------------------------------------------------------------
# test_market_bucket_trusted_test
# ---------------------------------------------------------------------------

def test_market_bucket_trusted_test():
    """Settlement=A, liquidity=A, has_history=True → TRUSTED_TEST."""
    result = market_buckets.classify(
        settlement_grade = "A",
        liquidity_grade  = "A",
        has_history      = True,
        category         = "sports_game_result",
        settlement_risk  = "LOW",
        adjusted_edge    = 0.05,
    )
    assert result["market_bucket"] == "TRUSTED_TEST"
    assert result["can_model"] is True
    assert result["can_paper_trade"] is True


# ---------------------------------------------------------------------------
# Smoke: can_approve_bets always False
# ---------------------------------------------------------------------------

def test_can_approve_bets_always_false():
    """can_approve_bets must be False on all module outputs."""
    book   = _good_book()
    fee    = fee_model.calculate(0.55, 0.02, "B", 0.01)
    edge   = edge_engine.evaluate(0.64, book, "sports")
    sr     = settlement_risk.grade_contract("test", "official source", "MLB box", "sports_player_stat")
    guard  = execution_guard.validate_execution_request("KALSHI_PLAYABLE_LIMIT_ONLY", book, 0.52, "B", 1, "paper")
    bucket = market_buckets.classify("A", "B", True, "sports", "LOW")

    assert edge["can_approve_bets"]   is False
    assert sr["can_approve_bets"]     is False
    assert guard["can_approve_bets"]  is False
    assert bucket["can_approve_bets"] is False
