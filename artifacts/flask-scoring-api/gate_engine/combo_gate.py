"""
combo_gate.py — WOW-PATCH-2026-07-10
Kalshi sports-combo size gate + joint EV calculation.

Rules (Reliability Freeze — always active until explicitly lifted):
  1–2 markets → allowed, proceed to joint EV check
  3 markets   → REJECT_BAD_STRUCTURE
  4–5 markets → HARD_REJECT_COMBO_MULTIPLICATION
  any size    → can_execute = False, dry_run_only = True (unconditional)

Joint EV validation (validate_combo_ev):
  Requires: adjusted_prob per leg, combo_cost, combo_max_return.
  Computes: joint_model_probability, combo_breakeven_probability, joint_adjusted_edge.
  Any missing required field → COMBO_EV_UNOBTAINABLE / REJECT_BAD_STRUCTURE.
"""
from __future__ import annotations

import math
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RELIABILITY_FREEZE = True   # global flag — set False to lift freeze

COMBO_THRESHOLD_SOFT_REJECT = 3    # 3 markets → REJECT_BAD_STRUCTURE
COMBO_THRESHOLD_HARD_REJECT  = 4   # 4+ markets → HARD_REJECT_COMBO_MULTIPLICATION

REJECT_CODE_SOFT    = "REJECT_BAD_STRUCTURE"
REJECT_CODE_HARD    = "HARD_REJECT_COMBO_MULTIPLICATION"
REJECT_CODE_EV_MISS = "COMBO_EV_UNOBTAINABLE"

LEG_REQUIRED_FIELDS = ["adjusted_prob"]


# ---------------------------------------------------------------------------
# Public: combo size gate
# ---------------------------------------------------------------------------

def validate_combo_size(legs: list[Any]) -> dict[str, Any]:
    """
    Evaluate whether a Kalshi combo of `len(legs)` markets is allowed.

    Returns:
        {
          allowed          : bool  — True only for 1–2 markets
          market_count     : int
          reject_code      : str | None
          reject_reason    : str | None
          can_execute      : False  (always — unconditional during Reliability Freeze)
          dry_run_only     : True   (always)
        }
    """
    n = len(legs) if legs else 0

    base = {
        "market_count": n,
        "can_execute":  False,
        "dry_run_only": True,
    }

    if n < 1:
        return {
            **base,
            "allowed":       False,
            "reject_code":   REJECT_CODE_SOFT,
            "reject_reason": (
                "0-market combo is invalid (no legs supplied). "
                "Minimum combo size is 1 market."
            ),
        }

    if n >= COMBO_THRESHOLD_HARD_REJECT:
        return {
            **base,
            "allowed":       False,
            "reject_code":   REJECT_CODE_HARD,
            "reject_reason": (
                f"{n}-market Kalshi combo is unconditionally blocked during "
                "Reliability Freeze. Hard rejection before construction."
            ),
        }

    if n == COMBO_THRESHOLD_SOFT_REJECT:
        return {
            **base,
            "allowed":       False,
            "reject_code":   REJECT_CODE_SOFT,
            "reject_reason": (
                f"3-market Kalshi combo rejected (REJECT_BAD_STRUCTURE). "
                "Maximum allowed during Reliability Freeze: 2 markets."
            ),
        }

    return {
        **base,
        "allowed":       True,
        "reject_code":   None,
        "reject_reason": None,
    }


# ---------------------------------------------------------------------------
# Public: joint EV validation (for allowed 1–2 market combos)
# ---------------------------------------------------------------------------

