"""
orderbook_normalizer.py  —  YES/NO orderbook → normalized spread
WOW v16 Kalshi Exchange Layer

Kalshi prices are in CENTS (0–100 integer) from the API.
This module converts to decimal (0.0–1.0) internally.

Key identity:
  YES bid at price X  ≡  NO ask at (100 - X)
  YES ask at price X  ≡  NO bid at (100 - X)

So a full normalized book gives both sides from a single orderbook response.

Liquidity grade (A→F):
  A: best_yes_spread ≤ 2¢,  depth_within_2c ≥ 500
  B: spread ≤ 4¢,  depth_within_2c ≥ 200
  C: spread ≤ 6¢,  depth_within_2c ≥ 50
  D: spread ≤ 10¢, depth_within_2c ≥ 10
  F: spread > 10¢ or depth < 10
"""
from __future__ import annotations

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_decimal(price_cents: int | float | None) -> Optional[float]:
    """Convert cents (0–100) to decimal (0.0–1.0)."""
    if price_cents is None:
        return None
    return round(float(price_cents) / 100.0, 4)


def _parse_levels(raw: list[dict | list] | None) -> list[tuple[float, int]]:
    """
    Parse an orderbook level list into [(price_decimal, size), ...].

    Kalshi returns levels as {"price": int_cents, "quantity": int}
    or as [price_cents, size] in some API versions.
    """
    out = []
    for item in (raw or []):
        if isinstance(item, dict):
            p = item.get("price") or item.get("yes_price")
            s = item.get("quantity") or item.get("size") or 0
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            p, s = item[0], item[1]
        else:
            continue
        dec = _to_decimal(p)
        if dec is not None:
            out.append((dec, int(s)))
    return out


def _depth_within(
    levels: list[tuple[float, int]],
    mid:    float,
    cents:  int,
) -> int:
    """Sum of size within `cents`¢ of mid_price (decimal ±cents/100)."""
    band = cents / 100.0
    return sum(size for price, size in levels if abs(price - mid) <= band)


def _grade(spread: Optional[float], depth_2c: int) -> str:
    if spread is None:
        return "F"
    spread_cents = spread * 100
    if spread_cents <= 2 and depth_2c >= 500:
        return "A"
    if spread_cents <= 4 and depth_2c >= 200:
        return "B"
    if spread_cents <= 6 and depth_2c >= 50:
        return "C"
    if spread_cents <= 10 and depth_2c >= 10:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(raw_orderbook: dict[str, Any], ticker: str = "") -> dict[str, Any]:
    """
    Normalize a raw Kalshi orderbook response into a clean market screen.

    Input shape (from GET /markets/{ticker}/orderbook):
      {
        "orderbook": {
          "yes": [{"price": int, "quantity": int}, ...],   # bids descending
          "no":  [{"price": int, "quantity": int}, ...],   # bids descending
        }
      }
    or the older format:
      {
        "yes_bids": [...], "yes_asks": [...],
        "no_bids":  [...], "no_asks":  [...],
      }

    Returns a NormalizedBook-shaped dict.
    """
    ob = raw_orderbook.get("orderbook") or raw_orderbook

    # New API format: yes/no bids only (asks derived)
    yes_bids_raw = ob.get("yes") or ob.get("yes_bids") or []
    no_bids_raw  = ob.get("no")  or ob.get("no_bids")  or []

    yes_bids = sorted(_parse_levels(yes_bids_raw), key=lambda x: -x[0])
    no_bids  = sorted(_parse_levels(no_bids_raw),  key=lambda x: -x[0])

    # Derive: YES ask from best NO bid complement, NO ask from best YES bid complement
    best_yes_bid = yes_bids[0][0] if yes_bids else None
    best_no_bid  = no_bids[0][0]  if no_bids  else None

    # YES ask ≡ 1 - best NO bid  (the price a YES buyer would have to pay)
    best_yes_ask = round(1.0 - best_no_bid, 4)  if best_no_bid  is not None else None
    best_no_ask  = round(1.0 - best_yes_bid, 4) if best_yes_bid is not None else None

    yes_spread = (
        round(best_yes_ask - best_yes_bid, 4)
        if best_yes_bid is not None and best_yes_ask is not None
        else None
    )
    no_spread = (
        round(best_no_ask - best_no_bid, 4)
        if best_no_bid is not None and best_no_ask is not None
        else None
    )
    mid_price = (
        round((best_yes_bid + best_yes_ask) / 2.0, 4)
        if best_yes_bid is not None and best_yes_ask is not None
        else None
    )

    # Depth
    all_levels = yes_bids + no_bids
    depth_at_price = (
        sum(s for p, s in yes_bids if p == best_yes_bid)
        if best_yes_bid is not None else 0
    )
    depth_1c = _depth_within(all_levels, mid_price or 0.5, 1) if mid_price else 0
    depth_2c = _depth_within(all_levels, mid_price or 0.5, 2) if mid_price else 0

    grade = _grade(yes_spread, depth_2c)

    return {
        "ticker":          ticker,
        "best_yes_bid":    best_yes_bid,
        "best_yes_ask":    best_yes_ask,
        "best_no_bid":     best_no_bid,
        "best_no_ask":     best_no_ask,
        "yes_spread":      yes_spread,
        "no_spread":       no_spread,
        "mid_price":       mid_price,
        "depth_at_price":  depth_at_price,
        "depth_within_1c": depth_1c,
        "depth_within_2c": depth_2c,
        "liquidity_grade": grade,
        "raw_level_count": len(yes_bids) + len(no_bids),
    }


def is_liquid_enough(normalized: dict[str, Any], min_grade: str = "C") -> bool:
    """Return True if liquidity_grade meets minimum threshold."""
    order = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    actual  = order.get(normalized.get("liquidity_grade", "F"), 0)
    minimum = order.get(min_grade, 2)
    return actual >= minimum
