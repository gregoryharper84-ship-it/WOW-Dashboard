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
  - Sportsbook consensus no-vig gate (Kalshi Sports ML Edge Rule — WNBA/
    MLB Only, approved 2026-07-05): a no-vig fair probability for the
    exact Kalshi YES-side team must be AVAILABLE from
    kalshi_engine.llp_bridge.consensus_odds. If it is NOT_CALLED/FAILED,
    cap at LLP_SCOUT (no consensus at all — never compute a money edge
    from model_probability alone). If it is STALE or CONTRADICTORY, or
    single_book_fallback=True, cap at LLP_WATCH (a real consensus quote
    exists but is not trustworthy/independent enough to approve off).
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

Edge floor comparison (post-2026-07-05 consensus amendment): the post-
friction edge must clear EDGE_FLOOR against BOTH the LLP model probability
AND the sportsbook no-vig consensus fair probability. Only comparing
against the LLP model (as before the amendment) let an overconfident or
buggy model manufacture edge on its own say-so — the consensus figure is
an independent floor, not merely informational.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .. import fee_model as _fee_model
from .. import settlement_risk as _settlement_risk
from gate_engine.llp_governance import LLPLabel, cap_label

# ── Edge floors (POST-friction, market-type-aware) ──────────────────────────
# main_winner  = KXMLBGAME/KXWNBAGAME series — most liquid, 1.5% floor
# derivative   = F5, 3-way, run-total, props — less liquid, 2.5% floor
EDGE_FLOOR_MAIN       = 0.015  # MLB/WNBA main winner markets
EDGE_FLOOR_DERIVATIVE = 0.025  # derivative / low-liquidity sports tier
EDGE_FLOOR = EDGE_FLOOR_DERIVATIVE  # backward-compat alias (old callers)

