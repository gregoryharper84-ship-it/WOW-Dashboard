"""
card_finalizer.py — Hard structural gates + weakest-leg finalizer

WOW Stage 2: Reviewer-mandated code-level enforcement. These gates run
unconditionally after per-row classification — they cannot be bypassed
by prompt instructions, governance degradation, or Custom GPT override.

HARD CONSTANTS (never change at runtime):
    MAX_SAME_EVENT_LEGS = 2
    MAX_LIVE_MICRO_LEGS_PER_EVENT = 1
    REJECT_ALL_SAME_DIRECTION_CONCENTRATION = True
    REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS = True
    REQUIRE_WEAKEST_LEG_FINALIZER = True
    SHRINK_CARD_WHEN_NO_REPLACEMENT = True

HARD RULE:
    can_execute = False
    EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Module-level constants — these are policy, not config
# ---------------------------------------------------------------------------

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

# Permanent hard limits (not gated by freeze window)
MAX_SAME_EVENT_LEGS                   = 2
MAX_LIVE_MICRO_LEGS_PER_EVENT         = 1

# Directional concentration: block when ALL legs in same direction and N ≥ threshold
REJECT_ALL_SAME_DIRECTION_CONCENTRATION = True
SAME_DIRECTION_CONCENTRATION_MIN_LEGS   = 3  # only applies when N >= this

# Live-state requirement
REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS = True

# Weakest-leg finalizer
REQUIRE_WEAKEST_LEG_FINALIZER    = True
SHRINK_CARD_WHEN_NO_REPLACEMENT  = True
MIN_CARD_LEGS_AFTER_SHRINK       = 2          # never shrink below 2 legs

# Blocker prefixes
_BLOCKER_SAME_EVENT      = "CARD_GATE:SAME_EVENT_OVERLOAD"
_BLOCKER_LIVE_OVERLOAD   = "CARD_GATE:LIVE_MICRO_EVENT_OVERLOAD"
_BLOCKER_DIRECTION_CONC  = "CARD_GATE:ALL_SAME_DIRECTION_CONCENTRATED"
_BLOCKER_LIVE_STATE      = "CARD_GATE:LIVE_MARKET_MISSING_LIVE_STATE"
_BLOCKER_WEAKEST_REMOVED = "CARD_GATE:WEAKEST_LEG_REMOVED"


# ---------------------------------------------------------------------------
# Gate 1 — MAX_SAME_EVENT_LEGS (permanent, not freeze-only)
# ---------------------------------------------------------------------------

def _gate_same_event(rows: list[dict[str, Any]]) -> list[str]:
    """
    Block rows from an event that appears more than MAX_SAME_EVENT_LEGS times.
    Permanent — no freeze window check.

    Returns list of blocker strings applied to each offending row.
    """
    game_counts: Counter = Counter()
    for row in rows:
        game = (row.get("game") or row.get("game_id") or "").lower().strip()
        if game:
            game_counts[game] += 1

    blockers_applied: list[str] = []
    for row in rows:
        game = (row.get("game") or row.get("game_id") or "").lower().strip()
        if game and game_counts[game] > MAX_SAME_EVENT_LEGS:
            blocker = (
                f"{_BLOCKER_SAME_EVENT}:{game_counts[game]}x_game:{game}"
                f"_(max_{MAX_SAME_EVENT_LEGS})"
            )
            if blocker not in (row.get("blockers") or []):
                row.setdefault("blockers", []).append(blocker)
            row.setdefault("gates", {}).setdefault("card_finalizer", {}).update({
                "same_event_gate_failed": True,
                "same_event_count":       game_counts[game],
                "same_event_limit":       MAX_SAME_EVENT_LEGS,
            })
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_BAD_STRUCTURE.value
            blockers_applied.append(blocker)

    return blockers_applied


# ---------------------------------------------------------------------------
# Gate 2 — MAX_LIVE_MICRO_LEGS_PER_EVENT
# ---------------------------------------------------------------------------

def _gate_live_micro_event(rows: list[dict[str, Any]]) -> list[str]:
    """
    Block a card with more than MAX_LIVE_MICRO_LEGS_PER_EVENT live-micro legs
    from the same event.

    A row is identified as live-micro when:
      row.get("market_phase") == "live" OR
      row.get("pregame_or_live") == "live" OR
      any blocker contains "LIVE_STATE"
    """
    live_event_counts: Counter = Counter()
    for row in rows:
        if _is_live_micro_row(row):
            game = (row.get("game") or row.get("game_id") or "").lower().strip()
            if game:
                live_event_counts[game] += 1

    blockers_applied: list[str] = []
    for row in rows:
        if not _is_live_micro_row(row):
            continue
        game = (row.get("game") or row.get("game_id") or "").lower().strip()
        if game and live_event_counts[game] > MAX_LIVE_MICRO_LEGS_PER_EVENT:
            blocker = (
                f"{_BLOCKER_LIVE_OVERLOAD}:{live_event_counts[game]}x_live"
                f"_legs_game:{game}_(max_{MAX_LIVE_MICRO_LEGS_PER_EVENT})"
            )
            if blocker not in (row.get("blockers") or []):
                row.setdefault("blockers", []).append(blocker)
            row.setdefault("gates", {}).setdefault("card_finalizer", {}).update({
                "live_micro_event_gate_failed": True,
                "live_event_count":             live_event_counts[game],
                "live_event_limit":             MAX_LIVE_MICRO_LEGS_PER_EVENT,
            })
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_BAD_STRUCTURE.value
            blockers_applied.append(blocker)

    return blockers_applied


def _is_live_micro_row(row: dict[str, Any]) -> bool:
    if row.get("market_phase") == "live":
        return True
    if row.get("pregame_or_live") == "live":
        return True
    if any("LIVE_STATE" in b for b in (row.get("blockers") or [])):
        return True
    if (row.get("gates") or {}).get("live_micro_market", {}).get("live_state_status"):
        return True
    return False


# ---------------------------------------------------------------------------
# Gate 3 — REJECT_ALL_SAME_DIRECTION_CONCENTRATION
# ---------------------------------------------------------------------------

def _gate_directional_concentration(rows: list[dict[str, Any]]) -> list[str]:
    """
    Block a card where ALL legs share the same direction (MORE or LESS)
    and the card has at least SAME_DIRECTION_CONCENTRATION_MIN_LEGS legs.

    A five-leg all-LESS card is a single-narrative fragility risk that must
    be identified before any leg reaches the final card.

    Writes:
        row["gates"]["card_finalizer"]["directional_concentration"] = "ALL_LESS" | "ALL_MORE" | "MIXED"
    """
    if not REJECT_ALL_SAME_DIRECTION_CONCENTRATION:
        return []

    if len(rows) < SAME_DIRECTION_CONCENTRATION_MIN_LEGS:
        return []

    directions = [
        (row.get("direction") or "").upper()
        for row in rows
        if (row.get("direction") or "").upper() in ("MORE", "LESS")
    ]

    if not directions:
        return []

    unique_dirs = set(directions)
    if len(unique_dirs) > 1:
        # Mixed directions — annotate but do not block
        for row in rows:
            row.setdefault("gates", {}).setdefault("card_finalizer", {}).update({
                "directional_concentration": "MIXED",
            })
        return []

    # All same direction
    dominant = directions[0]
    concentration_label = f"ALL_{dominant}"
    blocker = (
        f"{_BLOCKER_DIRECTION_CONC}:{concentration_label}"
        f"_{len(rows)}_LEGS_ALL_{dominant}"
    )
    blockers_applied: list[str] = []

    for row in rows:
        if blocker not in (row.get("blockers") or []):
            row.setdefault("blockers", []).append(blocker)
        row.setdefault("gates", {}).setdefault("card_finalizer", {}).update({
            "directional_concentration":        concentration_label,
            "directional_concentration_failed": True,
            "total_legs":                       len(rows),
            "dominant_direction":               dominant,
        })
        if row.get("terminal_label") is None:
            row["terminal_label"] = PropLabel.REJECT_BAD_STRUCTURE.value
        blockers_applied.append(blocker)

    return blockers_applied


# ---------------------------------------------------------------------------
# Gate 4 — REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS
# ---------------------------------------------------------------------------

def _gate_live_state_required(rows: list[dict[str, Any]]) -> list[str]:
    """
    Block live-market rows where no live state was supplied or the live state
    was stale/missing. If REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS is True, this
    gate is unconditional — the row cannot reach FINAL_APPROVED without
    a fresh live state.
    """
    if not REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS:
        return []

    blockers_applied: list[str] = []
    for row in rows:
        if not _is_live_micro_row(row):
            continue
        # Check if the live micro result passed
        live_gate = (row.get("gates") or {}).get("live_micro_market", {})
        live_passed = live_gate.get("live_state_passed", True)  # default True = not a live row
        live_status = live_gate.get("live_state_status")

        if live_status and not live_passed:
            blocker = f"{_BLOCKER_LIVE_STATE}:status={live_status}"
            if blocker not in (row.get("blockers") or []):
                row.setdefault("blockers", []).append(blocker)
            row.setdefault("gates", {}).setdefault("card_finalizer", {}).update({
                "live_state_gate_failed": True,
                "live_state_status":      live_status,
            })
            if row.get("terminal_label") is None:
                row["terminal_label"] = PropLabel.REJECT_BAD_STRUCTURE.value
            blockers_applied.append(blocker)

    return blockers_applied


# ---------------------------------------------------------------------------
# Gate 5 — Weakest-leg finalizer
# ---------------------------------------------------------------------------

def _score_row(row: dict[str, Any]) -> float:
    """
    Score a row by its quality for weakest-leg ranking.
    Lower score = weaker leg. Uses calibrated_probability, then edge_score.
    """
    # Prefer calibrated_probability from prob_ledger result
    cal_prob = row.get("calibrated_probability")
    if cal_prob is not None:
        try:
            return float(cal_prob)
        except (TypeError, ValueError):
            pass

    # Fall back to edge_score from ev_gate
    edge = (row.get("gates") or {}).get("ev_gate", {}).get("edge_score")
    if edge is not None:
        try:
            return 0.5 + float(edge) / 100.0
        except (TypeError, ValueError):
            pass

    # Fall back to calibrated_lower_bound from live micro
    cb = row.get("calibrated_lower_bound")
    if cb is not None:
        try:
            return float(cb)
        except (TypeError, ValueError):
            pass

    # No signal available — treat as average
    return 0.50


def identify_weakest_leg(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Rank all rows by quality score and identify the weakest leg.

    Returns:
        {
          ranked_rows: list of (row, score, rank)  — 1=weakest
          weakest_row_id: str | None
          weakest_score: float | None
          weakest_gap: float       — gap between weakest and 2nd weakest
        }
    """
    if not rows:
        return {"ranked_rows": [], "weakest_row_id": None, "weakest_score": None, "weakest_gap": 0.0}

    scored = [(row, _score_row(row)) for row in rows]
    scored.sort(key=lambda x: x[1])   # ascending — index 0 = weakest

    ranked = []
    for rank, (row, score) in enumerate(scored, start=1):
        ranked.append((row, score, rank))
        row.setdefault("gates", {}).setdefault("card_finalizer", {}).update({
            "weakest_leg_rank":  rank,
            "weakest_leg_score": round(score, 4),
        })

    weakest_row, weakest_score = scored[0]
    gap = round(scored[1][1] - weakest_score, 4) if len(scored) > 1 else 0.0

    return {
        "ranked_rows":     ranked,
        "weakest_row_id":  weakest_row.get("row_id"),
        "weakest_score":   round(weakest_score, 4),
        "weakest_gap":     gap,
    }


