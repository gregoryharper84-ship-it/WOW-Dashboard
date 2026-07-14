"""
series_state.py — WOW-PATCH-2026-07-10
MLB winner-market series-state audit (Rule F) + win-streak governance (Rule G).

Rule F — SERIES_STATE_CAUTION triggers:
  1. Opponent won the first two games of the series (opponent_win_streak_in_series >= 2)
  2. Opponent win streak >= 5 (opponent_win_streak >= 5)
  3. Selected team scored <= 1 run in the prior game (previous_game_runs_selected_team <= 1)

  Two or more triggers cap the row at LLP_WATCH unless:
    fresh no-vig consensus AND model edge still clear the edge floor
    after an additional uncertainty tax (UNCERTAINTY_TAX).

Rule G — recent_win_streak isolation:
  recent_win_streak is metadata only.
  It MUST NOT alter model_probability, edge_floor, combo_size, or stake.
  Any path that reads recent_win_streak for actionable purposes raises AssertionError.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SERIES_CAUTION_LABEL      = "SERIES_STATE_CAUTION"
LLP_WATCH_CEILING         = "LLP_WATCH"

TRIGGER_OPPONENT_SERIES   = "OPPONENT_SERIES_WINS_FIRST_TWO"
TRIGGER_OPPONENT_STREAK   = "OPPONENT_WIN_STREAK_GTE_5"
TRIGGER_SELECTED_RUNS     = "SELECTED_TEAM_LOW_RUNS_PRIOR_GAME"

CAUTION_THRESHOLD         = 2       # triggers needed for LLP_WATCH cap
OPPONENT_STREAK_LIMIT     = 5
RUNS_LOW_THRESHOLD        = 1       # <= this triggers caution
UNCERTAINTY_TAX           = 0.010   # additional edge requirement when cautioned

# Edge floor by market type (mirrors llp_governance defaults)
DEFAULT_EDGE_FLOOR = 0.015


# ---------------------------------------------------------------------------
# Rule G guard: recent_win_streak must never reach actionable paths
# ---------------------------------------------------------------------------

def assert_win_streak_isolation(context: dict[str, Any] | None, path_name: str) -> None:
    """
    Rule G assertion: raise AssertionError if recent_win_streak is present in
    a context that is used for tier, floor, combo-size, or stake decisions.

    Call this at every actionable decision point. Pass the dict being evaluated
    and a human-readable path_name for the error message.
    """
    if context and "recent_win_streak" in context:
        raise AssertionError(
            f"Rule G violation: recent_win_streak present in actionable context "
            f"'{path_name}'. recent_win_streak is metadata-only and must never "
            "alter model_probability, edge_floor, combo_size, or stake."
        )


# ---------------------------------------------------------------------------
# Series-state caution check
# ---------------------------------------------------------------------------

def run_series_state_audit(
    enrichment: dict[str, Any],
    edge_floor: float = DEFAULT_EDGE_FLOOR,
) -> dict[str, Any]:
    """
    MLB winner-market series-state audit.

    Accepted enrichment fields (all optional):
        series_score                        str | None   — e.g. "0-2" (selected team wins-losses)
        opponent_win_streak_in_series       int | None   — opponent wins in current series
        opponent_win_streak                 int | None   — opponent's rolling win streak
        previous_game_runs_selected_team    int | None   — runs scored by selected team in prior game
        home_recent_record                  str | None   — e.g. "3-7" last 10 at home
        selected_team_recent_record         str | None   — e.g. "4-6" last 10
        bullpen_workload                    float | None — innings pitched by bullpen last 3 games
        returning_starter_flag              bool | None
        no_vig_consensus_edge               float | None — live consensus edge (post-uncertainty-tax check)
        model_edge                          float | None — model edge value

    Returns:
        {
          passed                  bool
          code                    str
          detail                  str
          triggers                list[str]   — which cautions fired
          trigger_count           int
          ceiling                 str | None  — LLP_WATCH if cap applied, else None
          uncertainty_tax_applied bool
          edge_after_tax          float | None
        }
    """
    # Rule G: recent_win_streak must not be in the enrichment dict at this point
    assert_win_streak_isolation(enrichment, "run_series_state_audit")

    triggers: list[str] = []
    detail_parts: list[str] = []

    # Trigger 1: opponent won first 2 games of series.
    # Detected via explicit opponent_win_streak_in_series field OR by
    # parsing series_score (format: "SELECTED_WINS-OPPONENT_WINS", e.g. "0-2").
    opp_series_wins = enrichment.get("opponent_win_streak_in_series")

    # If not provided directly, derive from series_score string
    if opp_series_wins is None:
        series_score = enrichment.get("series_score")
        if series_score:
            try:
                # Expected format: "{selected_wins}-{opponent_wins}" e.g. "0-2"
                parts = str(series_score).strip().split("-")
                if len(parts) == 2:
                    opponent_wins_in_series = int(parts[1])
                    opp_series_wins = opponent_wins_in_series
            except (TypeError, ValueError, IndexError):
                pass

    if opp_series_wins is not None:
        try:
            if int(opp_series_wins) >= 2:
                triggers.append(TRIGGER_OPPONENT_SERIES)
                detail_parts.append(
                    f"opponent_win_streak_in_series={opp_series_wins} (>= 2)"
                )
        except (TypeError, ValueError):
            pass

    # Trigger 2: opponent win streak >= 5
    opp_streak = enrichment.get("opponent_win_streak")
    if opp_streak is not None:
        try:
            if int(opp_streak) >= OPPONENT_STREAK_LIMIT:
                triggers.append(TRIGGER_OPPONENT_STREAK)
                detail_parts.append(
                    f"opponent_win_streak={opp_streak} (>= {OPPONENT_STREAK_LIMIT})"
                )
        except (TypeError, ValueError):
            pass

    # Trigger 3: selected team scored <= 1 run prior game
    prior_runs = enrichment.get("previous_game_runs_selected_team")
    if prior_runs is not None:
        try:
            if int(prior_runs) <= RUNS_LOW_THRESHOLD:
                triggers.append(TRIGGER_SELECTED_RUNS)
                detail_parts.append(
                    f"previous_game_runs_selected_team={prior_runs} (<= {RUNS_LOW_THRESHOLD})"
                )
        except (TypeError, ValueError):
            pass

    trigger_count = len(triggers)

    if trigger_count < CAUTION_THRESHOLD:
        return {
            "passed":                  True,
            "code":                    "SERIES_STATE_OK",
            "detail":                  (
                f"Series-state audit: {trigger_count} trigger(s) — below caution threshold. "
                + ("; ".join(detail_parts) if detail_parts else "No triggers fired.")
            ),
            "triggers":                triggers,
            "trigger_count":           trigger_count,
            "ceiling":                 None,
            "uncertainty_tax_applied": False,
            "edge_after_tax":          None,
        }

    # >= 2 triggers: apply LLP_WATCH cap unless edge clears floor + tax
    uncertainty_tax_applied = False
    edge_after_tax: float | None = None
    cap_applied = True

    no_vig_edge  = enrichment.get("no_vig_consensus_edge")
    model_edge   = enrichment.get("model_edge")

    # Rule F edge-clear: BOTH no_vig_consensus_edge AND model_edge must
    # independently clear edge_floor after uncertainty tax. A single high signal
    # from one source cannot lift the caution cap — both must agree.
    _no_vig_float: float | None = None
    _model_float:  float | None = None

    try:
        if no_vig_edge is not None:
            _no_vig_float = float(no_vig_edge)
    except (TypeError, ValueError):
        pass

    try:
        if model_edge is not None:
            _model_float = float(model_edge)
    except (TypeError, ValueError):
        pass

    if _no_vig_float is not None and _model_float is not None:
        no_vig_taxed = _no_vig_float - UNCERTAINTY_TAX
        model_taxed  = _model_float  - UNCERTAINTY_TAX
        edge_after_tax = round(min(no_vig_taxed, model_taxed), 4)
        uncertainty_tax_applied = True
        if no_vig_taxed >= edge_floor and model_taxed >= edge_floor:
            cap_applied = False
    elif _no_vig_float is not None or _model_float is not None:
        # Only one signal present — insufficient; keep cap_applied = True
        available = _no_vig_float if _no_vig_float is not None else _model_float
        edge_after_tax = round(float(available) - UNCERTAINTY_TAX, 4)
        uncertainty_tax_applied = True

    ceiling = LLP_WATCH_CEILING if cap_applied else None
    code    = SERIES_CAUTION_LABEL if cap_applied else "SERIES_STATE_EDGE_CLEARED"

    return {
        "passed":                  not cap_applied,
        "code":                    code,
        "detail":                  (
            f"Series-state audit: {trigger_count} trigger(s) fired — "
            + "; ".join(detail_parts)
            + f". Cap: {'LLP_WATCH (edge did not clear floor+tax)' if cap_applied else 'NOT applied (edge cleared floor after uncertainty tax)'}."
        ),
        "triggers":                triggers,
        "trigger_count":           trigger_count,
        "ceiling":                 ceiling,
        "uncertainty_tax_applied": uncertainty_tax_applied,
        "edge_after_tax":          edge_after_tax,
    }


# ---------------------------------------------------------------------------
# Rule G: ensure recent_win_streak never changes actionable outputs
# Callable as an assertion from any gate that processes candidates
# ---------------------------------------------------------------------------

def validate_win_streak_is_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Rule G: assert recent_win_streak is present only as metadata, not in
    any field that drives model_probability, edge_floor, combo_size, or stake.

    Enforcement layers:
      1. Explicit declaration: win_streak_used_for list must be empty.
      2. Source-tag audit: note/source fields must not reference win streak.
      3. Actionable field audit: model_probability, edge_floor, combo_size,
         stake, stake_tier, recommended_stake must not have win-streak-derived
         values (checked via presence of "win_streak" in their source_tags).
      4. Cross-field contamination: if recent_win_streak equals model_probability
         or edge_floor exactly (a suspicious match), flag it.

    Returns {passed, code, detail}.
    """
    ws = candidate.get("recent_win_streak")
    violations: list[str] = []

    # Layer 1: explicit declaration
    win_streak_source = (candidate.get("win_streak_used_for") or [])
    if isinstance(win_streak_source, list) and win_streak_source:
        violations.extend([f"win_streak_used_for: {v}" for v in win_streak_source])
    elif isinstance(win_streak_source, str) and win_streak_source:
        violations.append(f"win_streak_used_for: {win_streak_source}")

    # Layer 2: source-tag audit on note fields
    for note_field in ("adjustment_source", "edge_source", "stake_source",
                       "tier_source", "combo_source", "floor_source"):
        note = str(candidate.get(note_field) or "").lower()
        if "win_streak" in note or "recent_streak" in note or "streak" in note:
            violations.append(f"{note_field} references win streak")

    # Layer 3: actionable field source tags
    for actionable_field in ("model_probability", "edge_floor", "combo_size",
                             "stake", "adjusted_stake", "stake_tier", "recommended_stake"):
        tag_key = f"{actionable_field}_source"
        tag_val = str(candidate.get(tag_key) or "").lower()
        if "win_streak" in tag_val or "recent_streak" in tag_val:
            violations.append(
                f"{actionable_field} is sourced from win streak "
                f"({tag_key}={candidate.get(tag_key)!r})"
            )

    # Layer 4: suspicious numeric coincidence (win streak == model prob exactly)
    if ws is not None:
        model_prob = candidate.get("model_probability")
        edge_floor_val = candidate.get("edge_floor")
        try:
            ws_float = float(ws)
            if model_prob is not None and float(model_prob) == ws_float:
                violations.append(
                    f"recent_win_streak={ws} matches model_probability={model_prob} "
                    "— possible contamination"
                )
            if edge_floor_val is not None and float(edge_floor_val) == ws_float:
                violations.append(
                    f"recent_win_streak={ws} matches edge_floor={edge_floor_val} "
                    "— possible contamination"
                )
        except (TypeError, ValueError):
            pass

    if violations:
        return {
            "passed": False,
            "code":   "RULE_G_WIN_STREAK_VIOLATION",
            "detail": (
                f"Rule G violation: recent_win_streak={ws} is influencing "
                f"actionable outputs — {'; '.join(violations)}. "
                "Win streak is metadata only — must not alter tier, floor, "
                "combo size, or stake."
            ),
        }

    return {
        "passed": True,
        "code":   "RULE_G_WIN_STREAK_ISOLATED",
        "detail": (
            f"recent_win_streak={ws!r} is present as metadata only. "
            "No actionable fields influenced (all 4 Rule G layers passed)."
        ),
    }
