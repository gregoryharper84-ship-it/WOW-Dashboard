"""
edge_engine.py  —  Kalshi edge calculation and approval thresholds
WOW v16 Kalshi Exchange Layer

Hard thresholds (adjusted_edge, not raw):
  LIQUID SPORTS / WEATHER       ≥ 4%    (0.04)
  MACRO / NEWS                  ≥ 5–6%  (0.05)
  THIN / NARRATIVE              ≥ 6–8%  (0.06 default, up to 0.08)

Any contract below its category threshold → KALSHI_REJECT_NO_EDGE
Any contract with no model probability   → KALSHI_REJECT_UNCALIBRATED

Terminal labels:
  KALSHI_FINAL_APPROVED         — all checks pass, limit-order confirmed
  KALSHI_PLAYABLE_LIMIT_ONLY    — edge passes, must use limit (no market orders)
  KALSHI_WATCH                  — edge slightly below threshold but not far
  KALSHI_REJECT_NO_EDGE         — adjusted edge below threshold
  KALSHI_REJECT_FEE_DRAG        — fee+spread erases otherwise valid raw edge
  KALSHI_REJECT_THIN_BOOK       — orderbook below minimum liquidity grade
  KALSHI_REJECT_UNCALIBRATED    — no model probability available
"""
from __future__ import annotations

from typing import Any

from .fee_model import adjusted_edge as _calc_adjusted_edge
from .orderbook_normalizer import is_liquid_enough

# ---------------------------------------------------------------------------
# Category thresholds
# ---------------------------------------------------------------------------

# Category → minimum adjusted_edge to play
_THRESHOLDS: dict[str, float] = {
    "sports":    0.04,
    "weather":   0.04,
    "macro":     0.05,
    "politics":  0.06,
    "news":      0.05,
    "narrative": 0.06,
    "crypto":    0.06,
    "other":     0.06,
}

# Watch zone: within this many percentage points below threshold
_WATCH_BAND = 0.02

# Minimum liquidity grade required
_MIN_LIQUIDITY_GRADE = "C"

