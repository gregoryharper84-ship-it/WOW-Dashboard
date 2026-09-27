"""Research-only MLB exact run-line shadow scoring.

This lane reuses the existing governed MLB nine-inning run-distribution inputs and
failure-regime mixture. The requested run line is applied only after final-score
samples exist. Games tied after nine are resolved with the separately reviewed
2024 extra-inning final-margin challenger; moneyline probability is never converted
into run-line probability.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Callable, Iterable

import numpy as np

from mlb_event_specialist_v16 import (
    MIN_SIMULATIONS,
    LineupAdjustment,
    ProspectiveModelUnavailable,
    WeatherContext,
    _bullpen_failure_probability,
    _defense_failure_probability,
    _feature_map,
    _fetch_player_stats,
    _lineup_adjustment,
    _load_evidence,
    _nb_draw,
    _parse_feed,
    _seed_for_event,
    _starter_failure_probability,
    _starter_hand,
    _weather_context,
)
from v17.mlb_extra_inning_margin_challenger import (
    ExtraInningMarginArtifact,
    resolve_tied_nine_inning_samples,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
RUN_LINE_MODEL_FAMILY = "MLB_V16_V2D_RUN_LINE_SHADOW_V1"
EXTRA_MARGIN_MODEL_FAMILY = "MLB_EXTRA_INNING_FINAL_MARGIN_EMPIRICAL_V1"
ELIGIBLE_SCORE_STATUSES = frozenset({"PASS", "SHADOW_SCORED_PREGAME"})

_FROZEN_2024_HOME_HIST = ((1, 98), (2, 6), (4, 1))
_FROZEN_2024_AWAY_HIST = ((1, 56), (2, 23), (3, 7), (4, 7), (5, 5), (6, 3), (7, 1))
_FROZEN_2024_RECEIPT = {
    "cohort": "GOVERNED_MLB_EXTRA_INNING_2024",
    "cohort_window_start": "2024-01-01",
    "cohort_window_end": "2024-12-31",
    "train_rows": 207,
    "home_histogram": _FROZEN_2024_HOME_HIST,
    "away_histogram": _FROZEN_2024_AWAY_HIST,
    "source_tables": ["wow_mlb_retrosplits_rows", "wow_mlb_v2a_game_features_2024"],
    "evidence_review_pr": 930,
}
_FROZEN_2024_RECEIPT_HASH = sha256(
    json.dumps(_FROZEN_2024_RECEIPT, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


class MLBRunLineShadowUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def frozen_2024_extra_inning_margin_artifact() -> ExtraInningMarginArtifact:
    return ExtraInningMarginArtifact(
        train_start="2024-01-01",
        train_end="2024-12-31",
        train_rows=207,
        home_histogram=_FROZEN_2024_HOME_HIST,
        away_histogram=_FROZEN_2024_AWAY_HIST,
        training_dataset_hash=_FROZEN_2024_RECEIPT_HASH,
    )


def score_final_run_line_samples(
    home_runs: Iterable[int], away_runs: Iterable[int], *, home_run_line: float
) -> dict[str, float | int]:
    home = [int(value) for value in home_runs]
    away = [int(value) for value in away_runs]
    if not home or len(home) != len(away):
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_SAMPLES_INVALID",
            "home/away final score samples must be non-empty and aligned",
        )
    line = float(home_run_line)
    if not (-10.0 < line < 10.0):
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_INVALID", "home run line is outside supported sanity bounds"
        )
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


def _simulate_nine_inning_samples(
    *,
    home_mu: float,
    away_mu: float,
    home_alpha: float,
    away_alpha: float,
    lineup_home: LineupAdjustment,
    lineup_away: LineupAdjustment,
    weather: WeatherContext,
    home_features: dict[str, float],
    away_features: dict[str, float],
    seed: int,
    simulation_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Mirror MLB V16/V2D failure-mixture random draws through nine innings."""
    if int(simulation_count) < MIN_SIMULATIONS:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_SIMULATION_COUNT_INSUFFICIENT",
            f"need at least {MIN_SIMULATIONS} simulations",
        )
    n = int(simulation_count)
    rng = np.random.default_rng(int(seed))
    home_sp_p = _starter_failure_probability(away_features)
    away_sp_p = _starter_failure_probability(home_features)
    home_bp_p = _bullpen_failure_probability(away_features)
    away_bp_p = _bullpen_failure_probability(home_features)
    home_def_p = _defense_failure_probability(away_features)
    away_def_p = _defense_failure_probability(home_features)
    home_sp_fail = rng.random(n) < home_sp_p
    away_sp_fail = rng.random(n) < away_sp_p
    home_bp_fail = rng.random(n) < home_bp_p
    away_bp_fail = rng.random(n) < away_bp_p
    home_def_fail = rng.random(n) < home_def_p
    away_def_fail = rng.random(n) < away_def_p
    weather_disrupt = rng.random(n) < weather.disruption_probability
    shared_sigma = 0.045 + 0.10 * weather.disruption_probability
    shared_env = rng.lognormal(mean=-0.5 * shared_sigma**2, sigma=shared_sigma, size=n)
    shared_env *= np.where(
        weather_disrupt, rng.choice(np.asarray([0.84, 1.18]), size=n), 1.0
    )
    home_mu_vec = np.full(n, float(home_mu) * lineup_home.ratio * weather.factor) * shared_env
    away_mu_vec = np.full(n, float(away_mu) * lineup_away.ratio * weather.factor) * shared_env
    home_mu_vec *= (
        np.where(away_sp_fail, 1.34, 1.0)
        * np.where(away_bp_fail, 1.17, 1.0)
        * np.where(away_def_fail, 1.08, 1.0)
    )
    away_mu_vec *= (
        np.where(home_sp_fail, 1.34, 1.0)
        * np.where(home_bp_fail, 1.17, 1.0)
        * np.where(home_def_fail, 1.08, 1.0)
    )
    return (
        _nb_draw(rng, home_mu_vec, float(home_alpha)),
        _nb_draw(rng, away_mu_vec, float(away_alpha)),
    )


