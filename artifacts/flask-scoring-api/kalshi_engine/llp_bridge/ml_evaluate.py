"""
ml_evaluate.py  —  core logic for POST /wow/llp/kalshi/ml-evaluate
WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2, Step 5

Stub evaluation endpoint logic. Sports inventory is currently
INVENTORY_EMPTY, so this can only ever return stub/test-shaped output —
no label produced here may be trusted above LLP_SCOUT until real sports
inventory exists (see WOW-SHARED-NOTES.md carryover).

Edge sequencing (per Greg's approved amendment #4 — exact order, skip
nothing, never reorder):
  1. spread            (from KalshiPriceNormalizer / orderbook_normalizer)
  2. fee/friction       (kalshi_engine.fee_model)
  3. staleness grade    (KalshiPriceNormalizer — A/B/C/KALSHI_DATA_UNOBTAINABLE)
  4. shrinkage          (only applied if model_probability >= 0.80)
  5. compare to 2.5% floor (derivatives/low-liquidity sports tier)

Hard caps (never bypassed regardless of raw edge):
  - Settlement-rule auditor: ticker, event_ticker, market_title, and
    settlement_condition must ALL be present and unambiguous, or the row
    is capped at LLP_SCOUT.
  - Fee/friction buffer: if fee/friction cannot be computed (e.g. no
    executable price, no liquidity grade), cap at LLP_WATCH — never
    LLP_PLAYABLE/LLP_APPROVED from raw price edge alone.
  - Fuzzy/ambiguous ticker mapping (match_type != EXACT) caps at LLP_SCOUT.
  - dry_run_only=True and can_execute=False on every response, no exceptions.

Model-probability shrinkage: when model_probability >= 0.80, shrink toward
0.80 by SHRINKAGE_FACTOR to penalize overconfident high-probability claims
before comparing to the post-friction floor.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import fee_model as _fee_model
from .. import settlement_risk as _settlement_risk

# Binary sports edge threshold — derivatives/low-liquidity tier, POST-friction.
EDGE_FLOOR = 0.025

# Shrinkage applied to model probabilities >= this value.
SHRINKAGE_TRIGGER = 0.80
SHRINKAGE_FACTOR   = 0.5  # shrink 50% of the way back toward SHRINKAGE_TRIGGER


def _apply_shrinkage(model_probability: float) -> tuple[float, bool]:
    """Return (possibly-shrunk probability, was_shrunk)."""
    if model_probability >= SHRINKAGE_TRIGGER:
        shrunk = SHRINKAGE_TRIGGER + (model_probability - SHRINKAGE_TRIGGER) * (1 - SHRINKAGE_FACTOR)
        return round(shrunk, 6), True
    return model_probability, False


def evaluate_stub(
    ticker:               Optional[str],
    event_ticker:         Optional[str],
    market_title:         Optional[str],
    settlement_condition: Optional[str],
    model_probability:    Optional[float],
    match_type:           str,                  # "EXACT" | "FUZZY" | "NONE" from KalshiMarketMapper
    normalized_price:     Optional[dict[str, Any]],  # output of KalshiPriceNormalizer, or None
) -> dict[str, Any]:
    """
    Evaluate a single LLP<->Kalshi sports candidate through the required
    edge sequence, subject to the settlement/fuzzy/fee hard caps.

    Returns a dict with `label` in {LLP_SCOUT, LLP_WATCH} only — this stub
    can never emit LLP_PLAYABLE or LLP_APPROVED, since sports inventory is
    currently empty and no real ticker has passed regression tests.
    """
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    ceilings: list[str] = []

    # ── Settlement-rule auditor (mandatory — gates everything else) ─────────
    settlement_complete = all([ticker, event_ticker, market_title, settlement_condition])
    settlement_grade_result = None
    if settlement_complete:
        settlement_grade_result = _settlement_risk.grade_contract(
            title=market_title or "",
            settlement_condition=settlement_condition,
            resolution_source="Kalshi settlement rules (as captured)",
            category="sports_game_result",
            contract_ticker=ticker or "",
        )
        if settlement_grade_result["settlement_risk"] in ("HIGH", "REJECT") or \
           settlement_grade_result["resolution_clarity_grade"] in ("D", "F"):
            ceilings.append("LLP_SCOUT")
            warnings.append(
                "SETTLEMENT_AMBIGUOUS: ticker/event/title present but settlement "
                "wording graded ambiguous — capped at LLP_SCOUT."
            )
    else:
        ceilings.append("LLP_SCOUT")
        missing = [
            name for name, val in (
                ("ticker", ticker), ("event_ticker", event_ticker),
                ("market_title", market_title), ("settlement_condition", settlement_condition),
            ) if not val
        ]
        warnings.append(
            f"SETTLEMENT_INCOMPLETE: missing {missing} — cannot exceed LLP_SCOUT "
            f"per settlement-rule auditor."
        )

    # ── Fuzzy mapping cap ─────────────────────────────────────────────────
    if match_type != "EXACT":
        ceilings.append("LLP_SCOUT")
        warnings.append(
            f"MATCH_TYPE_{match_type}: only EXACT ticker matches are approval-eligible."
        )

    # ── Step 1: spread (recorded via liquidity_grade from the normalized book) ──
    steps.append({
        "step": 1, "name": "spread",
        "liquidity_grade": normalized_price.get("liquidity_grade") if normalized_price else None,
    })

    # ── Step 2: fee/friction ──────────────────────────────────────────────
    fee_result = None
    fee_unavailable = False
    executable_price = normalized_price.get("executable_price") if normalized_price else None
    liquidity_grade = normalized_price.get("liquidity_grade") if normalized_price else None

    if executable_price is None or liquidity_grade in (None, "F"):
        fee_unavailable = True
        ceilings.append("LLP_WATCH")
        warnings.append(
            "FEE_FRICTION_UNAVAILABLE: no executable price and/or liquidity grade — "
            "capped at LLP_WATCH per fee/friction buffer rule."
        )
    else:
        fee_result = _fee_model.calculate(
            entry_price=executable_price,
            yes_spread=None,
            liquidity_grade=liquidity_grade,
        )
    steps.append({"step": 2, "name": "fee_friction", "result": fee_result, "unavailable": fee_unavailable})

    # ── Step 3: staleness grade ───────────────────────────────────────────
    staleness_grade = normalized_price.get("staleness_grade") if normalized_price else "KALSHI_DATA_UNOBTAINABLE"
    if staleness_grade == "KALSHI_DATA_UNOBTAINABLE":
        ceilings.append("LLP_SCOUT")
        warnings.append("STALENESS_UNOBTAINABLE: orderbook age >=600s or missing timestamp.")
    steps.append({"step": 3, "name": "staleness_grade", "grade": staleness_grade})

    # ── Step 4: shrinkage (only if model_probability >= 0.80) ────────────
    shrunk_probability = model_probability
    was_shrunk = False
    if model_probability is not None:
        shrunk_probability, was_shrunk = _apply_shrinkage(model_probability)
    steps.append({"step": 4, "name": "shrinkage", "applied": was_shrunk, "shrunk_probability": shrunk_probability})

    # ── Step 5: compare to 2.5% floor (post-friction) ─────────────────────
    adjusted_edge = None
    meets_floor = False
    if model_probability is not None and fee_result is not None:
        raw_edge = round(shrunk_probability - executable_price, 6)
        adjusted_edge = round(raw_edge - fee_result["total_drag"], 6)
        meets_floor = adjusted_edge >= EDGE_FLOOR
        if not meets_floor:
            warnings.append(
                f"EDGE_BELOW_FLOOR: adjusted_edge={adjusted_edge} < EDGE_FLOOR={EDGE_FLOOR}"
            )
    steps.append({
        "step": 5, "name": "compare_to_floor",
        "adjusted_edge": adjusted_edge, "edge_floor": EDGE_FLOOR, "meets_floor": meets_floor,
    })

    # ── Final label: most-restrictive ceiling always wins; never above SCOUT
    #    for this stub regardless of edge math, since inventory is empty. ──
    label_priority = {"LLP_SCOUT": 0, "LLP_WATCH": 1}
    if ceilings:
        label = min(ceilings, key=lambda c: label_priority.get(c, 0))
    elif meets_floor:
        label = "LLP_WATCH"  # stub ceiling — cannot go higher until inventory is real
        warnings.append(
            "STUB_CEILING: edge math passed all gates, but this is a stub endpoint "
            "against empty sports inventory — capped at LLP_WATCH, not a real signal."
        )
    else:
        label = "LLP_SCOUT"

    return {
        "label":               label,
        "settlement_grade":    settlement_grade_result,
        "match_type":          match_type,
        "steps":               steps,
        "warnings":            warnings,
        "ceilings_applied":    sorted(set(ceilings)),
        "can_approve_bets":    False,
        "dry_run_only":        True,
        "can_execute":         False,
        "stub":                True,
        "connected":           False,
    }
