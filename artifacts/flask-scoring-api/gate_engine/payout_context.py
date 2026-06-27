"""
payout_context.py  —  Module C: Payout Context on Every Prop
WOW v16 / Section 29.1

Every prop requires a payout context block evaluating slip EV at the prop level.
A prop that passes market gates but has negative EV at the intended slip format
receives terminal label MARKET_QUALIFIED_BUT_SLIP_NEGATIVE and is blocked from
slip construction.

PAYOUT LADDER:
  POSITIVE_EV  — usable_prob > required_per_leg_prob
  MARGINAL_EV  — usable_prob within 2% of required (positive but thin)
  NEGATIVE_EV  — usable_prob < required_per_leg_prob → MARKET_QUALIFIED_BUT_SLIP_NEGATIVE
  FORMAT_PENDING — slip type not yet determined; holds out of slip construction
  UNUSABLE     — model_prob or usable_prob cannot be computed
  UNVERIFIED   — no payout table data for this format

PrizePicks Power Play break-even thresholds (per leg):
  2-pick Power:  57.8%
  3-pick Power:  64.2%
  4-pick Power:  67.9%
  5-pick Power:  71.0%  (approx)
  6-pick Power:  73.5%  (approx)

Flex break-evens are lower (partial hit payouts), format-specific.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# PrizePicks payout table (per-leg break-even probabilities)
# ---------------------------------------------------------------------------

POWER_BREAKEVEN: dict[str, float] = {
    "2-pick Power": 0.578,
    "3-pick Power": 0.642,
    "4-pick Power": 0.679,
    "5-pick Power": 0.710,
    "6-pick Power": 0.735,
}

# Flex break-evens are conservative estimates (each-leg minimum)
FLEX_BREAKEVEN: dict[str, float] = {
    "3-pick Flex":  0.555,
    "4-pick Flex":  0.565,
    "5-pick Flex":  0.575,
    "6-pick Flex":  0.580,
}

ALL_FORMATS: dict[str, float] = {**POWER_BREAKEVEN, **FLEX_BREAKEVEN}

# Straight sportsbook bet — EV is judged by no-vig edge, not PP table
STRAIGHT_BET_FORMATS = {"Straight bet", "Sportsbook", "BetUS"}

# Margin within which POSITIVE_EV is flagged as MARGINAL_EV
MARGINAL_THRESHOLD = 0.02   # 2%

# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def run(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute payout context for a prop row.

    Reads from enrichment["payout_context"] (or row["payout_context"]):
        {
          intended_format:      str   (e.g. "3-pick Power" / "FORMAT_PENDING")
          model_probability:    float (final_model_prob, 0–1)
          uncertainty_haircut:  float (fraction, e.g. 0.04)
          usable_probability:   float | None  (model_prob minus haircut)
          sportsbook_edge:      float | None  (for straight bets, pure edge)
        }

    Returns:
        {
          passed:               bool
          intended_format:      str
          slip_breakeven:       float | None
          required_per_leg_prob: float | None
          model_probability:    float | None
          uncertainty_haircut:  float | None
          usable_probability:   float | None
          ev_gap:               float | None   (usable - required)
          payout_slip_label:    str
          blocked_from_slip:    bool
          code:                 str
          detail:               str
        }
    """
    enr = enrichment or {}
    ctx_raw = (
        row.get("payout_context")
        or enr.get("payout_context")
        or {}
    )
    if isinstance(ctx_raw, str):
        ctx_raw = {}

    intended_format    = str(ctx_raw.get("intended_format") or "FORMAT_PENDING").strip()
    model_prob         = _parse_float(ctx_raw.get("model_probability"))
    haircut            = _parse_float(ctx_raw.get("uncertainty_haircut")) or 0.0
    usable_prob        = _parse_float(ctx_raw.get("usable_probability"))
    sportsbook_edge    = _parse_float(ctx_raw.get("sportsbook_edge"))

    # Derive usable_prob if not explicitly provided
    if usable_prob is None and model_prob is not None:
        usable_prob = round(model_prob - haircut, 4)

    # Handle straight bet
    if intended_format in STRAIGHT_BET_FORMATS:
        return _straight_bet_result(
            row, intended_format, model_prob, haircut, usable_prob, sportsbook_edge
        )

    # Format pending
    if intended_format.upper() == "FORMAT_PENDING" or intended_format == "":
        result = _build_result(
            passed=True,   # not a blocker by itself
            intended_format="FORMAT_PENDING",
            slip_breakeven=None,
            required_per_leg_prob=None,
            model_probability=model_prob,
            uncertainty_haircut=haircut,
            usable_probability=usable_prob,
            ev_gap=None,
            payout_slip_label="FORMAT_PENDING",
            blocked_from_slip=False,
            code="FORMAT_PENDING",
            detail="Slip type not yet determined — holding out of slip construction.",
        )
        row.setdefault("gates", {})["payout_context"] = result
        # FORMAT_PENDING is not a terminal blocker — let classifier decide the label
        return result

    # Known format — look up break-even
    breakeven = ALL_FORMATS.get(intended_format)
    if breakeven is None:
        result = _build_result(
            passed=False,
            intended_format=intended_format,
            slip_breakeven=None,
            required_per_leg_prob=None,
            model_probability=model_prob,
            uncertainty_haircut=haircut,
            usable_probability=usable_prob,
            ev_gap=None,
            payout_slip_label="UNVERIFIED",
            blocked_from_slip=True,
            code="FORMAT_UNVERIFIED",
            detail=f"No payout table entry for format '{intended_format}' — cap at MODEL_QUALIFIED_HOLD.",
        )
        row.setdefault("gates", {})["payout_context"] = result
        if not row.get("terminal_label"):
            row["label_ceiling"] = PropLabel.MODEL_QUALIFIED_HOLD.value
        row["blockers"].append(f"PAYOUT_CONTEXT:FORMAT_UNVERIFIED:{intended_format}")
        return result

    # Compute EV gap
    if usable_prob is None:
        result = _build_result(
            passed=False,
            intended_format=intended_format,
            slip_breakeven=breakeven,
            required_per_leg_prob=breakeven,
            model_probability=model_prob,
            uncertainty_haircut=haircut,
            usable_probability=None,
            ev_gap=None,
            payout_slip_label="UNUSABLE",
            blocked_from_slip=True,
            code="PAYOUT_UNUSABLE",
            detail="Cannot compute usable_probability — model_prob or haircut missing.",
        )
        row.setdefault("gates", {})["payout_context"] = result
        row["blockers"].append("PAYOUT_CONTEXT:UNUSABLE:model_prob_missing")
        return result

    ev_gap = round(usable_prob - breakeven, 4)

    if ev_gap < 0:
        slip_label = "NEGATIVE_EV"
        blocked    = True
        passed     = False
        code       = "MARKET_QUALIFIED_BUT_SLIP_NEGATIVE"
        detail     = (
            f"{intended_format}: usable_prob={usable_prob:.1%} < "
            f"required={breakeven:.1%} (ev_gap={ev_gap:.1%}) — "
            f"blocked from slip. Prop may still have standalone market value."
        )
        # Apply terminal label
        row.setdefault("terminal_label", None)
        if not row.get("terminal_label"):
            row["terminal_label"] = PropLabel.MARKET_QUALIFIED_BUT_SLIP_NEGATIVE.value
        row["blockers"].append(
            f"PAYOUT_CONTEXT:SLIP_NEGATIVE:ev_gap={ev_gap:.1%}:{intended_format}"
        )
    elif ev_gap <= MARGINAL_THRESHOLD:
        slip_label = "MARGINAL_EV"
        blocked    = False
        passed     = True
        code       = "PAYOUT_MARGINAL_EV"
        detail     = (
            f"{intended_format}: usable_prob={usable_prob:.1%} vs "
            f"required={breakeven:.1%} (ev_gap=+{ev_gap:.1%}) — marginal, proceed with caution."
        )
    else:
        slip_label = "POSITIVE_EV"
        blocked    = False
        passed     = True
        code       = "PAYOUT_POSITIVE_EV"
        detail     = (
            f"{intended_format}: usable_prob={usable_prob:.1%} vs "
            f"required={breakeven:.1%} (ev_gap=+{ev_gap:.1%}) — eligible for slip."
        )

    result = _build_result(
        passed=passed,
        intended_format=intended_format,
        slip_breakeven=breakeven,
        required_per_leg_prob=breakeven,
        model_probability=model_prob,
        uncertainty_haircut=haircut,
        usable_probability=usable_prob,
        ev_gap=ev_gap,
        payout_slip_label=slip_label,
        blocked_from_slip=blocked,
        code=code,
        detail=detail,
    )
    row.setdefault("gates", {})["payout_context"] = result
    return result


