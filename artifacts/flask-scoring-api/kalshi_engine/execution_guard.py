"""
execution_guard.py  —  Kalshi kill-switch + paper-trade enforcement
WOW v16 Kalshi Exchange Layer

Hard-wired kill switches. These override all other logic.
Until ALLOW_LIVE_TRADING is explicitly set to True (in env) AND
ALLOW_MARKET_ORDERS is also True, NO real orders will be submitted.

Current state:
  DRY_RUN_ONLY        = True    (always logs, never executes)
  ALLOW_LIVE_TRADING  = False   (env var KALSHI_ALLOW_LIVE_TRADING must = "true")
  ALLOW_MARKET_ORDERS = False   (never — only limit orders permitted)

REQUIRE_ORDERBOOK     = True    — evaluation blocked without fresh orderbook
REQUIRE_LIMIT_PRICE   = True    — must specify a limit price
REQUIRE_SETTLEMENT_AUDIT = True — grade_contract() must run first
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Kill switches (override via environment only — never in code)
# ---------------------------------------------------------------------------

def _flag(env_var: str) -> bool:
    return os.environ.get(env_var, "false").lower() == "true"


DRY_RUN_ONLY:           bool = True                       # non-negotiable default
ALLOW_LIVE_TRADING:     bool = _flag("KALSHI_ALLOW_LIVE_TRADING")
ALLOW_MARKET_ORDERS:    bool = False                      # hard-coded off
REQUIRE_ORDERBOOK:      bool = True
REQUIRE_LIMIT_PRICE:    bool = True
REQUIRE_SETTLEMENT_AUDIT: bool = True

MAX_CONTRACTS_PER_TRADE = int(os.environ.get("KALSHI_MAX_CONTRACTS_PER_TRADE", "10"))
MAX_DAILY_RISK          = float(os.environ.get("KALSHI_MAX_DAILY_RISK", "100.0"))
MAX_MARKET_RISK         = float(os.environ.get("KALSHI_MAX_MARKET_RISK", "25.0"))


# ---------------------------------------------------------------------------
# Guard: validate all execution prerequisites
# ---------------------------------------------------------------------------

def validate_execution_request(
    label:                   str,
    normalized_book:         dict[str, Any] | None,
    limit_price:             float | None,
    settlement_grade:        str | None,
    contracts:               int    = 1,
    mode:                    str    = "paper",     # "paper" or "live"
) -> dict[str, Any]:
    """
    Check all execution prerequisites before a (paper or live) trade.

    Returns:
      { allowed, mode, blocks, warnings, kill_switches }
    """
    blocks:   list[str] = []
    warnings: list[str] = []

    # ── Kill switches ─────────────────────────────────────────────────────────
    if mode == "live" and not ALLOW_LIVE_TRADING:
        blocks.append("KILL_SWITCH: ALLOW_LIVE_TRADING=False — live trading disabled")
    if ALLOW_MARKET_ORDERS is False and limit_price is None:
        blocks.append("KILL_SWITCH: ALLOW_MARKET_ORDERS=False + no limit_price provided")
    if DRY_RUN_ONLY and mode == "live":
        blocks.append("KILL_SWITCH: DRY_RUN_ONLY=True")

    # ── Label check ───────────────────────────────────────────────────────────
    playable_labels = {"KALSHI_PLAYABLE_LIMIT_ONLY", "KALSHI_FINAL_APPROVED"}
    if label not in playable_labels:
        blocks.append(f"LABEL_BLOCK: {label} is not a playable label")

    # ── Orderbook requirement ─────────────────────────────────────────────────
    if REQUIRE_ORDERBOOK and (not normalized_book or normalized_book.get("liquidity_grade") == "F"):
        blocks.append("REQUIRE_ORDERBOOK: no live orderbook or grade=F")

    # ── Limit price ───────────────────────────────────────────────────────────
    if REQUIRE_LIMIT_PRICE and limit_price is None:
        blocks.append("REQUIRE_LIMIT_PRICE: must specify limit_price for all Kalshi entries")

    # ── Settlement audit ──────────────────────────────────────────────────────
    if REQUIRE_SETTLEMENT_AUDIT and settlement_grade in (None, "F", "D"):
        grade_str = settlement_grade or "missing"
        blocks.append(
            f"REQUIRE_SETTLEMENT_AUDIT: settlement grade={grade_str} — run grade_contract() first"
        )

    # ── Position size ─────────────────────────────────────────────────────────
    if contracts > MAX_CONTRACTS_PER_TRADE:
        blocks.append(f"SIZE_LIMIT: {contracts} > MAX_CONTRACTS_PER_TRADE={MAX_CONTRACTS_PER_TRADE}")

    allowed = len(blocks) == 0
    return {
        "allowed":          allowed,
        "mode":             mode if allowed else "blocked",
        "blocks":           blocks,
        "warnings":         warnings,
        "kill_switches": {
            "DRY_RUN_ONLY":           DRY_RUN_ONLY,
            "ALLOW_LIVE_TRADING":     ALLOW_LIVE_TRADING,
            "ALLOW_MARKET_ORDERS":    ALLOW_MARKET_ORDERS,
            "REQUIRE_ORDERBOOK":      REQUIRE_ORDERBOOK,
            "REQUIRE_LIMIT_PRICE":    REQUIRE_LIMIT_PRICE,
            "REQUIRE_SETTLEMENT_AUDIT": REQUIRE_SETTLEMENT_AUDIT,
            "MAX_CONTRACTS_PER_TRADE": MAX_CONTRACTS_PER_TRADE,
            "MAX_DAILY_RISK":          MAX_DAILY_RISK,
            "MAX_MARKET_RISK":         MAX_MARKET_RISK,
        },
        "can_approve_bets": False,
    }


def paper_trade_log_entry(
    ticker:            str,
    side:              str,
    model_probability: float,
    entry_price:       float,
    contracts:         int,
    adjusted_edge:     float,
    label:             str,
    notes:             str = "",
) -> dict[str, Any]:
    """
    Build a paper-trade log entry dict (NOT written to DB here —
    caller passes this to calibration_ledger.log_paper_trade).
    """
    return {
        "ticker":            ticker,
        "side":              side,
        "model_probability": model_probability,
        "entry_price":       entry_price,
        "contracts":         contracts,
        "adjusted_edge":     adjusted_edge,
        "label":             label,
        "timestamp_utc":     datetime.now(tz=timezone.utc).isoformat(),
        "notes":             notes,
        "mode":              "paper",
        "can_approve_bets":  False,
    }
