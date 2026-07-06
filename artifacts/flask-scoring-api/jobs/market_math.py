"""
jobs/market_math.py — WOW-PATCH-2026-07-06-CROSS-MARKET-REJECT-PROOF-AND-DEGRADED-RUN-GATE

Shared math/helpers for the cross-market reject-proof output contract:
  - American odds -> implied probability / no-vig pair de-vig
  - PrizePicks whole-line cash/push/loss threshold conversion
  - Drift grade (board vs consensus / model vs no-vig)
  - Mandatory market-cause tag enum + best-effort classifier

This module is intentionally dependency-free (no DB, no network) so it can be
unit-tested in isolation and reused by both the legacy wow_daily_scan.py job
and the gate_engine v16 pipeline.
"""

import math


# ---------------------------------------------------------------------------
# American odds <-> implied probability
# ---------------------------------------------------------------------------

def american_to_prob(price):
    """Convert a single American odds price to its raw (vig-included) implied probability."""
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    if price == 0:
        return None
    if price > 0:
        return 100.0 / (price + 100.0)
    return -price / (-price + 100.0)


def no_vig_pair(price_more, price_less):
    """
    De-vig a MORE/LESS (over/under) American-odds pair from the same book.

    Returns (no_vig_more, no_vig_less) — both raw implied probabilities
    normalized to sum to 1.0 — or (None, None) if either leg is unpriced.
    """
    p_more = american_to_prob(price_more)
    p_less = american_to_prob(price_less)
    if p_more is None or p_less is None:
        return None, None
    total = p_more + p_less
    if total <= 0:
        return None, None
    return round(p_more / total, 6), round(p_less / total, 6)


# ---------------------------------------------------------------------------
# PrizePicks whole-line cash/push/loss threshold conversion (item 7)
# ---------------------------------------------------------------------------

def pp_cash_threshold(side, line):
    """
    Convert a display line into the actual cash/push/loss threshold a
    PrizePicks whole-number line requires.

    Whole-number lines (e.g. 5, 10, 20) support a push (refund) at the exact
    line value on most PrizePicks markets; fractional (.5) lines are decided
    outright with no push possible. Market comparisons (edge math, hit-rate)
    must be evaluated against `cash_requires` / `cash_at_or_below`, never the
    bare display line, per WOW-PATCH-2026-07-06 item 7.

    Returns a dict:
      line_type          — "whole" | "fractional"
      cash_requires       — stat value needed to CASH the MORE side (None for LESS)
      cash_at_or_below     — stat value that CASHES the LESS side (None for MORE)
      push_at              — stat value that pushes (refund), or None if no push possible
      loss_at_or_below     — for MORE: stat values that lose outright
      loss_above           — for LESS: stat values that lose outright
    """
    try:
        line = float(line)
    except (TypeError, ValueError):
        return {
            "line_type": "unknown", "cash_requires": None, "cash_at_or_below": None,
            "push_at": None, "loss_at_or_below": None, "loss_above": None,
        }

    is_whole = float(line).is_integer()
    side = (side or "MORE").upper()

    if not is_whole:
        # Fractional line — standard over/under, no push possible.
        if side in ("MORE", "OVER"):
            return {
                "line_type": "fractional", "cash_requires": math.ceil(line),
                "cash_at_or_below": None, "push_at": None,
                "loss_at_or_below": math.floor(line), "loss_above": None,
            }
        return {
            "line_type": "fractional", "cash_requires": None,
            "cash_at_or_below": math.floor(line), "push_at": None,
            "loss_at_or_below": None, "loss_above": math.ceil(line),
        }

    # Whole-number line — push possible at the exact line value.
    if side in ("MORE", "OVER"):
        return {
            "line_type": "whole", "cash_requires": line + 1,
            "cash_at_or_below": None, "push_at": line,
            "loss_at_or_below": line - 1, "loss_above": None,
        }
    return {
        "line_type": "whole", "cash_requires": None,
        "cash_at_or_below": line - 1, "push_at": line,
        "loss_at_or_below": None, "loss_above": line + 1,
    }