def _straight_bet_result(
    row: dict[str, Any],
    intended_format: str,
    model_prob: float | None,
    haircut: float,
    usable_prob: float | None,
    sportsbook_edge: float | None,
) -> dict[str, Any]:
    """Handle straight sportsbook bet — use sportsbook edge, not PP table."""
    if sportsbook_edge is None:
        slip_label = "UNVERIFIED"
        passed     = False
        code       = "STRAIGHT_BET_EDGE_MISSING"
        detail     = "Straight bet: sportsbook_edge not provided — cannot compute EV."
        row["blockers"].append("PAYOUT_CONTEXT:STRAIGHT_BET_EDGE_MISSING")
    elif sportsbook_edge > 0:
        slip_label = "POSITIVE_EV"
        passed     = True
        code       = "STRAIGHT_BET_POSITIVE_EDGE"
        detail     = f"Straight bet: sportsbook_edge=+{sportsbook_edge:.2%} > 0 — positive EV."
    elif sportsbook_edge > -0.02:
        slip_label = "MARGINAL_EV"
        passed     = True
        code       = "STRAIGHT_BET_MARGINAL_EDGE"
        detail     = f"Straight bet: sportsbook_edge={sportsbook_edge:.2%} — marginal."
    else:
        slip_label = "NEGATIVE_EV"
        passed     = False
        code       = "STRAIGHT_BET_NEGATIVE_EDGE"
        detail     = f"Straight bet: sportsbook_edge={sportsbook_edge:.2%} < 0 — negative EV."
        row["blockers"].append(f"PAYOUT_CONTEXT:STRAIGHT_BET_NEGATIVE:{sportsbook_edge:.2%}")

    result = _build_result(
        passed=passed,
        intended_format=intended_format,
        slip_breakeven=None,
        required_per_leg_prob=None,
        model_probability=model_prob,
        uncertainty_haircut=haircut,
        usable_probability=usable_prob,
        ev_gap=sportsbook_edge,
        payout_slip_label=slip_label,
        blocked_from_slip=not passed,
        code=code,
        detail=detail,
    )
    row.setdefault("gates", {})["payout_context"] = result
    return result


def _build_result(
    passed: bool,
    intended_format: str,
    slip_breakeven: float | None,
    required_per_leg_prob: float | None,
    model_probability: float | None,
    uncertainty_haircut: float | None,
    usable_probability: float | None,
    ev_gap: float | None,
    payout_slip_label: str,
    blocked_from_slip: bool,
    code: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "passed":                passed,
        "intended_format":       intended_format,
        "slip_breakeven":        slip_breakeven,
        "required_per_leg_prob": required_per_leg_prob,
        "model_probability":     model_probability,
        "uncertainty_haircut":   uncertainty_haircut,
        "usable_probability":    usable_probability,
        "ev_gap":                ev_gap,
        "payout_slip_label":     payout_slip_label,
        "blocked_from_slip":     blocked_from_slip,
        "code":                  code,
        "detail":                detail,
    }


def _parse_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
