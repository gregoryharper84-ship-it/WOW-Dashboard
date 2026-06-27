"""
final_lock_orchestrator.py  —  WOW v16 Gate-Engine Master Orchestrator
POST /gate-engine/final-lock

Runs all five patch gates in order and returns a single unified decision.
Individual gate endpoints remain for testing. This is the truth source for
the Final Lock Dashboard.

Gate run order (fail-fast on terminal rejects):
  0. Settlement loopback  — freshness check, sets global ceiling
  1. Sharp anchor         — directional market alignment (skipped if no sharp data)
  2. House rules          — platform settlement vulnerability
  3. Execution friction   — final-execution staleness/drift
  4. Correlation gate     — slip leg overlap (skipped if <2 legs)

Label ceiling hierarchy:
  FINAL_APPROVED       — all gates pass, settlement fresh, edge ≥ threshold
  MONEY_QUALIFIED      — blocked only by execution cautions (not hard rejects)
  MARKET_VERIFIED_HOLD — sharp anchor neutral, some gate cautions
  MODEL_QUALIFIED_HOLD — settlement stale or market data incomplete
  RESEARCH_INTEREST    — edge below threshold but no reject trigger
  REJECT_*             — any terminal reject from any gate

can_approve_bets: False is enforced unconditionally.
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel
from . import settlement_loopback as _sl
from . import sharp_anchor        as _sa
from . import house_rules         as _hr
from . import execution_friction  as _ef
from . import correlation_gate    as _cg

# ---------------------------------------------------------------------------
# Label hierarchy (index = ceiling rank; higher = more permissive)
# ---------------------------------------------------------------------------

_LABEL_RANK: dict[str, int] = {
    PropLabel.REJECT_SHARP_CONFLICT.value:            0,
    PropLabel.REJECT_FALLING_KNIFE.value:             0,
    PropLabel.REJECT_HOUSE_RULES_VULNERABILITY.value: 0,
    PropLabel.REJECT_EXECUTION_STALE.value:           0,
    PropLabel.REJECT_PAYOUT_CHANGED.value:            0,
    PropLabel.REJECT_LINE_MOVED_AGAINST_SIDE.value:   0,
    PropLabel.REJECT_POWER_CORRELATED.value:          0,
    PropLabel.RESEARCH_INTEREST.value:                1,
    PropLabel.MODEL_QUALIFIED_HOLD.value:             2,
    PropLabel.MARKET_VERIFIED_HOLD.value:             3,
    PropLabel.MONEY_QUALIFIED.value:                  4,
    PropLabel.FINAL_APPROVED.value:                   5,
}

_REJECT_VALUES = {l for l, r in _LABEL_RANK.items() if r == 0}

_APPROVAL_PRIORITY = [
    PropLabel.FINAL_APPROVED.value,
    PropLabel.MONEY_QUALIFIED.value,
    PropLabel.MARKET_VERIFIED_HOLD.value,
    PropLabel.MODEL_QUALIFIED_HOLD.value,
    PropLabel.RESEARCH_INTEREST.value,
]


def _lower_ceiling(current: str, candidate: str) -> str:
    """Return whichever label has the lower rank (more restrictive)."""
    r_current   = _LABEL_RANK.get(current,   5)
    r_candidate = _LABEL_RANK.get(candidate, 5)
    return current if r_current <= r_candidate else candidate


def run(params: dict[str, Any]) -> dict[str, Any]:
    """
    Run all gates and return a unified final-lock result.

    Parameters (from POST body JSON):
      # Prop identity
      player            str
      sport             str
      market            str
      side              "MORE" | "LESS" | "OVER" | "UNDER"
      pp_line           float
      platform          str   default "prizepicks"

      # Sharp market data (optional — gate skipped if absent)
      sharp_no_vig_prob float | None   (0–1, probability for OVER/MORE)
      sharp_fair_line   float | None

      # Model / EV data (optional)
      model_probability     float | None
      shrinkage_probability float | None   (used as usable_probability)
      per_leg_breakeven     float | None
      payout_multiplier     float | None

      # Execution context (optional — execution gate skipped if absent)
      analysis_pp_line       float | None
      analysis_payout        float | None
      current_payout         float | None
      analysis_slip_type     str | None
      current_slip_type      str | None
      analysis_pick_count    int | None
      current_pick_count     int | None
      line_timestamp_utc     str | None   (ISO-8601)
      player_status          str | None

      # House rules inputs (optional)
      injury_flag            bool  default False
      minutes_dependency     bool  default True
      market_type            str   default "player_prop"

      # Slip legs for correlation gate (optional — skipped if <2 legs)
      slip_legs   list[{player, stat, side, team?}]
      slip_type   str | None

      # Settlement (always checked via DB)
      skip_settlement_check  bool  default False
    """
    side      = (params.get("side") or "MORE").upper().strip()
    platform  = (params.get("platform") or "prizepicks").lower()
    pp_line   = params.get("pp_line")
    player    = params.get("player") or ""
    market    = params.get("market") or ""

    model_prob    = params.get("model_probability")
    shrinkage     = params.get("shrinkage_probability")
    per_leg_be    = params.get("per_leg_breakeven")
    payout_mult   = params.get("payout_multiplier")

    gate_results:     dict[str, Any] = {}
    blocking_gates:   list[str]      = []
    warnings:         list[str]      = []
    required_actions: list[str]      = []
    terminal_reject:  str | None     = None

    # Ceiling starts at FINAL_APPROVED; gates can only lower it.
    ceiling = PropLabel.FINAL_APPROVED.value

    # ── Gate 0: Settlement freshness ────────────────────────────────────────
    settlement_stale = False
    if not params.get("skip_settlement_check"):
        fresh = _sl.check_freshness()
        gate_results["settlement_loopback"] = fresh
        if fresh.get("stale"):
            settlement_stale = True
            ceiling = _lower_ceiling(ceiling, PropLabel.MODEL_QUALIFIED_HOLD.value)
            blocking_gates.append("SETTLEMENT_LOOPBACK_STALE")
            required_actions.append(
                "Ingest recent settled results via POST /lock-api/settle "
                f"(last entry: {fresh.get('last_ingested_at') or 'never'})"
            )
        elif fresh.get("code") == "SETTLEMENT_DB_UNAVAILABLE":
            warnings.append("SETTLEMENT_DB_UNAVAILABLE")
            ceiling = _lower_ceiling(ceiling, PropLabel.MODEL_QUALIFIED_HOLD.value)
    else:
        gate_results["settlement_loopback"] = {"skipped": True}

    # ── Gate 1: Sharp anchor ─────────────────────────────────────────────────
    snvp = params.get("sharp_no_vig_prob")
    sfl  = params.get("sharp_fair_line")
    if snvp is not None or sfl is not None:
        sa = _sa.check_standalone(
            pp_line           = pp_line,
            sharp_fair_line   = sfl,
            sharp_no_vig_prob = snvp,
            side              = side,
        )
        gate_results["sharp_anchor"] = sa
        if sa.get("reject"):
            tl = sa.get("terminal_label")
            terminal_reject = terminal_reject or tl
            blocking_gates.append(f"SHARP_ANCHOR:{tl}")
            required_actions.append("Do not play this side — sharp market opposes direction.")
        elif sa.get("anchor_status") == "MARKET_VERIFIED_HOLD_STALE":
            warnings.append("MARKET_VERIFIED_HOLD_STALE")
        elif sa.get("anchor_status") == "NO_SHARP_DATA":
            warnings.append("NO_SHARP_DATA")
            ceiling = _lower_ceiling(ceiling, PropLabel.MARKET_VERIFIED_HOLD.value)
    else:
        gate_results["sharp_anchor"] = {"skipped": True, "reason": "no sharp data provided"}
        warnings.append("NO_SHARP_DATA")
        ceiling = _lower_ceiling(ceiling, PropLabel.MARKET_VERIFIED_HOLD.value)

    # ── Gate 2: House rules ───────────────────────────────────────────────────
    if not terminal_reject:
        hr = _hr.check_standalone(
            platform           = platform,
            injury_flag        = bool(params.get("injury_flag", False)),
            minutes_dependency = bool(params.get("minutes_dependency", True)),
            model_prob         = model_prob,
            per_leg_breakeven  = per_leg_be,
            market_type        = params.get("market_type", "player_prop"),
        )
        gate_results["house_rules"] = hr
        if not hr["passed"]:
            terminal_reject = PropLabel.REJECT_HOUSE_RULES_VULNERABILITY.value
            blocking_gates.append(f"HOUSE_RULES:{hr['code']}")
            required_actions.append(
                f"Injury/partial-play risk erases edge on {platform}. "
                "Do not play until player status and minutes are confirmed."
            )
        elif hr["code"] == "HOUSE_RULES_CAUTION":
            warnings.append("HOUSE_RULES_CAUTION")
            ceiling = _lower_ceiling(ceiling, PropLabel.MARKET_VERIFIED_HOLD.value)
    else:
        gate_results["house_rules"] = {"skipped": True, "reason": "prior terminal reject"}

    # ── Gate 3: Execution friction ────────────────────────────────────────────
    has_execution_data = any([
        params.get("line_timestamp_utc"),
        params.get("analysis_pp_line") is not None,
        params.get("current_payout") is not None,
        params.get("player_status"),
    ])
    if has_execution_data and not terminal_reject:
        ef = _ef.check_standalone(
            analysis_pp_line    = params.get("analysis_pp_line"),
            current_pp_line     = pp_line,
            analysis_payout     = params.get("analysis_payout"),
            current_payout      = params.get("current_payout"),
            analysis_slip_type  = params.get("analysis_slip_type"),
            current_slip_type   = params.get("current_slip_type"),
            analysis_pick_count = params.get("analysis_pick_count"),
            current_pick_count  = params.get("current_pick_count"),
            line_timestamp_utc  = params.get("line_timestamp_utc"),
            player_status       = params.get("player_status"),
            side                = side,
            platform            = platform,
            max_line_age_seconds= int(params.get("max_line_age_seconds", 30)),
        )
        gate_results["execution_friction"] = ef
        if ef.get("rejects"):
            tl = ef["rejects"][0]
            terminal_reject = terminal_reject or tl
            blocking_gates.append(f"EXECUTION:{tl}")
            required_actions.append("Refresh line data before submission — execution window expired.")
        elif ef.get("cautions"):
            for c in ef["cautions"]:
                warnings.append(c)
            ceiling = _lower_ceiling(ceiling, PropLabel.MONEY_QUALIFIED.value)
    else:
        gate_results["execution_friction"] = {
            "skipped": True,
            "reason": "no execution context provided — add line_timestamp_utc for final-lock",
        }
        warnings.append("EXECUTION_CONTEXT_MISSING")
        ceiling = _lower_ceiling(ceiling, PropLabel.MARKET_VERIFIED_HOLD.value)
        required_actions.append("Provide line_timestamp_utc and current_payout for full execution check.")

    # ── Gate 4: Correlation gate ──────────────────────────────────────────────
    slip_legs = params.get("slip_legs") or []
    slip_type = params.get("slip_type") or ""
    if isinstance(slip_legs, list) and len(slip_legs) >= 2:
        cg = _cg.classify_legs(slip_legs)
        gate_results["correlation_gate"] = cg
        is_power = "power" in slip_type.lower()
        if cg["block_power_play"] and is_power:
            terminal_reject = terminal_reject or PropLabel.REJECT_POWER_CORRELATED.value
            blocking_gates.append(f"CORRELATION_GATE:{cg['classification']}")
            required_actions.append(
                f"Remove overlapping legs — {cg['classification']} detected in Power slip."
            )
        elif cg.get("note_flex_math"):
            warnings.append(f"CORRELATION_NOTE_FLEX:{cg['classification']}")
    else:
        gate_results["correlation_gate"] = {
            "skipped": True,
            "reason": "fewer than 2 legs — single-leg prop or legs not provided",
        }

    # ── EV metrics ────────────────────────────────────────────────────────────
    usable_prob    = shrinkage or model_prob
    ev_before      = None
    ev_after       = None
    slip_ev        = None

    if usable_prob is not None and per_leg_be is not None:
        ev_before = round(usable_prob - per_leg_be, 4)

    # Compute post-haircut EV (house_rules haircut applied if relevant)
    hr_result = gate_results.get("house_rules") or {}
    adj_prob = hr_result.get("model_prob_adjusted") if isinstance(hr_result, dict) else None
    if adj_prob is not None and per_leg_be is not None:
        ev_after = round(adj_prob - per_leg_be, 4)
    elif ev_before is not None:
        ev_after = ev_before

    if usable_prob is not None and payout_mult is not None:
        slip_ev = round(usable_prob * payout_mult - 1.0, 4)

    # ── Final label determination ─────────────────────────────────────────────
    if terminal_reject:
        final_label     = terminal_reject
        approval_ceiling = "BLOCKED"
    else:
        # Apply ceiling: walk from FINAL_APPROVED down until ceiling allows
        final_label = ceiling
        approval_ceiling = ceiling

        # Further downgrade if EV is below research threshold
        if ev_before is not None and ev_before <= 0 and not terminal_reject:
            final_label = _lower_ceiling(final_label, PropLabel.RESEARCH_INTEREST.value)

    # ── Summary ────────────────────────────────────────────────────────────────
    gates_passed  = [g for g, r in gate_results.items()
                     if isinstance(r, dict) and r.get("passed") is True]
    gates_failed  = [g for g, r in gate_results.items()
                     if isinstance(r, dict) and r.get("passed") is False]
    gates_skipped = [g for g, r in gate_results.items()
                     if isinstance(r, dict) and r.get("skipped")]

    return {
        "can_approve_bets":      False,
        "final_label":           final_label,
        "approval_ceiling":      approval_ceiling,
        "terminal_reject":       terminal_reject,
        "settlement_stale":      settlement_stale,
        "gate_results":          gate_results,
        "gates_passed":          gates_passed,
        "gates_failed":          gates_failed,
        "gates_skipped":         gates_skipped,
        "blocking_gates":        blocking_gates,
        "warnings":              warnings,
        "required_next_actions": required_actions,
        "EV_before_friction":    ev_before,
        "EV_after_friction":     ev_after,
        "usable_probability":    round(usable_prob, 4) if usable_prob else None,
        "model_probability":     round(model_prob, 4) if model_prob else None,
        "per_leg_breakeven":     per_leg_be,
        "slip_EV":               slip_ev,
        "prop": {
            "player":   player,
            "market":   market,
            "side":     side,
            "pp_line":  pp_line,
            "platform": platform,
        },
    }