def _score_snapshot_identity(db: Any, score_snapshot_id: str) -> dict[str, Any]:
    rows = (
        db.table("wow_mlb_forward_score_snapshots")
        .select(
            "score_snapshot_id,shadow_event_id,distribution_id,home_mu,away_mu,"
            "score_status,probability_publishable,can_execute"
        )
        .eq("score_snapshot_id", str(score_snapshot_id))
        .limit(1)
        .execute().data
        or []
    )
    if len(rows) != 1:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_SCORE_SNAPSHOT_UNAVAILABLE",
            "governed MLB forward score snapshot is unavailable",
        )
    score = dict(rows[0])
    if score.get("can_execute") is not False:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_EXECUTION_INVARIANT_VIOLATION",
            "MLB run-line score snapshot must preserve can_execute=false",
        )
    score_status = str(score.get("score_status") or "")
    if score_status not in ELIGIBLE_SCORE_STATUSES:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_SCORE_SNAPSHOT_NOT_PREGAME_SCORED",
            "MLB run-line requires a governed pregame-scored snapshot",
        )
    if not score.get("shadow_event_id"):
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_EVENT_IDENTITY_UNAVAILABLE",
            "score snapshot is missing its shadow event identity",
        )
    return score


def run_mlb_run_line_forward_shadow(
    db: Any,
    *,
    score_snapshot_id: str,
    home_run_line: float,
    simulation_count: int = MIN_SIMULATIONS,
    stats_fetcher: Callable[[list[int], int], dict[int, dict[str, Any]]] = _fetch_player_stats,
) -> dict[str, Any]:
    line = float(home_run_line)
    if not (-10.0 < line < 10.0):
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_INVALID", "home run line is outside supported sanity bounds"
        )
    score_identity = _score_snapshot_identity(db, str(score_snapshot_id))
    bridge = {
        "score_snapshot_id": str(score_snapshot_id),
        "shadow_event_id": str(score_identity["shadow_event_id"]),
    }
    try:
        evidence = _load_evidence(db, bridge)
    except ProspectiveModelUnavailable as exc:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_GOVERNED_EVIDENCE_UNAVAILABLE", str(exc)
        ) from exc
    score = evidence["score"]
    event = evidence["event"]
    lineup = evidence["lineup"]
    dist = evidence["distribution"]
    if dist.get("can_execute") is not False:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_EXECUTION_INVARIANT_VIOLATION",
            "MLB run distribution must preserve can_execute=false",
        )
    if int(dist.get("extra_inning_training_games") or 0) <= 0:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_EXTRA_INNING_WIN_EVIDENCE_UNAVAILABLE",
            "extra-inning winner evidence is unavailable",
        )
    feed = _parse_feed(lineup["raw_body"])
    home_order = [int(x) for x in lineup.get("home_batting_order") or []]
    away_order = [int(x) for x in lineup.get("away_batting_order") or []]
    season = int(str(event["official_date"])[:4])
    player_stats = stats_fetcher(home_order + away_order, season)
    home_starter_hand = _starter_hand(feed, event.get("home_probable_pitcher_id"))
    away_starter_hand = _starter_hand(feed, event.get("away_probable_pitcher_id"))
    home_lineup = _lineup_adjustment(home_order, away_starter_hand, player_stats)
    away_lineup = _lineup_adjustment(away_order, home_starter_hand, player_stats)
    weather = _weather_context(feed)
    home_features = _feature_map(evidence["features"]["HOME"])
    away_features = _feature_map(evidence["features"]["AWAY"])
    seed = _seed_for_event(str(score_identity["shadow_event_id"]), str(score_snapshot_id))
    home_runs9, away_runs9 = _simulate_nine_inning_samples(
        home_mu=float(score["home_mu"]),
        away_mu=float(score["away_mu"]),
        home_alpha=float(dist["home_alpha_total"]),
        away_alpha=float(dist["away_alpha_total"]),
        lineup_home=home_lineup,
        lineup_away=away_lineup,
        weather=weather,
        home_features=home_features,
        away_features=away_features,
        seed=seed,
        simulation_count=int(simulation_count),
    )
    ties_after_9 = int(np.sum(home_runs9 == away_runs9))
    artifact = frozen_2024_extra_inning_margin_artifact()
    home_final, away_final = resolve_tied_nine_inning_samples(
        home_runs9=home_runs9.tolist(),
        away_runs9=away_runs9.tolist(),
        extra_inning_home_win_probability=float(dist["extra_inning_home_win_probability"]),
        artifact=artifact,
        seed=seed ^ 0x5A17E11,
    )
    scored = score_final_run_line_samples(home_final, away_final, home_run_line=line)
    return {
        "status": "EXPERIMENT_CREATED",
        "code": "MLB_RUN_LINE_FORWARD_SHADOW_COMPLETE",
        "sport": "MLB",
        "score_snapshot_id": str(score_snapshot_id),
        "score_snapshot_status": str(score_identity.get("score_status") or ""),
        "shadow_event_id": str(score_identity["shadow_event_id"]),
        "distribution_id": str(score.get("distribution_id") or ""),
        "distribution_model_version": str(dist.get("model_version") or ""),
        "model_family": RUN_LINE_MODEL_FAMILY,
        "extra_inning_margin_model_family": EXTRA_MARGIN_MODEL_FAMILY,
        "extra_inning_margin_artifact_hash": artifact.training_dataset_hash,
        "extra_inning_margin_artifact_provenance": "GOVERNED_2024_COHORT_SUMMARY_PR_930",
        "extra_inning_margin_train_rows": artifact.train_rows,
        "home_run_line": line,
        **scored,
        "tie_after_9_probability": ties_after_9 / int(simulation_count),
        "extra_inning_home_win_probability": float(dist["extra_inning_home_win_probability"]),
        "projected_final_runs_home": float(np.mean(home_final)),
        "projected_final_runs_away": float(np.mean(away_final)),
        "simulation_seed": int(seed),
        "evaluation_state": "FORWARD_SHADOW_UNCERTIFIED",
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


def run_mlb_run_line_shadow_preflight(
    db: Any, *, score_snapshot_id: str, home_run_line: float
) -> dict[str, Any]:
    score = _score_snapshot_identity(db, str(score_snapshot_id))
    dist_rows = (
        db.table("wow_mlb_v2b_distribution_state")
        .select(
            "distribution_id,model_version,home_alpha_total,away_alpha_total,"
            "extra_inning_home_win_probability,extra_inning_training_games,"
            "training_end,research_only,probability_publishable,can_execute"
        )
        .eq("distribution_id", score.get("distribution_id"))
        .limit(1)
        .execute().data
        or []
    )
    if len(dist_rows) != 1:
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_DISTRIBUTION_UNAVAILABLE",
            "governed MLB run distribution state is unavailable",
        )
    dist = dict(dist_rows[0])
    artifact = frozen_2024_extra_inning_margin_artifact()
    return {
        "status": "EXPERIMENT_CREATED",
        "code": "MLB_RUN_LINE_FORWARD_SHADOW_READY",
        "sport": "MLB",
        "score_snapshot_id": str(score_snapshot_id),
        "score_snapshot_status": str(score.get("score_status") or ""),
        "distribution_id": dist.get("distribution_id"),
        "distribution_model_version": dist.get("model_version"),
        "extra_inning_margin_model_family": EXTRA_MARGIN_MODEL_FAMILY,
        "extra_inning_margin_artifact_hash": artifact.training_dataset_hash,
        "extra_inning_margin_train_rows": artifact.train_rows,
        "home_run_line": float(home_run_line),
        "market_features_used": False,
        "run_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": True,
        "can_execute": False,
    }


__all__ = [
    "ELIGIBLE_SCORE_STATUSES",
    "MLBRunLineShadowUnavailable",
    "frozen_2024_extra_inning_margin_artifact",
    "run_mlb_run_line_forward_shadow",
    "run_mlb_run_line_shadow_preflight",
    "score_final_run_line_samples",
]
