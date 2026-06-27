"""
house_rules.py  —  Patch: House Rules Matrix
WOW v16 / Patch 2026-06-27

Platform-specific settlement rules must be verified before any prop approval.
A player who starts and exits early can be graded very differently depending
on the platform — this gate enforces that difference.

Required matrix fields (when platform is known):
  platform            — PrizePicks, FanDuel, DraftKings, BetUS, sportsbook
  market_type         — player_prop, team_total, game_prop, combo_stat
  dnp_rule            — void, loss, push, action, pro-rata
  partial_play_rule   — action, void, pro-rata
  stat_correction_rule — platform policy on post-game stat corrections
  combo_stat_rule     — how combo stats (pts+reb+ast) are graded
  injury_return_rule  — policy on injury-return players in first game back

HOUSE_RULES_VULNERABILITY fires when:
  1. Player has elevated injury/return risk (injury_flag = True)
  AND
  2. Platform grades partial play as action (not void)
  AND
  3. model_prob depends on normal minutes/role

Haircut applied when injury risk is elevated: INJURY_HAIRCUT_FRACTION
If haircut erases edge below MINIMUM_SURVIVING_EDGE → REJECT_HOUSE_RULES_VULNERABILITY

Labels:
  HOUSE_RULES_OK                    — all rules verified, no elevated risk
  HOUSE_RULES_CAUTION               — elevated risk but edge survives haircut
  REJECT_HOUSE_RULES_VULNERABILITY  — partial play risk + elevated injury erases edge
"""
from __future__ import annotations

from typing import Any

from .labels import PropLabel

# ---------------------------------------------------------------------------
# Default PrizePicks house rules (source of truth for most WOW use cases)
# ---------------------------------------------------------------------------

PRIZEPICKS_RULES = {
    "dnp_rule":            "void",          # DNP = void / no action on PrizePicks
    "partial_play_rule":   "action",        # Partial games are typically action
    "stat_correction_rule": "24h_window",   # Corrections honored within 24h
    "combo_stat_rule":     "sum_at_close",  # Combo stats computed at game close
    "injury_return_rule":  "action",        # First game back = action (no grade protection)
    "fantasy_score_rule":  "platform_calc", # Fantasy score = PP's own formula
    "void_push_rule":      "void_only",     # Push not possible; under-min-minutes = void
}

SPORTSBOOK_RULES = {
    "dnp_rule":            "void",
    "partial_play_rule":   "varies_by_book",
    "stat_correction_rule": "official_box",
    "combo_stat_rule":     "official_box",
    "injury_return_rule":  "action",
    "void_push_rule":      "push_or_void",
}

PLATFORM_RULES: dict[str, dict] = {
    "prizepicks": PRIZEPICKS_RULES,
    "fanduel":    {**SPORTSBOOK_RULES, "partial_play_rule": "action"},
    "draftkings": {**SPORTSBOOK_RULES, "partial_play_rule": "action"},
    "betus":      SPORTSBOOK_RULES,
    "sportsbook": SPORTSBOOK_RULES,
}

# Haircut applied to model_probability when injury/partial-play risk is elevated
INJURY_HAIRCUT_FRACTION = 0.08    # −8% applied to model_prob

# Minimum edge remaining after haircut for approval to survive
MINIMUM_SURVIVING_EDGE  = 0.02    # must clear per-leg breakeven by at least 2%


