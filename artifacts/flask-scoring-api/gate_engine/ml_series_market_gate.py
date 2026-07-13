"""
gate_engine/ml_series_market_gate.py
WOW-PATCH-2026-07-13 — P1-6 + P1-7

P1-6: Series-State and Current-Execution Penalty
P1-7: Market Disagreement Gate (3-way comparison: model / market / platform)

P1-6 rule:
    if team_trails_series_by_2_or_more:
        require_series_state_review = True
    if same_series_run_diff <= -8:
        confidence_ceiling = min(ceiling, LLP_PLAYABLE)

    Does not auto-reject — prevents season priors from overpowering current info.

P1-7 comparison:
    edge_vs_prizepicks = model_prob - breakeven_prob
    edge_vs_market     = model_prob - no_vig_prob
    platform_price_delta = no_vig_prob - breakeven_prob

    Quadrant:
      model AND market > breakeven → MARKET_CORROBORATED_EDGE
      model > breakeven, market NOT → MODEL_ONLY_DISAGREEMENT  (cap WATCH in Reliability Freeze)
      market > breakeven, model NOT → MARKET_ONLY_EDGE
      neither > breakeven          → NO_VERIFIED_EDGE
"""
from __future__ import annotations

from typing import Any

from .ml_labels import MLReasonCode, MarketDisagreementLabel


# ---------------------------------------------------------------------------
# P1-6: Series State Gate
# ---------------------------------------------------------------------------

SERIES_DEFICIT_REVIEW_THRESHOLD  = 2    # trail by 2+ → require review
SERIES_RUN_DIFF_CEILING_THRESHOLD = -8  # run diff <= -8 → cap LLP_PLAYABLE
SERIES_LABEL_CEILING              = "LLP_PLAYABLE"


def validate_series_state(candidate: dict[str, Any]) -> dict[str, Any]:
    """
    Apply series-state adjustment to a full-series ML candidate.

    Required input fields:
        series_score_before_game  : str | None  — e.g. "1-2" (selected team's perspective)
        series_games_played       : int | None
        series_run_differential   : int | None  — cumulative run diff for selected team in series
        team_errors_last_5        : int | None
        bullpen_usage_in_series   : float | None — innings pitched by bullpen in series
        lineup_absences           : int | None
        recent_run_differential   : int | None  — last 5 games run diff

    Returns:
        {
          passed                  : bool
          code                    : str
          detail                  : str
          reason_code             : str | None
          ceiling                 : str | None
          series_state_review     : bool
          series_run_diff         : int | None
          team_trails_series_by   : int | None  (games behind in series)
          adjustments_applied     : list[str]
        }
    """
    adjustments: list[str] = []
    ceiling: str | None = None
    series_review = False

    series_score_raw = candidate.get("series_score_before_game")
    run_diff         = _to_int(candidate.get("series_run_differential"))
    games_trailed    = _parse_series_deficit(series_score_raw)
    recent_run_diff  = _to_int(candidate.get("recent_run_differential"))
    errors_last_5    = _to_int(candidate.get("team_errors_last_5"))
    lineup_absences  = _to_int(candidate.get("lineup_absences"))

    # Rule 1: trailing by ≥2 games in series → require review
    if games_trailed is not None and games_trailed >= SERIES_DEFICIT_REVIEW_THRESHOLD:
        series_review = True
        adjustments.append(
            f"SERIES_STATE_REVIEW_REQUIRED: team trails series by {games_trailed} game(s)"
        )

    # Rule 2: run differential ≤ -8 in series → cap at LLP_PLAYABLE
    if run_diff is not None and run_diff <= SERIES_RUN_DIFF_CEILING_THRESHOLD:
        ceiling = SERIES_LABEL_CEILING
        adjustments.append(
            f"SERIES_RUN_DIFF_CEILING: series run differential {run_diff} <= "
            f"{SERIES_RUN_DIFF_CEILING_THRESHOLD} — season priors capped at LLP_PLAYABLE"
        )

    # Informational: errors and lineup absences (documented but not hard-ceiling)
    if errors_last_5 is not None and errors_last_5 >= 3:
        adjustments.append(
            f"EXECUTION_WARNING: {errors_last_5} errors in last 5 games"
        )
    if lineup_absences is not None and lineup_absences >= 2:
        adjustments.append(
            f"LINEUP_WARNING: {lineup_absences} absences in current lineup"
        )

    if adjustments:
        detail = "; ".join(adjustments)
        passed = ceiling is None  # only hard ceiling = fail, review = warn but pass
        code = "SERIES_STATE_CEILING_APPLIED" if ceiling else "SERIES_STATE_REVIEW_REQUIRED"
    else:
        detail = "No adverse series-state signals detected"
        passed = True
        code   = "SERIES_STATE_CLEAR"

    return {
        "passed":               passed,
        "code":                 code,
        "detail":               detail,
        "reason_code":          None,
        "ceiling":              ceiling,
        "series_state_review":  series_review,
        "series_run_diff":      run_diff,
        "team_trails_series_by": games_trailed,
        "adjustments_applied":  adjustments,
    }


# ---------------------------------------------------------------------------
# P1-7: Market Disagreement Gate
# ---------------------------------------------------------------------------

