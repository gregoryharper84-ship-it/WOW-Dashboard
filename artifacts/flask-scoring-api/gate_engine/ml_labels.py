"""
gate_engine/ml_labels.py
WOW-PATCH-2026-07-13 — ML Game Winner Lane
New reason codes / internal labels for the ML price/settlement/exposure
governance patch.  The external six-label LLP structure is preserved;
these are stored as internal `reason_code` fields alongside `final_label`.
"""
from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Internal reason codes (stored alongside final_label — never replace it)
# ---------------------------------------------------------------------------

class MLReasonCode(str, Enum):
    # P0-1 settlement truth
    PROMO_SETTLEMENT         = "LLP_SETTLEMENT_RECONCILIATION_REQUIRED"
    OFFICIAL_LOSS            = "OFFICIAL_RESULT_OVERRIDES_PLATFORM_DISPLAY"

    # P0-2 deduplication
    DUPLICATE_EXPOSURE       = "LLP_REJECT_DUPLICATE_EXPOSURE"

    # P0-3 / P1-4 edge gate
    NO_VERIFIED_EDGE         = "LLP_REJECT_NO_VERIFIED_EDGE"
    PRICE_COMPRESSION        = "LLP_REJECT_PRICE_COMPRESSION"
    MISSING_EDGE_FIELDS      = "LLP_WATCH_MISSING_EDGE_FIELDS"

    # P1-5 bullpen
    BULLPEN_UNVERIFIED       = "LLP_WATCH_BULLPEN_UNVERIFIED"

    # P1-7 market disagreement
    MARKET_DISAGREEMENT      = "LLP_WATCH_MARKET_DISAGREEMENT"

    # Settlement reconciliation
    SETTLEMENT_RECONCILIATION = "LLP_SETTLEMENT_RECONCILIATION_REQUIRED"


# ---------------------------------------------------------------------------
# Breakeven-probability compression table (P1-4)
# Maps (be_lo, be_hi) → (min_verified_edge, description)
# ---------------------------------------------------------------------------

COMPRESSION_TABLE: list[tuple[float, float, float, str]] = [
    (0.52, 0.56, 0.015, "52–56% BE: min +1.5% verified edge"),
    (0.56, 0.60, 0.020, "56–60% BE: min +2.0% verified edge"),
    (0.60, 0.65, 0.025, "60–65% BE: min +2.5% verified edge"),
    (0.65, 0.70, 0.030, "65–70% BE: min +3.0% verified edge"),
    (0.70, 1.01, 0.035, "70%+ BE: min +3.5% verified edge"),
]


def compression_floor(breakeven_prob: float) -> float | None:
    """Return minimum verified_edge for this breakeven_prob bucket, or None if <52%."""
    for lo, hi, floor, _ in COMPRESSION_TABLE:
        if lo <= breakeven_prob < hi:
            return floor
    return None


def compression_description(breakeven_prob: float) -> str:
    for lo, hi, _, desc in COMPRESSION_TABLE:
        if lo <= breakeven_prob < hi:
            return desc
    return "breakeven <52% — below min threshold"


# ---------------------------------------------------------------------------
# Market-disagreement quadrant labels (P1-7)
# ---------------------------------------------------------------------------

class MarketDisagreementLabel(str, Enum):
    MARKET_CORROBORATED_EDGE = "MARKET_CORROBORATED_EDGE"
    MODEL_ONLY_DISAGREEMENT  = "MODEL_ONLY_DISAGREEMENT"
    MARKET_ONLY_EDGE         = "MARKET_ONLY_EDGE"
    NO_VERIFIED_EDGE         = "NO_VERIFIED_EDGE"


# ---------------------------------------------------------------------------
# Platform settlement status codes (P0-1)
# ---------------------------------------------------------------------------

class PlatformSettlementStatus(str, Enum):
    SETTLED_WIN              = "SETTLED_WIN"
    SETTLED_LOSS             = "SETTLED_LOSS"
    PROMO_OR_SPECIAL         = "PROMO_OR_SPECIAL_SETTLEMENT"
    PUSH                     = "PUSH"
    PENDING                  = "PENDING"
    UNKNOWN                  = "UNKNOWN"