# Final-lock recheck window: a fresh final-lock check must have run within
# this many seconds before the ml-evaluate call; otherwise cap at LLP_WATCH.
FINAL_LOCK_WINDOW_SECONDS = 1800  # 30 minutes

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
    consensus_odds:       Optional[dict[str, Any]] = None,  # output of consensus_odds.get_consensus_no_vig_probability, or None
    # ── WOW-PATCH-2026-07-07-KALSHI-FINAL-LOCK-EDGE-DISCOVERY ───────────
    market_type:              str            = "main_winner",  # "main_winner" | "derivative"
    trading_active:           Optional[bool] = None,           # False → cap LLP_SCOUT
    final_lock_rechecked_at:  Optional[str]  = None,           # ISO-8601; None/stale → cap LLP_WATCH
    kalshi_orderbook_source:  str            = "no_ticker",    # "direct_api" | "caller_supplied" | "fetch_failed" | "no_ticker"
) -> dict[str, Any]:
    """
    Evaluate a single LLP<->Kalshi sports candidate through the required
    edge sequence, subject to the settlement/fuzzy/fee/inventory/consensus
    hard caps.

    Returns a dict with `label` in {LLP_SCOUT, LLP_WATCH, LLP_PLAYABLE} —
    LLP_PLAYABLE is only reachable when every real gate (inventory,
    settlement, exact match, sportsbook consensus, fee/friction,
    staleness, edge) passes. LLP_APPROVED is never emitted here (see
    module docstring).

    `inventory_signal` is the LIVE result of KalshiInventoryAdapter at call
    time — not something the caller can spoof via candidate_markets/
    raw_orderbook. Unless it is exactly "INVENTORY_READY", the row is
    additionally hard-capped at LLP_SCOUT: a caller cannot self-report
    their way to a trusted label while the exchange has no real sports
    winner markets.

    `consensus_odds` is the LIVE result of
    `consensus_odds.get_consensus_no_vig_probability` at call time — same
    non-spoofable contract as `inventory_signal`. See module docstring for
    the ODDS_CONSENSUS_* hard-cap rules.
    """
    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    ceilings: list[str] = []
    blocker_tags: list[str] = []

    # ── Select market-type-aware edge floor ──────────────────────────────────
    edge_floor = EDGE_FLOOR_MAIN if market_type == "main_winner" else EDGE_FLOOR_DERIVATIVE

    # ── WOW-PATCH-2026-07-07 Gate A: orderbook source enforcement ────────────
    # Web UI / caller-supplied prices CANNOT satisfy direct API freshness.
    # Only orderbook_fetcher.fetch() produces source="direct_api".
    # Any other source caps at LLP_WATCH so that a caller cannot manufacture
    # LLP_PLAYABLE off non-executable display prices.
    if kalshi_orderbook_source != "direct_api":
        ceilings.append("LLP_WATCH")
        blocker_tags.append("KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API")
        warnings.append(
            f"KALSHI_ORDERBOOK_SOURCE_NOT_DIRECT_API: source='{kalshi_orderbook_source}' — "
            f"only server-side direct_api orderbook fetches satisfy executable price "
            f"freshness; web UI / caller-supplied prices are display-only, capped at LLP_WATCH."
        )

    # ── WOW-PATCH-2026-07-07 Gate B: market status / trading_active ──────────
    # If the market is known to not be open/trading, no executable price can
    # exist — cap at LLP_SCOUT so the row cannot ever reach LLP_PLAYABLE.
    if trading_active is False:
        ceilings.append("LLP_SCOUT")
        blocker_tags.append("MARKET_NOT_TRADING")
        warnings.append(
            "MARKET_NOT_TRADING: Kalshi market is not open/trading_active — "
            "no executable price can exist; capped at LLP_SCOUT."
        )

    # ── WOW-PATCH-2026-07-07 Gate C: final-lock recheck freshness ────────────
    # The final-lock recheck must have run within FINAL_LOCK_WINDOW_SECONDS
    # before this evaluate call; otherwise cap at LLP_WATCH.
    final_lock_fresh = False
    final_lock_age_seconds: float | None = None
    if final_lock_rechecked_at:
        try:
            fl_ts = datetime.fromisoformat(
                final_lock_rechecked_at.replace("Z", "+00:00")
            )
            if fl_ts.tzinfo is None:
                fl_ts = fl_ts.replace(tzinfo=timezone.utc)
            final_lock_age_seconds = (datetime.now(tz=timezone.utc) - fl_ts).total_seconds()
            final_lock_fresh = final_lock_age_seconds <= FINAL_LOCK_WINDOW_SECONDS
        except (ValueError, TypeError):
            final_lock_age_seconds = None

    if not final_lock_fresh:
        ceilings.append("LLP_WATCH")
        blocker_tags.append("FINAL_LOCK_RECHECK_REQUIRED")
        if final_lock_rechecked_at is None:
            warnings.append(
                "FINAL_LOCK_RECHECK_REQUIRED: final_lock_rechecked_at not supplied — "
                f"a final-lock recheck within {FINAL_LOCK_WINDOW_SECONDS}s is required "
                f"before a candidate may advance beyond LLP_WATCH."
            )
        else:
            warnings.append(
                f"FINAL_LOCK_RECHECK_REQUIRED: final_lock_rechecked_at is "
                f"{final_lock_age_seconds:.0f}s ago, exceeds window of "
                f"{FINAL_LOCK_WINDOW_SECONDS}s — cap at LLP_WATCH."
            )

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

    # ── Sportsbook consensus no-vig gate (mandatory — Kalshi Sports ML Edge
    #    Rule, WNBA/MLB Only, approved 2026-07-05). ml-evaluate must never
    #    compute a money edge from model_probability alone; a real no-vig
    #    consensus quote is required, fresh, and non-contradictory. ─────────
    consensus_status = (consensus_odds or {}).get("status", "NOT_CALLED")
    consensus_fair_probability = (consensus_odds or {}).get("consensus_fair_probability")
    consensus_single_book = bool((consensus_odds or {}).get("single_book_fallback"))
    blocker_tags.extend((consensus_odds or {}).get("blocker_tags") or [])

    # NOTE: check status BEFORE the generic "probability is None" fallback —
    # a STALE result legitimately carries consensus_fair_probability=None
    # (stale books are never used to compute a probability) and must still
    # be capped at LLP_WATCH, not misrouted into the harsher LLP_SCOUT
    # NOT_CALLED/FAILED branch just because the probability field is empty.
    if consensus_status in ("NOT_CALLED", "FAILED"):
        ceilings.append("LLP_SCOUT")
        warnings.append(
            f"ODDS_CONSENSUS_{consensus_status}: no sportsbook no-vig consensus "
            f"fair probability available — capped at LLP_SCOUT; a money edge "
            f"can never be computed from model_probability alone."
        )
    elif consensus_status == "STALE":
        ceilings.append("LLP_WATCH")
        warnings.append(
            "ODDS_CONSENSUS_STALE: newest usable sportsbook quote exceeds the "
            "freshness window — capped at LLP_WATCH."
        )
    elif consensus_status == "CONTRADICTORY":
        ceilings.append("LLP_WATCH")
        warnings.append(
            "ODDS_CONSENSUS_CONTRADICTORY: fresh sportsbook books disagree beyond "
            "the tolerance spread — capped at LLP_WATCH, consensus not trustworthy."
        )
    elif consensus_fair_probability is None:
        # Defensive fallback: an AVAILABLE-labeled result with no usable
        # probability should never happen from a well-formed consensus_odds
        # payload, but if it does, never compute an edge from nothing.
        ceilings.append("LLP_SCOUT")
        warnings.append(
            f"ODDS_CONSENSUS_{consensus_status}_NO_PROBABILITY: consensus status "
            f"'{consensus_status}' but no usable fair probability was returned — "
            f"capped at LLP_SCOUT."
        )
    elif consensus_single_book:
        ceilings.append("LLP_WATCH")
        warnings.append(
            "ODDS_CONSENSUS_SINGLE_BOOK: only one sportsbook quote available — "
            "a single raw ML price is never treated as fair probability; "
            "capped at LLP_WATCH."
        )
    steps.append({
        "step": "consensus_gate", "name": "sportsbook_no_vig_consensus",
        "status": consensus_status,
        "consensus_fair_probability": consensus_fair_probability,
        "single_book_fallback": consensus_single_book,
    })

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

    # ── Step 5: compare to 2.5% floor (post-friction), against BOTH the LLP
    #    model probability AND the sportsbook no-vig consensus (2026-07-05
    #    amendment — see module docstring). Both must independently clear
    #    EDGE_FLOOR; the model alone can never manufacture approval-eligible
    #    edge without independent sportsbook corroboration. ──────────────
    model_adjusted_edge = None
    consensus_adjusted_edge = None
    meets_floor = False
    if fee_result is not None:
        if model_probability is not None:
            model_raw_edge = round(shrunk_probability - executable_price, 6)
            model_adjusted_edge = round(model_raw_edge - fee_result["total_drag"], 6)
        if consensus_fair_probability is not None:
            consensus_raw_edge = round(consensus_fair_probability - executable_price, 6)
            consensus_adjusted_edge = round(consensus_raw_edge - fee_result["total_drag"], 6)

        model_meets_floor = model_adjusted_edge is not None and model_adjusted_edge >= edge_floor
        consensus_meets_floor = consensus_adjusted_edge is not None and consensus_adjusted_edge >= edge_floor
        meets_floor = model_meets_floor and consensus_meets_floor

        if not meets_floor:
            if not model_meets_floor:
                warnings.append(
                    f"EDGE_BELOW_FLOOR (model): adjusted_edge={model_adjusted_edge} < edge_floor={edge_floor} "
                    f"(market_type={market_type})"
                )
            if not consensus_meets_floor:
                warnings.append(
                    f"EDGE_BELOW_FLOOR (consensus): adjusted_edge={consensus_adjusted_edge} < edge_floor={edge_floor} "
                    f"(market_type={market_type})"
                )
    steps.append({
        "step": 5, "name": "compare_to_floor",
        "model_adjusted_edge": model_adjusted_edge,
        "consensus_adjusted_edge": consensus_adjusted_edge,
        "edge_floor": edge_floor, "market_type": market_type, "meets_floor": meets_floor,
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
        "blocker_tags":        sorted(set(blocker_tags)),
        "consensus_odds":      consensus_odds,
        "can_approve_bets":    False,
        "dry_run_only":        True,
        "can_execute":         False,
        # WOW-PATCH-2026-07-07 fields
        "market_type":             market_type,
        "edge_floor":              edge_floor,
        "kalshi_orderbook_source": kalshi_orderbook_source,
        "trading_active":          trading_active,
        "final_lock_fresh":        final_lock_fresh,
        "final_lock_age_seconds":  final_lock_age_seconds,
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