def run(
    row:       dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Validate house rules for this prop.

    Reads from enrichment["house_rules"] or row["house_rules"]:
        {
          platform:            str
          market_type:         str
          injury_flag:         bool   — True if elevated injury/return risk
          minutes_dependency:  bool   — True if model_prob depends on full minutes
          model_prob:          float  — current model probability
          per_leg_breakeven:   float  — breakeven from payout_context
        }

    Writes result to row["gates"]["house_rules"].
    """
    if row.get("terminal_label") is not None:
        return row

    enr = enrichment or {}
    hr_data = enr.get("house_rules") or row.get("house_rules") or {}

    platform_raw  = (hr_data.get("platform") or "prizepicks").lower().strip()
    market_type   = (hr_data.get("market_type") or "player_prop").lower().strip()
    injury_flag   = bool(hr_data.get("injury_flag", False))
    minutes_dep   = bool(hr_data.get("minutes_dependency", True))  # conservative default
    model_prob    = hr_data.get("model_prob") or row.get("model_probability")
    per_leg_be    = hr_data.get("per_leg_breakeven")

    platform_rules = PLATFORM_RULES.get(platform_raw, PRIZEPICKS_RULES)
    partial_play   = platform_rules.get("partial_play_rule", "action")
    dnp_rule       = platform_rules.get("dnp_rule", "void")
    injury_return  = platform_rules.get("injury_return_rule", "action")

    result = _evaluate(
        platform=platform_raw,
        market_type=market_type,
        injury_flag=injury_flag,
        minutes_dependency=minutes_dep,
        partial_play_rule=partial_play,
        dnp_rule=dnp_rule,
        injury_return_rule=injury_return,
        model_prob=model_prob,
        per_leg_breakeven=per_leg_be,
        platform_rules=platform_rules,
    )

    row.setdefault("gates", {})["house_rules"] = result

    if result["code"] == "REJECT_HOUSE_RULES_VULNERABILITY":
        if not row.get("terminal_label"):
            row["terminal_label"] = PropLabel.REJECT_HOUSE_RULES_VULNERABILITY.value
            row.setdefault("blockers", []).append(
                f"HOUSE_RULES:REJECT_HOUSE_RULES_VULNERABILITY:"
                f"platform={platform_raw}:partial_play={partial_play}"
            )
    elif result["code"] == "HOUSE_RULES_CAUTION":
        row.setdefault("blockers", []).append(
            f"HOUSE_RULES:CAUTION:injury_haircut_applied:platform={platform_raw}"
        )

    return row


def check_standalone(
    platform:          str,
    injury_flag:       bool,
    minutes_dependency: bool,
    model_prob:        float | None,
    per_leg_breakeven: float | None,
    market_type:       str = "player_prop",
) -> dict[str, Any]:
    """Standalone check (no row mutation) — used by the Final Lock endpoint and tests."""
    platform_lc    = platform.lower().strip()
    platform_rules = PLATFORM_RULES.get(platform_lc, PRIZEPICKS_RULES)
    return _evaluate(
        platform=platform_lc,
        market_type=market_type,
        injury_flag=injury_flag,
        minutes_dependency=minutes_dependency,
        partial_play_rule=platform_rules.get("partial_play_rule", "action"),
        dnp_rule=platform_rules.get("dnp_rule", "void"),
        injury_return_rule=platform_rules.get("injury_return_rule", "action"),
        model_prob=model_prob,
        per_leg_breakeven=per_leg_breakeven,
        platform_rules=platform_rules,
    )


# ---------------------------------------------------------------------------
# Internal logic
# ---------------------------------------------------------------------------

def _evaluate(
    platform:           str,
    market_type:        str,
    injury_flag:        bool,
    minutes_dependency: bool,
    partial_play_rule:  str,
    dnp_rule:           str,
    injury_return_rule: str,
    model_prob:         float | None,
    per_leg_breakeven:  float | None,
    platform_rules:     dict,
) -> dict[str, Any]:

    vulnerability = False
    caution       = False
    haircut_applied = 0.0
    adj_model_prob  = model_prob
    surviving_edge  = None
    details: list[str] = []

    # Check the core vulnerability trigger:
    # injury risk + partial play grades as action + minutes-dependent model
    if injury_flag and partial_play_rule == "action" and minutes_dependency:
        # Apply injury haircut
        if model_prob is not None:
            haircut_applied = INJURY_HAIRCUT_FRACTION
            adj_model_prob  = round(model_prob - haircut_applied, 4)
            details.append(
                f"Injury/return risk elevated + {platform} grades partial play as action "
                f"+ model_prob depends on full minutes. "
                f"Haircut applied: {model_prob:.3f} → {adj_model_prob:.3f} "
                f"(−{haircut_applied:.1%})."
            )

            if per_leg_breakeven is not None:
                surviving_edge = round(adj_model_prob - per_leg_breakeven, 4)
                if surviving_edge < MINIMUM_SURVIVING_EDGE:
                    vulnerability = True
                    details.append(
                        f"Post-haircut edge {surviving_edge:.4f} < minimum "
                        f"{MINIMUM_SURVIVING_EDGE:.2f} → "
                        f"REJECT_HOUSE_RULES_VULNERABILITY."
                    )
                else:
                    caution = True
                    details.append(
                        f"Post-haircut edge {surviving_edge:.4f} ≥ minimum "
                        f"{MINIMUM_SURVIVING_EDGE:.2f} — caution, edge survives."
                    )
            else:
                # No breakeven to compare — caution only
                caution = True
                details.append(
                    "No per_leg_breakeven available — cannot verify surviving edge. Caution."
                )
        else:
            # No model_prob — cannot compute haircut; caution
            caution = True
            details.append(
                "Injury/return risk elevated but model_prob not provided — caution."
            )

    # Additional check: DNP risk on a platform where DNP = action
    if dnp_rule == "action" and injury_flag:
        details.append(
            f"WARNING: {platform} grades DNP as action — full loss risk if player sits."
        )
        caution = True

    if not injury_flag:
        details.append(
            f"No elevated injury/return risk. {platform} rules: "
            f"partial_play={partial_play_rule}, dnp={dnp_rule}."
        )

    if vulnerability:
        code = "REJECT_HOUSE_RULES_VULNERABILITY"
    elif caution:
        code = "HOUSE_RULES_CAUTION"
    else:
        code = "HOUSE_RULES_OK"

    return {
        "passed":            not vulnerability,
        "code":              code,
        "platform":          platform,
        "market_type":       market_type,
        "injury_flag":       injury_flag,
        "minutes_dependency": minutes_dependency,
        "partial_play_rule": partial_play_rule,
        "dnp_rule":          dnp_rule,
        "injury_return_rule": injury_return_rule,
        "haircut_applied":   haircut_applied,
        "model_prob_original": model_prob,
        "model_prob_adjusted": adj_model_prob,
        "surviving_edge":    surviving_edge,
        "per_leg_breakeven": per_leg_breakeven,
        "platform_rules":    platform_rules,
        "detail":            " ".join(details) or f"Platform={platform}, rules verified, no elevated risk.",
        "can_approve_bets":  False,
    }