def finalize_card(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Run the weakest-leg finalizer as a code gate (not a prompt instruction).

    If REQUIRE_WEAKEST_LEG_FINALIZER is True:
      1. Identify the weakest leg.
      2. If it is significantly below the rest (gap > 0.05) and removing it
         would not shrink the card below MIN_CARD_LEGS_AFTER_SHRINK, mark it
         as WEAKEST_LEG_REMOVED (it is NOT surfaced in final_card).
      3. If removing would shrink below minimum and SHRINK_CARD_WHEN_NO_REPLACEMENT
         is True, still remove and set "card_shrunk=True".

    Does NOT drop rows from the list — marks them with a terminal_label override
    so the pipeline's existing output logic excludes them from final_card.

    Returns:
        {
          finalizer_ran: bool
          weakest_row_id: str | None
          weakest_removed: bool
          card_shrunk: bool
          remaining_legs: int
          weakest_leg_score: float | None
          weakest_gap: float
        }
    """
    if not REQUIRE_WEAKEST_LEG_FINALIZER:
        return {"finalizer_ran": False}

    ranking = identify_weakest_leg(rows)
    weakest_row_id = ranking["weakest_row_id"]
    weakest_score  = ranking["weakest_score"]
    gap            = ranking["weakest_gap"]

    # Only remove if gap is material (> 0.05 quality points) and card is large enough
    weakest_removed = False
    card_shrunk     = False
    remaining_legs  = len(rows)

    if weakest_row_id and gap > 0.05:
        can_remove_without_shrink = (len(rows) - 1) >= MIN_CARD_LEGS_AFTER_SHRINK

        should_remove = can_remove_without_shrink or SHRINK_CARD_WHEN_NO_REPLACEMENT

        if should_remove:
            for row in rows:
                if row.get("row_id") == weakest_row_id:
                    blocker = (
                        f"{_BLOCKER_WEAKEST_REMOVED}"
                        f":score={weakest_score:.4f}"
                        f":gap={gap:.4f}"
                    )
                    if blocker not in (row.get("blockers") or []):
                        row.setdefault("blockers", []).append(blocker)
                    row.setdefault("gates", {}).setdefault("card_finalizer", {}).update({
                        "weakest_leg_removed":  True,
                        "weakest_removal_gap":  gap,
                    })
                    # Override terminal label so it does not appear in final_card
                    row["terminal_label"] = PropLabel.NO_PLAY.value
                    weakest_removed = True
                    remaining_legs  = len(rows) - 1
                    if not can_remove_without_shrink:
                        card_shrunk = True
                    break

    return {
        "finalizer_ran":      True,
        "weakest_row_id":     weakest_row_id,
        "weakest_removed":    weakest_removed,
        "card_shrunk":        card_shrunk,
        "remaining_legs":     remaining_legs,
        "weakest_leg_score":  weakest_score,
        "weakest_gap":        gap,
    }


# ---------------------------------------------------------------------------
# Orchestrator — run ALL hard gates in one call
# ---------------------------------------------------------------------------

def run_hard_gates(
    rows: list[dict[str, Any]],
    skip_same_event:        bool = False,
    skip_live_overload:     bool = False,
    skip_direction_conc:    bool = False,
    skip_live_state_req:    bool = False,
) -> dict[str, Any]:
    """
    Run all hard structural gates unconditionally after slip-level gates.

    Gates:
      1. same_event        — MAX_SAME_EVENT_LEGS = 2 (permanent)
      2. live_overload     — MAX_LIVE_MICRO_LEGS_PER_EVENT = 1
      3. direction_conc    — REJECT_ALL_SAME_DIRECTION_CONCENTRATION
      4. live_state_req    — REQUIRE_LIVE_STATE_FOR_LIVE_MARKETS

    None of these gates are optional in production. The skip_* flags are
    available ONLY for isolated unit testing.

    Returns a report dict; all mutations are applied in-place to rows.
    """
    report: dict[str, Any] = {
        "can_execute":   False,
        "execution_rule": EXECUTION_RULE,
        "gates_run":     [],
        "total_blockers_added": 0,
    }

    g1 = [] if skip_same_event     else _gate_same_event(rows)
    g2 = [] if skip_live_overload  else _gate_live_micro_event(rows)
    g3 = [] if skip_direction_conc else _gate_directional_concentration(rows)
    g4 = [] if skip_live_state_req else _gate_live_state_required(rows)

    all_blockers = g1 + g2 + g3 + g4

    report["gates_run"] = [
        "same_event_gate"          if not skip_same_event     else None,
        "live_micro_event_gate"    if not skip_live_overload  else None,
        "directional_concentration" if not skip_direction_conc else None,
        "live_state_required"      if not skip_live_state_req else None,
    ]
    report["gates_run"] = [g for g in report["gates_run"] if g]
    report["total_blockers_added"] = len(all_blockers)
    report["blockers_by_gate"] = {
        "same_event":     g1,
        "live_overload":  g2,
        "direction_conc": g3,
        "live_state_req": g4,
    }

    # Summary per row
    report["row_summary"] = [
        {
            "row_id":         row.get("row_id"),
            "player":         row.get("player"),
            "terminal_label": row.get("terminal_label"),
            "card_gate":      row.get("gates", {}).get("card_finalizer", {}),
        }
        for row in rows
    ]

    return report