# Maximum playable price: model_prob - min_threshold (don't overpay)
def _max_playable(model_prob: float, category: str) -> float:
    threshold = _THRESHOLDS.get(category, 0.06)
    return round(model_prob - threshold, 4)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate(
    model_probability:  float | None,
    normalized_book:    dict[str, Any],
    category:           str   = "sports",
    side:               str   = "YES",
    uncertainty_tax:    float = 0.01,
) -> dict[str, Any]:
    """
    Evaluate a Kalshi contract for edge and label.

    Parameters
    ----------
    model_probability  — your probability estimate for YES winning (0–1)
    normalized_book    — output of orderbook_normalizer.normalize()
    category           — market category (determines threshold)
    side               — YES or NO (your intended trade side)
    uncertainty_tax    — model uncertainty haircut

    Returns
    -------
    dict with: label, adjusted_edge, max_playable_price, blocking_reasons,
               execution, fee_detail, can_approve_bets: False
    """
    blocking:  list[str] = []
    warnings:  list[str] = []
    label = "KALSHI_REJECT_UNCALIBRATED"

    # ── Guard: no model probability ──────────────────────────────────────────
    if model_probability is None:
        return {
            "label":               "KALSHI_REJECT_UNCALIBRATED",
            "adjusted_edge":       None,
            "raw_edge":            None,
            "max_playable_price":  None,
            "blocking_reasons":    ["No model probability — KALSHI_REJECT_UNCALIBRATED"],
            "warnings":            [],
            "execution":           "BLOCKED_NO_MODEL",
            "fee_detail":          {},
            "can_approve_bets":    False,
        }

    # ── Guard: liquidity ─────────────────────────────────────────────────────
    liq_grade = normalized_book.get("liquidity_grade", "F")
    if not is_liquid_enough(normalized_book, _MIN_LIQUIDITY_GRADE):
        blocking.append(f"LIQUIDITY_GRADE_{liq_grade}_BELOW_MINIMUM_{_MIN_LIQUIDITY_GRADE}")

    # ── Entry price for this side ────────────────────────────────────────────
    # For YES: entry = best_yes_ask (what you pay to buy YES)
    # For NO:  entry = best_no_ask  (what you pay to buy NO)
    #          model_prob for NO side = 1 - model_probability
    side_upper = (side or "YES").upper()
    if side_upper == "YES":
        entry_price     = normalized_book.get("best_yes_ask")
        side_model_prob = model_probability
    else:
        entry_price     = normalized_book.get("best_no_ask")
        side_model_prob = round(1.0 - model_probability, 4)

    if entry_price is None:
        blocking.append("NO_ENTRY_PRICE_AVAILABLE")
        return {
            "label":               "KALSHI_DATA_UNOBTAINABLE",
            "adjusted_edge":       None,
            "raw_edge":            None,
            "max_playable_price":  None,
            "blocking_reasons":    blocking,
            "warnings":            warnings,
            "execution":           "BLOCKED_NO_ORDERBOOK",
            "fee_detail":          {},
            "can_approve_bets":    False,
        }

    # ── Edge calculation ─────────────────────────────────────────────────────
    threshold  = _THRESHOLDS.get(category.lower(), 0.06)
    max_play   = _max_playable(side_model_prob, category)
    yes_spread = normalized_book.get("yes_spread")

    fee_result = _calc_adjusted_edge(
        model_probability = side_model_prob,
        entry_price       = entry_price,
        yes_spread        = yes_spread,
        liquidity_grade   = liq_grade,
        uncertainty_tax   = uncertainty_tax,
    )

    raw_edge  = fee_result["raw_edge"]
    adj_edge  = fee_result["adjusted_edge"]

    # ── Classify ─────────────────────────────────────────────────────────────
    if blocking:
        label     = "KALSHI_REJECT_THIN_BOOK" if "LIQUIDITY" in blocking[0] else "KALSHI_DATA_UNOBTAINABLE"
        execution = "BLOCKED"
    elif adj_edge < 0 and raw_edge >= threshold:
        # Raw edge existed but fees/spread killed it
        blocking.append(
            f"FEE_DRAG: raw_edge={raw_edge:.3f} but adjusted_edge={adj_edge:.3f} — "
            f"fee+spread erased edge."
        )
        label     = "KALSHI_REJECT_FEE_DRAG"
        execution = "BLOCKED_FEE_DRAG"
    elif adj_edge < threshold - _WATCH_BAND:
        blocking.append(
            f"EDGE_BELOW_THRESHOLD: adjusted_edge={adj_edge:.3f} < "
            f"threshold={threshold:.3f} for category={category}"
        )
        label     = "KALSHI_REJECT_NO_EDGE"
        execution = "BLOCKED_NO_EDGE"
    elif adj_edge < threshold:
        warnings.append(f"WATCH_ZONE: adj_edge={adj_edge:.3f} within {_WATCH_BAND:.2f} of threshold={threshold:.3f}")
        label     = "KALSHI_WATCH"
        execution = "WATCH_ONLY"
    elif entry_price > max_play:
        warnings.append(f"PRICE_ABOVE_MAX_PLAYABLE: entry={entry_price:.3f} > max={max_play:.3f}")
        label     = "KALSHI_WATCH"
        execution = "WATCH_PRICE_MOVED"
    else:
        # All checks pass → LIMIT only (no market orders ever)
        label     = "KALSHI_PLAYABLE_LIMIT_ONLY"
        execution = "LIMIT_ONLY_NO_MARKET_ORDER"

    return {
        "label":               label,
        "side":                side_upper,
        "entry_price":         round(entry_price, 4),
        "model_probability":   round(side_model_prob, 4),
        "raw_edge":            round(raw_edge, 4),
        "adjusted_edge":       round(adj_edge, 4),
        "threshold":           threshold,
        "max_playable_price":  round(max_play, 4),
        "liquidity_grade":     liq_grade,
        "blocking_reasons":    blocking,
        "warnings":            warnings,
        "execution":           execution,
        "fee_detail":          fee_result,
        "can_approve_bets":    False,
    }