def compute_threshold_hit_rate(raw_games, side, threshold):
    """
    Recompute an empirical hit-rate against the cash threshold (not the bare
    display line) using the raw per-game stat rows already fetched for L5/L10.

    raw_games: list of {"stat": <number>, ...} dicts (raw_l5 / raw_l10 shape).
    threshold: the `cash_requires` (MORE) or `cash_at_or_below` (LESS) value
               from pp_cash_threshold(). Returns None if it cannot be computed.
    """
    if not raw_games or threshold is None:
        return None
    vals = [g.get("stat") for g in raw_games if isinstance(g, dict) and g.get("stat") is not None]
    if not vals:
        return None
    side = (side or "MORE").upper()
    if side in ("MORE", "OVER"):
        hits = sum(1 for v in vals if v >= threshold)
    else:
        hits = sum(1 for v in vals if v <= threshold)
    return round(hits / len(vals), 4)


# ---------------------------------------------------------------------------
# Drift grade
# ---------------------------------------------------------------------------

def compute_drift_grade(adjusted_edge):
    """
    Grade the magnitude/direction of adjusted_edge (model_probability -
    no_vig_probability) into a simple A-F letter grade for quick scan.

    A: edge >= 0.10 (strong favourable drift)
    B: edge >= 0.05
    C: edge >= 0.00  (flat / no verified edge)
    D: edge >= -0.05 (mild drift against side)
    F: edge < -0.05  (market meaningfully against side)
    U: edge unavailable (no consensus odds to compare against)
    """
    if adjusted_edge is None:
        return "U"
    if adjusted_edge >= 0.10:
        return "A"
    if adjusted_edge >= 0.05:
        return "B"
    if adjusted_edge >= 0.00:
        return "C"
    if adjusted_edge >= -0.05:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Mandatory market-cause tags (item 3)
# ---------------------------------------------------------------------------

MARKET_CAUSE_TAGS = (
    "NO_VERIFIED_MISPRICE",
    "MARKET_AGAINST_SIDE",
    "STALE_BOARD",
    "SOURCE_CONFLICT",
    "EXACT_MARKET_UNAVAILABLE",
    "ADJACENT_MARKET_ONLY",
    "ROLE_DEPLOYMENT_UNCERTAIN",
    "PUBLIC_OVERREACTION_UNVERIFIED",
    "PAYOUT_EV_FAIL",
)


def classify_market_cause(
    *,
    classification,
    odds_ok,
    logs_ok,
    adjusted_edge,
    used_average_only,
    manual_fallback_used,
    source_conflict,
    role_deployment_uncertain,
    payout_ev_fail,
    stale_board,
):
    """
    Best-effort deterministic mapping of run signals onto one of the 9
    mandatory market-cause tags. Precedence (highest signal wins):

      1. SOURCE_CONFLICT             — odds vs rundown disagree on the same prop
      2. ROLE_DEPLOYMENT_UNCERTAIN   — starter/role status not confirmed (MLB pitcher gate)
      3. STALE_BOARD                 — board data fell back / could not be confirmed fresh
      4. EXACT_MARKET_UNAVAILABLE    — no raw L5/L10 exact-stat log found for this market
      5. PAYOUT_EV_FAIL              — edge only exists against the display line, not the
                                        PrizePicks cash threshold (item 7)
      6. ADJACENT_MARKET_ONLY        — manual/cross-market fallback used to fill the stat
      7. MARKET_AGAINST_SIDE         — adjusted_edge is meaningfully negative
      8. PUBLIC_OVERREACTION_UNVERIFIED — model/score looks strong but no independent
                                           sportsbook confirmation exists (odds not ok)
      9. NO_VERIFIED_MISPRICE        — default: no threshold/role/source issue, edge is
                                        simply too small/flat to act on

    Only applies for classifications below the fully-approved tiers (Reject,
    Watch, Conditional, Data Insufficient, Model Qualified). Approved tiers
    return None — a market cause is a reason for NOT playing, not a badge on
    a play.
    """
    if classification in ("Market Verified Approved", "Final Approved — Internal Projection"):
        return None

    if source_conflict:
        return "SOURCE_CONFLICT"
    if role_deployment_uncertain:
        return "ROLE_DEPLOYMENT_UNCERTAIN"
    if stale_board:
        return "STALE_BOARD"
    if not logs_ok:
        return "EXACT_MARKET_UNAVAILABLE"
    if payout_ev_fail:
        return "PAYOUT_EV_FAIL"
    if manual_fallback_used or used_average_only:
        return "ADJACENT_MARKET_ONLY"
    if adjusted_edge is not None and adjusted_edge < -0.02:
        return "MARKET_AGAINST_SIDE"
    if not odds_ok:
        return "PUBLIC_OVERREACTION_UNVERIFIED"
    return "NO_VERIFIED_MISPRICE"