def validate_combo_ev(
    legs:              list[dict[str, Any]],
    combo_cost:        float | None,
    combo_max_return:  float | None,
    correlation_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Validate joint EV for an allowed combo.

    Each leg dict must include:
        adjusted_prob  — float 0–1

    Also required:
        combo_cost             — numeric > 0
        combo_max_return       — numeric > 0
        correlation_check      — dict with correlation review result (required for
                                 multi-leg combos). Missing → COMBO_EV_UNOBTAINABLE.
                                 Must contain: {"performed": bool, ...}
                                 For single-leg combos (n=1), correlation_check
                                 is optional (single event, no cross-leg dependency).

    Returns:
        {
          passed                  : bool
          code                    : str
          detail                  : str
          joint_model_probability : float | None
          combo_breakeven_prob    : float | None
          joint_adjusted_edge     : float | None
          correlation_review_flag : bool
          correlation_check       : dict | None
          can_execute             : False
          dry_run_only            : True
        }
    """
    base = {"can_execute": False, "dry_run_only": True}
    n = len(legs) if legs else 0

    # --- validate legs ---
    missing_leg_fields: list[str] = []
    probs: list[float] = []

    for i, leg in enumerate(legs):
        for field in LEG_REQUIRED_FIELDS:
            val = leg.get(field)
            if val is None:
                missing_leg_fields.append(f"leg[{i}].{field}")
            else:
                try:
                    probs.append(float(val))
                except (TypeError, ValueError):
                    missing_leg_fields.append(f"leg[{i}].{field}(unparseable)")

    if missing_leg_fields:
        return {
            **base,
            "passed":                   False,
            "code":                     REJECT_CODE_EV_MISS,
            "detail":                   (
                f"COMBO_EV_UNOBTAINABLE — missing leg fields: "
                f"{', '.join(missing_leg_fields)}. "
                "Cannot compute joint EV without adjusted_prob for every leg."
            ),
            "joint_model_probability":  None,
            "combo_breakeven_prob":     None,
            "joint_adjusted_edge":      None,
            "correlation_review_flag":  True,
            "correlation_check":        None,
        }

    # --- correlation_check required for multi-leg combos ---
    if n >= 2 and not correlation_check:
        return {
            **base,
            "passed":                   False,
            "code":                     REJECT_CODE_EV_MISS,
            "detail":                   (
                "COMBO_EV_UNOBTAINABLE — correlation_check is required for "
                f"{n}-leg combos. Must provide a correlation review artifact "
                "({\"performed\": true/false, ...}) so inter-leg dependencies "
                "are evaluated before EV approval."
            ),
            "joint_model_probability":  None,
            "combo_breakeven_prob":     None,
            "joint_adjusted_edge":      None,
            "correlation_review_flag":  True,
            "correlation_check":        None,
        }

    # --- validate combo_cost and combo_max_return ---
    missing_combo: list[str] = []
    if combo_cost is None:
        missing_combo.append("combo_cost")
    if combo_max_return is None:
        missing_combo.append("combo_max_return")

    if missing_combo:
        return {
            **base,
            "passed":                   False,
            "code":                     REJECT_CODE_EV_MISS,
            "detail":                   (
                f"COMBO_EV_UNOBTAINABLE — missing combo fields: "
                f"{', '.join(missing_combo)}."
            ),
            "joint_model_probability":  None,
            "combo_breakeven_prob":     None,
            "joint_adjusted_edge":      None,
            "correlation_review_flag":  True,
            "correlation_check":        correlation_check,
        }

    try:
        cost   = float(combo_cost)
        maxret = float(combo_max_return)
    except (TypeError, ValueError):
        return {
            **base,
            "passed":                   False,
            "code":                     REJECT_CODE_EV_MISS,
            "detail":                   "combo_cost or combo_max_return unparseable.",
            "joint_model_probability":  None,
            "combo_breakeven_prob":     None,
            "joint_adjusted_edge":      None,
            "correlation_review_flag":  True,
            "correlation_check":        correlation_check,
        }

    if maxret <= 0:
        return {
            **base,
            "passed":                   False,
            "code":                     REJECT_CODE_EV_MISS,
            "detail":                   "combo_max_return must be > 0.",
            "joint_model_probability":  None,
            "combo_breakeven_prob":     None,
            "joint_adjusted_edge":      None,
            "correlation_review_flag":  True,
            "correlation_check":        correlation_check,
        }

    # --- compute ---
    joint_prob  = math.prod(probs)
    breakeven   = cost / maxret
    joint_edge  = joint_prob - breakeven

    # Correlation review always flagged for multi-leg combos (n>=2).
    # An explicit correlation_check dict from the caller documents the review was performed.
    correlation_review_flag = n >= 2

    passed = joint_edge > 0

    return {
        **base,
        "passed":                   passed,
        "code":                     "COMBO_EV_OK" if passed else REJECT_CODE_SOFT,
        "detail":                   (
            f"joint_prob={joint_prob:.4f} breakeven={breakeven:.4f} "
            f"edge={joint_edge:.4f} {'(positive)' if passed else '(non-positive — REJECT_BAD_STRUCTURE)'}"
        ),
        "joint_model_probability":  round(joint_prob, 6),
        "combo_breakeven_prob":     round(breakeven, 6),
        "joint_adjusted_edge":      round(joint_edge, 6),
        "correlation_review_flag":  correlation_review_flag,
        "correlation_check":        correlation_check,
    }


# ---------------------------------------------------------------------------
# Public: full combo evaluation (size gate + EV gate)
# ---------------------------------------------------------------------------

def evaluate_kalshi_combo(
    legs:              list[dict[str, Any]],
    combo_cost:        float | None = None,
    combo_max_return:  float | None = None,
    correlation_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Full Kalshi combo evaluation: size gate first, then joint EV.

    correlation_check — required for multi-leg (n>=2) combos.
      Must be a dict documenting the correlation review artifact
      (e.g. {"performed": true, "method": "historical_correlation", ...}).
      Missing for multi-leg → COMBO_EV_UNOBTAINABLE at EV gate.

    Returns a unified result dict. can_execute and dry_run_only are
    always False/True regardless of outcome.
    """
    size_result = validate_combo_size(legs)

    if not size_result["allowed"]:
        return {
            "passed":                   False,
            "stage":                    "SIZE_GATE",
            "reject_code":              size_result["reject_code"],
            "reject_reason":            size_result["reject_reason"],
            "market_count":             size_result["market_count"],
            "can_execute":              False,
            "dry_run_only":             True,
            "joint_model_probability":  None,
            "combo_breakeven_prob":     None,
            "joint_adjusted_edge":      None,
            "correlation_review_flag":  None,
            "correlation_check":        None,
        }

    ev_result = validate_combo_ev(
        legs, combo_cost, combo_max_return, correlation_check=correlation_check
    )

    return {
        "passed":                   ev_result["passed"],
        "stage":                    "EV_GATE",
        "reject_code":              None if ev_result["passed"] else ev_result["code"],
        "reject_reason":            None if ev_result["passed"] else ev_result["detail"],
        "market_count":             size_result["market_count"],
        "can_execute":              False,
        "dry_run_only":             True,
        "joint_model_probability":  ev_result["joint_model_probability"],
        "combo_breakeven_prob":     ev_result["combo_breakeven_prob"],
        "joint_adjusted_edge":      ev_result["joint_adjusted_edge"],
        "correlation_review_flag":  ev_result["correlation_review_flag"],
        "correlation_check":        ev_result.get("correlation_check"),
    }
