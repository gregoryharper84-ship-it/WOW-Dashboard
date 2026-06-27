"""
schemas.py  —  Kalshi engine data contracts

All dataclasses / TypedDicts used internally.
Use these instead of raw dicts to get IDE/type-checker support.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Kalshi terminal labels
# ---------------------------------------------------------------------------

KALSHI_LABELS = {
    "KALSHI_FINAL_APPROVED",
    "KALSHI_PLAYABLE_LIMIT_ONLY",
    "KALSHI_WATCH",
    "KALSHI_SCOUT",
    "KALSHI_REJECT_NO_EDGE",
    "KALSHI_REJECT_BAD_RULES",
    "KALSHI_REJECT_THIN_BOOK",
    "KALSHI_REJECT_FEE_DRAG",
    "KALSHI_REJECT_UNCALIBRATED",
    "KALSHI_DATA_UNOBTAINABLE",
}

# Market bucket values
MARKET_BUCKETS = {
    "TRUSTED_TEST",   # verified settlement, good liquidity, tracked sport/event
    "WATCH",          # solid settlement but insufficient history or spread
    "TEST_ONLY",      # acceptable for paper-trade only; not for real capital
    "SCOUT",          # new/unknown — gather data, no model probability yet
    "REJECT",         # settlement ambiguity, too thin, bad rules, or model gap
}

# Execution mode (hardcoded — no live trading yet)
DRY_RUN_ONLY        = True
ALLOW_LIVE_TRADING  = False
ALLOW_MARKET_ORDERS = False


@dataclass
class KalshiOrderbook:
    ticker:           str
    yes_bids:         list[tuple[float, int]]  # [(price, size), ...]
    yes_asks:         list[tuple[float, int]]
    no_bids:          list[tuple[float, int]]
    no_asks:          list[tuple[float, int]]
    timestamp_utc:    str                       # ISO-8601


@dataclass
class NormalizedBook:
    ticker:           str
    best_yes_bid:     Optional[float]
    best_yes_ask:     Optional[float]
    best_no_bid:      Optional[float]
    best_no_ask:      Optional[float]
    yes_spread:       Optional[float]
    no_spread:        Optional[float]
    mid_price:        Optional[float]
    depth_at_price:   Optional[int]
    depth_within_1c:  int
    depth_within_2c:  int
    liquidity_grade:  str         # A / B / C / D / F
    timestamp_utc:    str


@dataclass
class FeeResult:
    entry_price:       float
    fee_rate:          float
    fee_per_contract:  float
    spread_drag:       float
    slippage_drag:     float
    uncertainty_tax:   float
    total_drag:        float
    raw_edge:          float
    adjusted_edge:     float


@dataclass
class ContractEvaluation:
    ticker:              str
    side:                str           # YES / NO
    model_probability:   float
    current_price:       float
    raw_edge:            float
    adjusted_edge:       float
    max_playable_price:  float
    liquidity_grade:     str
    settlement_risk:     str           # LOW / MEDIUM / HIGH / REJECT
    market_bucket:       str
    label:               str
    execution:           str
    blocking_reasons:    list[str]
    warnings:            list[str]
    fee_detail:          dict
    can_approve_bets:    bool = False


@dataclass
class PaperTradeEntry:
    ticker:              str
    side:                str
    model_probability:   float
    entry_price:         float
    contracts:           int
    kalshi_price_at_entry: float
    adjusted_edge:       float
    label:               str
    notes:               str = ""
