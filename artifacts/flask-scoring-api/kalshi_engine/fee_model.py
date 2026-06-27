"""
fee_model.py  —  Kalshi fee, spread, and slippage drag calculator
WOW v16 Kalshi Exchange Layer

Kalshi fee formula (as of 2024 schedule):
  fee_per_contract = FEE_RATE * min(entry_price, 1 - entry_price)

This is the "price-based contract formula" — fees are symmetric around 50¢
and are highest (max FEE_RATE * 0.50 = 3.5¢) at the midpoint.

Additional drag sources:
  spread_drag    = yes_spread / 2           (half-spread cost to cross)
  slippage_drag  = estimated fill slippage   (function of depth grade)
  uncertainty_tax = flat penalty for model uncertainty (caller-supplied)

All outputs in probability-space (decimal), same units as edge.
"""
from __future__ import annotations

from typing import Any, Optional

# ---------------------------------------------------------------------------
# Fee schedule constants
# ---------------------------------------------------------------------------

# Kalshi charges ~7% of the "risk amount" per contract.
# risk_amount = min(entry_price, 1 - entry_price) per $1 contract
FEE_RATE: float = 0.07

# Slippage estimates by liquidity grade
_SLIPPAGE_BY_GRADE: dict[str, float] = {
    "A": 0.002,   # 0.2¢ average slippage
    "B": 0.004,
    "C": 0.008,
    "D": 0.015,
    "F": 0.030,
}

# Default uncertainty tax for thin / narrative markets
DEFAULT_UNCERTAINTY_TAX = 0.01


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def calculate(
    entry_price:      float,
    yes_spread:       Optional[float] = None,
    liquidity_grade:  str             = "C",
    uncertainty_tax:  float           = DEFAULT_UNCERTAINTY_TAX,
    contracts:        int             = 1,
) -> dict[str, Any]:
    """
    Compute all drag components for a Kalshi contract entry.

    Parameters
    ----------
    entry_price      — decimal price paid (0.0–1.0)
    yes_spread       — best YES spread in decimal (from orderbook normalizer)
    liquidity_grade  — A/B/C/D/F from orderbook normalizer
    uncertainty_tax  — caller-supplied model uncertainty haircut
    contracts        — number of contracts (scales fee only; drag ratios stay the same)

    Returns
    -------
    dict with: raw_edge (placeholder), estimated_fee, spread_drag,
               slippage_drag, uncertainty_tax, total_drag, adjusted_edge (stub)
    """
    if not (0.0 < entry_price < 1.0):
        raise ValueError(f"entry_price must be in (0, 1), got {entry_price}")

    # Fee per contract
    fee_per_contract = round(FEE_RATE * min(entry_price, 1.0 - entry_price), 6)
    total_fee        = round(fee_per_contract * contracts, 6)

    # Spread drag: cost of crossing half the yes spread
    spread_drag = round((yes_spread or 0.0) / 2.0, 6)

    # Slippage drag
    slippage_drag = _SLIPPAGE_BY_GRADE.get(liquidity_grade, _SLIPPAGE_BY_GRADE["C"])

    total_drag = round(fee_per_contract + spread_drag + slippage_drag + uncertainty_tax, 6)

    return {
        "entry_price":        round(entry_price, 4),
        "fee_rate":           FEE_RATE,
        "fee_per_contract":   fee_per_contract,
        "total_fee":          total_fee,
        "spread_drag":        spread_drag,
        "slippage_drag":      slippage_drag,
        "uncertainty_tax":    uncertainty_tax,
        "total_drag":         total_drag,
        "liquidity_grade":    liquidity_grade,
    }


def adjusted_edge(
    model_probability: float,
    entry_price:       float,
    yes_spread:        Optional[float] = None,
    liquidity_grade:   str             = "C",
    uncertainty_tax:   float           = DEFAULT_UNCERTAINTY_TAX,
) -> dict[str, Any]:
    """
    Full edge calculation: raw → adjusted after all drag.

    raw_edge        = model_probability - entry_price
    adjusted_edge   = raw_edge - total_drag

    Returns the fee breakdown + raw_edge + adjusted_edge.
    """
    fees = calculate(
        entry_price     = entry_price,
        yes_spread      = yes_spread,
        liquidity_grade = liquidity_grade,
        uncertainty_tax = uncertainty_tax,
    )
    raw = round(model_probability - entry_price, 6)
    adj = round(raw - fees["total_drag"], 6)

    return {
        **fees,
        "model_probability": round(model_probability, 4),
        "raw_edge":          raw,
        "adjusted_edge":     adj,
    }