def validate_market_disagreement(
    model_prob:      float | None,
    no_vig_prob:     float | None,
    breakeven_prob:  float | None,
    reliability_freeze: bool = False,
) -> dict[str, Any]:
    """
    Compare model probability, market no-vig probability, and PrizePicks
    breakeven probability. Classify into one of the four quadrants.

    Returns:
        {
          passed                   : bool
          code                     : str
          detail                   : str
          reason_code              : str | None
          ceiling                  : str | None
          quadrant                 : str   (MarketDisagreementLabel)
          edge_vs_prizepicks       : float | None
          edge_vs_market           : float | None
          platform_price_delta     : float | None
          model_exceeds_breakeven  : bool | None
          market_exceeds_breakeven : bool | None
        }
    """
    from gate_engine.llp_governance import LLPLabel

    if any(v is None for v in (model_prob, no_vig_prob, breakeven_prob)):
        missing = [
            name for name, val in (
                ("model_prob", model_prob),
                ("no_vig_prob", no_vig_prob),
                ("breakeven_prob", breakeven_prob),
            ) if val is None
        ]
        return {
            "passed":                  False,
            "code":                    "MARKET_DISAGREEMENT_MISSING_INPUTS",
            "detail":                  f"Cannot classify market disagreement — missing: {missing}",
            "reason_code":             MLReasonCode.MARKET_DISAGREEMENT.value,
            "ceiling":                 LLPLabel.WATCH.value,
            "quadrant":                MarketDisagreementLabel.NO_VERIFIED_EDGE.value,
            "edge_vs_prizepicks":      None,
            "edge_vs_market":          None,
            "platform_price_delta":    None,
            "model_exceeds_breakeven": None,
            "market_exceeds_breakeven": None,
        }

    edge_vs_pp     = round(model_prob  - breakeven_prob, 6)
    edge_vs_market = round(model_prob  - no_vig_prob,    6)
    pp_delta       = round(no_vig_prob - breakeven_prob, 6)

    model_pos  = edge_vs_pp > 0
    market_pos = pp_delta   > 0  # market exceeds breakeven

    # Classify quadrant
    if model_pos and market_pos:
        quadrant    = MarketDisagreementLabel.MARKET_CORROBORATED_EDGE
        passed      = True
        ceiling     = None
        reason_code = None
        detail      = (
            f"Both model and market exceed PrizePicks breakeven. "
            f"edge_vs_pp={edge_vs_pp:.4f}, edge_vs_market={edge_vs_market:.4f}, "
            f"pp_delta={pp_delta:.4f}. Strongest approval case."
        )
        code = "MARKET_CORROBORATED"

    elif model_pos and not market_pos:
        quadrant    = MarketDisagreementLabel.MODEL_ONLY_DISAGREEMENT
        # In Reliability Freeze → cap WATCH; otherwise just a warning
        ceiling     = LLPLabel.WATCH.value if reliability_freeze else None
        passed      = not reliability_freeze
        reason_code = MLReasonCode.MARKET_DISAGREEMENT.value if reliability_freeze else None
        detail      = (
            f"Model exceeds breakeven but market does not. "
            f"edge_vs_pp={edge_vs_pp:.4f}, pp_delta={pp_delta:.4f} (negative). "
            f"{'Capped at LLP_WATCH during Reliability Freeze.' if reliability_freeze else 'Investigate before approving.'}"
        )
        code = "MODEL_ONLY_DISAGREEMENT"

    elif not model_pos and market_pos:
        quadrant    = MarketDisagreementLabel.MARKET_ONLY_EDGE
        passed      = False
        ceiling     = LLPLabel.WATCH.value
        reason_code = MLReasonCode.NO_VERIFIED_EDGE.value
        detail      = (
            f"Market exceeds breakeven but model does not — no LLP approval. "
            f"edge_vs_pp={edge_vs_pp:.4f} (negative), pp_delta={pp_delta:.4f}."
        )
        code = "MARKET_ONLY_EDGE"

    else:
        quadrant    = MarketDisagreementLabel.NO_VERIFIED_EDGE
        passed      = False
        ceiling     = LLPLabel.REJECT.value
        reason_code = MLReasonCode.NO_VERIFIED_EDGE.value
        detail      = (
            f"Neither model nor market exceeds PrizePicks breakeven. "
            f"edge_vs_pp={edge_vs_pp:.4f}, pp_delta={pp_delta:.4f}. "
            f"Hard reject: LLP_REJECT_NO_VERIFIED_EDGE."
        )
        code = "NO_VERIFIED_EDGE"

    return {
        "passed":                   passed,
        "code":                     code,
        "detail":                   detail,
        "reason_code":              reason_code,
        "ceiling":                  ceiling,
        "quadrant":                 quadrant.value,
        "edge_vs_prizepicks":       edge_vs_pp,
        "edge_vs_market":           edge_vs_market,
        "platform_price_delta":     pp_delta,
        "model_exceeds_breakeven":  model_pos,
        "market_exceeds_breakeven": market_pos,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_series_deficit(series_score: str | None) -> int | None:
    """
    Parse a series score string from the selected team's perspective.
    e.g. "1-2" → team is down 1 game (2-1 against them → -1).
    Returns games behind (positive = trailing), or None.
    """
    if not series_score:
        return None
    parts = str(series_score).strip().split("-")
    if len(parts) != 2:
        return None
    try:
        wins   = int(parts[0])
        losses = int(parts[1])
        deficit = losses - wins
        return max(deficit, 0)  # 0 = tied or ahead
    except ValueError:
        return None
