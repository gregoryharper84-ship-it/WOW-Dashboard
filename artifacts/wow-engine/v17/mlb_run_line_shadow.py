"""Research-only MLB run-line primitives and serving preflight.

WOW's MLB specialist already owns a governed full-game run distribution.  An
exact final-game run-line probability additionally requires a governed model of
final extra-inning score margin for simulations tied after nine.  The current
distribution state stores extra-inning winner probability but not that margin
distribution, so this module fails closed rather than assuming every extra-
inning win is by one run or converting moneyline probability to run line.
"""
from __future__ import annotations

from typing import Any, Iterable

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True


class MLBRunLineShadowUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def score_final_run_line_samples(home_runs: Iterable[int], away_runs: Iterable[int], *, home_run_line: float) -> dict[str, float | int]:
    """Score an exact signed home run line from already-final score samples."""
    home = [int(value) for value in home_runs]
    away = [int(value) for value in away_runs]
    if not home or len(home) != len(away):
        raise MLBRunLineShadowUnavailable("MLB_RUN_LINE_SAMPLES_INVALID", "home/away final score samples must be non-empty and aligned")
    line = float(home_run_line)
    if not (-10.0 < line < 10.0):
        raise MLBRunLineShadowUnavailable("MLB_RUN_LINE_INVALID", "home run line is outside supported sanity bounds")
    cover = push = not_cover = 0
    for h, a in zip(home, away):
        value = (h - a) + line
        if abs(value) <= 1e-12:
            push += 1
        elif value > 0:
            cover += 1
        else:
            not_cover += 1
    total = len(home)
    return {
        "p_cover": cover / total,
        "p_push": push / total,
        "p_not_cover": not_cover / total,
        "distribution_sample_n": total,
    }


def run_mlb_run_line_shadow_preflight(db: Any, *, score_snapshot_id: str, home_run_line: float) -> dict[str, Any]:
    """Prove the existing run distribution and fail closed on the missing margin component."""
    score_rows = (
        db.table("wow_mlb_forward_score_snapshots")
        .select("score_snapshot_id,shadow_event_id,distribution_id,home_mu,away_mu,score_status,probability_publishable,can_execute")
        .eq("score_snapshot_id", str(score_snapshot_id))
        .limit(1)
        .execute().data or []
    )
    if len(score_rows) != 1:
        raise MLBRunLineShadowUnavailable("MLB_RUN_LINE_SCORE_SNAPSHOT_UNAVAILABLE", "governed MLB forward score snapshot is unavailable")
    score = dict(score_rows[0])
    dist_rows = (
        db.table("wow_mlb_v2b_distribution_state")
        .select("distribution_id,model_version,home_alpha_total,away_alpha_total,extra_inning_home_win_probability,extra_inning_training_games,training_end,research_only,probability_publishable,can_execute")
        .eq("distribution_id", score.get("distribution_id"))
        .limit(1)
        .execute().data or []
    )
    if len(dist_rows) != 1:
        raise MLBRunLineShadowUnavailable("MLB_RUN_LINE_DISTRIBUTION_UNAVAILABLE", "governed MLB run distribution state is unavailable")
    dist = dict(dist_rows[0])
    if score.get("can_execute") is not False or dist.get("can_execute") is not False:
        raise MLBRunLineShadowUnavailable("MLB_RUN_LINE_EXECUTION_INVARIANT_VIOLATION", "MLB run-line evidence must preserve can_execute=false")
    if int(dist.get("extra_inning_training_games") or 0) <= 0:
        raise MLBRunLineShadowUnavailable("MLB_RUN_LINE_EXTRA_INNING_WIN_EVIDENCE_UNAVAILABLE", "extra-inning winner evidence is unavailable")
    line = float(home_run_line)
    if not (-10.0 < line < 10.0):
        raise MLBRunLineShadowUnavailable("MLB_RUN_LINE_INVALID", "home run line is outside supported sanity bounds")
    return {
        "status": "BLOCKED",
        "code": "MLB_RUN_LINE_EXTRA_INNING_MARGIN_MODEL_UNAVAILABLE",
        "sport": "MLB",
        "score_snapshot_id": str(score_snapshot_id),
        "distribution_id": dist.get("distribution_id"),
        "distribution_model_version": dist.get("model_version"),
        "home_mu": score.get("home_mu"),
        "away_mu": score.get("away_mu"),
        "home_alpha_total": dist.get("home_alpha_total"),
        "away_alpha_total": dist.get("away_alpha_total"),
        "extra_inning_home_win_probability": dist.get("extra_inning_home_win_probability"),
        "extra_inning_training_games": dist.get("extra_inning_training_games"),
        "home_run_line": line,
        "detail": "exact final run-line scoring requires a governed conditional final-margin distribution for simulations tied after nine",
        "market_features_used": False,
        "run_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "can_execute": CAN_EXECUTE,
    }


__all__ = ["MLBRunLineShadowUnavailable", "run_mlb_run_line_shadow_preflight", "score_final_run_line_samples"]
