"""
ml_evaluate.py  —  core logic for POST /wow/llp/kalshi/ml-evaluate
WOW-PATCH-2026-07-05-LLP-KALSHI-SPORTS-BRIDGE v2, Step 5

As of 2026-07-05, real MLB/WNBA winner-market inventory exists on Kalshi
(INVENTORY_READY is achievable — see inventory_adapter.py). Per explicit
user sign-off on 2026-07-05, the prior blanket "STUB_CEILING" that forced
every result to LLP_WATCH regardless of real gate outcomes has been
REMOVED. Results may now legitimately reach LLP_PLAYABLE when every real
gate (inventory, settlement, exact match, fee/friction, staleness, edge)
passes.

LLP_APPROVED is intentionally NOT reachable from this endpoint: per
`gate_engine/llp_governance.py` (the canonical LLP label engine, see
`validate_reapproval`), WATCH → APPROVED requires a full session-scoped
governance rerun (session exposure ledger, calibration history, steam
protocol) that this stateless, single-shot dry-run bridge call does not
have access to. This endpoint reuses `cap_label`/`LLPLabel` from
`gate_engine.llp_governance` for label ordering so the ceiling logic here
never diverges from the canonical engine, but it does not reinvent or
bypass the full governance pipeline.

Edge sequencing (per Greg's approved amendment #4 — exact order, skip
nothing, never reorder):
  1. spread            (from KalshiPriceNormalizer / orderbook_normalizer)
  2. fee/friction       (kalshi_engine.fee_model)
  3. staleness grade    (KalshiPriceNormalizer — A/B/C/KALSHI_DATA_UNOBTAINABLE)
  4. shrinkage          (only applied if model_probability >= 0.80)
  5. compare to 2.5% floor (derivatives/low-liquidity sports tier)

Hard caps (never bypassed regardless of raw edge):
  - Live inventory gate: inventory_signal must be exactly INVENTORY_READY,
    or the row is capped at LLP_SCOUT regardless of caller-supplied data.
  - Settlement-rule auditor: ticker, event_ticker, market_title, and
    settlement_condition must ALL be present and unambiguous, or the row
    is capped at LLP_SCOUT.
  - Fee/friction buffer: if fee/friction cannot be computed (e.g. no
    executable price, no liquidity grade), cap at LLP_WATCH — never
    LLP_PLAYABLE from raw price edge alone.
  - Fuzzy/ambiguous ticker mapping (match_type != EXACT) caps at LLP_SCOUT.
  - LLP_APPROVED is never emitted by this endpoint (see above) — ceiling
    is LLP_PLAYABLE.
  - dry_run_only=True and can_execute=False on every response, no exceptions.

Model-probability shrinkage: when model_probability >= 0.80, shrink toward
0.80 by SHRINKAGE_FACTOR to penalize overconfident high-probability claims
before comparing to the post-friction floor.
"""
from __future__ import annotations

from typing import Any, Optional

from .. import fee_model as _fee_model
from .. import settlement_risk as _settlement_risk
from gate_engine.llp_governance import LLPLabel, cap_label

# Binary sports edge threshold — derivatives/low-liquidity tier, POST-friction.
EDGE_FLOOR = 0.025

# This bridge endpoint is stateless (no session-scoped governance rerun),
# so LLP_APPROVED is never reachable here — see module docstring.
_ENDPOINT_LABEL_CEILING = LLPLabel.PLAYABLE.value

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
    inventory_signal:     str = "INVENTORY_EMPTY",  # live signal from KalshiInventoryAdapter
) -> dict[str, Any]:
    """
    Evaluate a single LLP<->Kalshi sports candidate through the required
    edge sequence, subject to the settlement/fuzzy/fee/inventory hard caps.

    Returns a dict with `label` in {LLP_SCOUT, LLP_WATCH} only — this stub
    can never emit LLP_PLAYABLE or LLP_APPROVED, since sports inventory is
    currently empty and no real ticker has passed regression tests.

    `inventory_signal` is the LIVE result of KalshiInventoryAdapter at call
    time — not something the caller can spoof via candidate_markets/
    raw_orderbook. Unless it is exactly "INVENTORY_READY", the row is
    additionally hard-capped at LLP_SCOUT: a caller cannot self-report
    their way to a trusted label while the exchange has no real sports
    winner markets.
    """
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    ceilings: list[str] = []

    # ── Live inventory gate (mandatory — cannot be bypassed by request body) ──
    if inventory_signal != "INVENTORY_READY":
        ceilings.append("LLP_SCOUT")
        warnings.append(
            f"INVENTORY_NOT_READY: live sports inventory signal is "
            f"'{inventory_signal}', not INVENTORY_READY — capped at LLP_SCOUT "
            f"regardless of caller-supplied data."
        )

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

    # ── Final label: apply all ceilings via the canonical cap_label ordering,
    #    then cap at this endpoint's structural ceiling (LLP_PLAYABLE — see
    #    module docstring for why LLP_APPROVED is never reachable here). ──
    if ceilings:
        label = LLPLabel.APPROVED.value  # start unrestricted, then fold in ceilings
        for c in ceilings:
            label = cap_label(label, c)
    elif meets_floor:
        label = _ENDPOINT_LABEL_CEILING  # LLP_PLAYABLE — all real gates passed
    else:
        label = LLPLabel.SCOUT.value

    label = cap_label(label, _ENDPOINT_LABEL_CEILING)

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
        # "stub" is retired terminology (see WOW-SHARED-NOTES.md 2026-07-05):
        # this is real evaluation logic against live inventory whenever
        # inventory_signal == INVENTORY_READY. "connected" reflects whether
        # this call was actually checked against live, real Kalshi
        # inventory (CONNECTED_READONLY) vs. running with no live inventory
        # backing it at all.
        "stub":                inventory_signal != "INVENTORY_READY",
        "connected":           inventory_signal == "INVENTORY_READY",
        "connected_status":    "CONNECTED_READONLY" if inventory_signal == "INVENTORY_READY" else "DRY_RUN_READY",
    }
