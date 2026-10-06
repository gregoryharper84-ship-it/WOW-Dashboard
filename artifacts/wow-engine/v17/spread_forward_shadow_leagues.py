"""Research-only NFL/WNBA exact-spread forward shadow scoring.

The fitted margin challenger is trained only from governed historical sporting
features. The requested spread is a post-fit threshold and never a feature.
No probability here is production-publishable or executable.

Forward serving uses the artifact-only fitter shared with the NCAAF latency
repair. Historical replay diagnostics stay on the replay path; the interactive
path must not recompute the synthetic-line evaluation grid for every request.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import httpx

from basketball_specialist_pipeline import load_games as load_basketball_games
from basketball_team_event_specialist import MIN_PRIOR_GAMES as BASKETBALL_MIN_PRIOR_GAMES
from v17.nfl_team_event_specialist import _prediction_feature_row, resolve_nfl_team_event_evidence
from v17.spread_margin_challenger import (
    AUTOMATIC_CERTIFICATION,
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
    GLOBAL_TERMINAL_REDUCER,
    MODEL_PROGRAM,
    PROBABILITY_PUBLISHABLE,
    RUNTIME_GENERATION,
    SpreadChallengerUnavailable,
    _dt,
    score_home_spread,
)
from v17.spread_margin_forward_fit import fit_margin_distribution_artifact
from v17.spread_margin_replay import load_replay_rows
from v17.wnba_spread_event_identity import resolve_wnba_current_event_identity

MIN_TRAIN_ROWS = 300
RIDGE_ALPHA = 4.0
SETTLEMENT_BASIS = "FULL_GAME_INCLUDING_OVERTIME"
CONTROLLING_SPREAD_SPECIALISTS = {
    "NFL": "NFL_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
    "WNBA": "WNBA_SPREAD_MARGIN_DISTRIBUTION_CHALLENGER_V1",
}


def _fit_forward_artifact(db: Any, *, sport: str, target_start: Any):
    """Fit the exact challenger artifact without replay-only diagnostics."""
    rows = load_replay_rows(db, sport=sport)
    cutoff = _dt(target_start)
    eligible = [row for row in rows if _dt(row.event_start_time) < cutoff]
    if len(eligible) < MIN_TRAIN_ROWS:
        raise SpreadChallengerUnavailable(
            f"{sport}_SPREAD_FORWARD_TRAINING_INSUFFICIENT",
            f"need at least {MIN_TRAIN_ROWS} prior spread rows; got {len(eligible)}",
        )
    artifact = fit_margin_distribution_artifact(
        eligible,
        sport=sport,
        min_rows=MIN_TRAIN_ROWS,
        ridge_alpha=RIDGE_ALPHA,
    )
    latest = max(_dt(row.event_start_time) for row in eligible)
    if latest >= cutoff:
        raise SpreadChallengerUnavailable(
            f"{sport}_SPREAD_FORWARD_TARGET_NOT_AFTER_TRAINING_CUTOFF",
            "forward target must be later than all fitted sporting rows",
        )
    return artifact, latest


def _validate_feature_schema(
    artifact: Any, features: Mapping[str, float], *, sport: str
) -> dict[str, float]:
    normalized = {str(name): float(value) for name, value in features.items()}
    if tuple(sorted(normalized)) != tuple(artifact.feature_names):
        raise SpreadChallengerUnavailable(
            f"{sport}_SPREAD_FORWARD_FEATURE_SCHEMA_MISMATCH",
            "forward feature schema does not match fitted spread artifact",
        )
    return normalized


def _common_result(
    *,
    sport: str,
    event_id: str,
    event_start_time: str,
    home_team: str,
    away_team: str,
    home_spread: float,
    artifact: Any,
    latest_training: Any,
    scored: Mapping[str, Any],
    feature_audit: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "status": "EXPERIMENT_CREATED",
        "code": f"{sport}_SPREAD_FORWARD_SHADOW_COMPLETE",
        "sport": sport,
        "event_id": str(event_id),
        "event_start_time": str(event_start_time),
        "home_team": str(home_team),
        "away_team": str(away_team),
        "home_spread": float(home_spread),
        "exact_line_side": "HOME",
        "exact_signed_spread": float(home_spread),
        "settlement_basis": SETTLEMENT_BASIS,
        "controlling_spread_specialist": CONTROLLING_SPREAD_SPECIALISTS[sport],
        "model_program": MODEL_PROGRAM,
        "model_family": artifact.model_family,
        "model_artifact_version": artifact.model_family,
        "feature_schema_version": artifact.feature_schema_version,
        "training_dataset_hash": artifact.training_dataset_hash,
        "artifact_train_rows": artifact.train_rows,
        "artifact_calibration_rows": artifact.calibration_rows,
        "artifact_test_rows": artifact.test_rows,
        "training_cutoff_event_time": latest_training.isoformat(),
        "model_timestamp": datetime.now(timezone.utc).isoformat(),
        "predicted_home_margin_center": scored["predicted_home_margin_center"],
        "p_cover": scored["p_cover"],
        "p_push": scored["p_push"],
        "p_not_cover": scored["p_not_cover"],
        "p_cover_given_no_push": scored["p_cover_given_no_push"],
        "research_lower_bound_cover": scored["research_lower_bound_cover"],
        "research_lower_bound_cover_unconditional": scored.get(
            "research_lower_bound_cover_unconditional",
            scored["research_lower_bound_cover"],
        ),
        "research_lower_bound_cover_given_no_push": scored.get(
            "research_lower_bound_cover_given_no_push"
        ),
        "cover_count": scored.get("cover_count"),
        "push_count": scored.get("push_count"),
        "not_cover_count": scored.get("not_cover_count"),
        "non_push_count": scored.get("non_push_count"),
        "distribution_sample_n": scored["distribution_sample_n"],
        "feature_audit": dict(feature_audit),
        "historical_replay_diagnostic": {
            "status": "NOT_RECOMPUTED_ON_FORWARD_PATH",
            "reason": "FULL_GRID_DIAGNOSTICS_REMAIN_IN_GOVERNED_HISTORICAL_REPLAY",
            "margin_mae": None,
            "cover_brier": None,
            "cover_log_loss": None,
            "cover_ece": None,
        },
        "evaluation_state": "FORWARD_SHADOW_UNCERTIFIED",
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "rank_eligible": False,
        "runtime_generation": RUNTIME_GENERATION,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "can_execute": CAN_EXECUTE,
    }


def run_nfl_forward_shadow(
    db: Any,
    *,
    event_id: str,
    event_start_time: str,
    home_team: str,
    away_team: str,
    home_spread: float,
) -> dict[str, Any]:
    target = _dt(event_start_time)
    request = SimpleNamespace(
        official_event_id=str(event_id),
        home_team=str(home_team),
        away_team=str(away_team),
        requested_slate_date=target.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
        event_start_time_utc=target.isoformat(),
        requested_timezone="America/New_York",
        settlement_basis=SETTLEMENT_BASIS,
    )
    identity = resolve_nfl_team_event_evidence(request, db=db)
    if not identity.get("ok"):
        raise SpreadChallengerUnavailable(
            str(identity.get("code") or "NFL_SPREAD_FORWARD_EVENT_IDENTITY_UNAVAILABLE"),
            "NFL canonical event identity could not be proven",
        )
    feature_row = _prediction_feature_row(
        db=db,
        evidence=dict(identity["evidence"]),
        canonical_game_id=str(identity["canonical_event_id"]),
        canonical_home_team=str(identity["canonical_home_team"]),
        canonical_away_team=str(identity["canonical_away_team"]),
    )
    artifact, latest = _fit_forward_artifact(db, sport="NFL", target_start=target)
    features = _validate_feature_schema(
        artifact, dict(feature_row.get("features") or {}), sport="NFL"
    )
    scored = score_home_spread(artifact, features, home_spread=float(home_spread))
    return _common_result(
        sport="NFL",
        event_id=str(identity["canonical_event_id"]),
        event_start_time=target.isoformat(),
        home_team=str(identity["canonical_home_team"]),
        away_team=str(identity["canonical_away_team"]),
        home_spread=float(home_spread),
        artifact=artifact,
        latest_training=latest,
        scored=scored,
        feature_audit={
            "identity_resolution": identity.get("identity_resolution"),
            "canonical_source_snapshot_id": identity.get("canonical_source_snapshot_id"),
            "feature_row_hash": feature_row.get("row_inputs_hash"),
            "feature_as_of": feature_row.get("feature_cutoff_date") or feature_row.get("max_prior_gameday"),
            "market_features_used": False,
            "moneyline_probability_used": False,
            "spread_line_used_as_feature": False,
            "can_execute": False,
        },
    )


def build_wnba_forward_features(
    games: Sequence[Any],
    *,
    target_date: date,
    home_team_id: str,
    away_team_id: str,
) -> tuple[dict[str, float], dict[str, Any]]:
    history: dict[str, list[tuple[date, int, int, bool]]] = {}
    for game in sorted(games, key=lambda g: (g.game_date, g.game_id)):
        if game.game_date >= target_date:
            continue
        history.setdefault(game.home_team_id, []).append(
            (game.game_date, game.home_score, game.away_score, bool(game.home_win))
        )
        history.setdefault(game.away_team_id, []).append(
            (game.game_date, game.away_score, game.home_score, not bool(game.home_win))
        )
    hh, ah = history.get(str(home_team_id), []), history.get(str(away_team_id), [])
    if len(hh) < BASKETBALL_MIN_PRIOR_GAMES or len(ah) < BASKETBALL_MIN_PRIOR_GAMES:
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_FORWARD_HISTORY_INSUFFICIENT",
            f"need at least {BASKETBALL_MIN_PRIOR_GAMES} prior games per team; home={len(hh)} away={len(ah)}",
        )
    hrest = max(0, (target_date - hh[-1][0]).days - 1)
    arest = max(0, (target_date - ah[-1][0]).days - 1)
    features = {
        "home_win_rate_prior": sum(x[3] for x in hh) / len(hh),
        "away_win_rate_prior": sum(x[3] for x in ah) / len(ah),
        "home_point_diff_prior": sum(x[1] - x[2] for x in hh) / len(hh),
        "away_point_diff_prior": sum(x[1] - x[2] for x in ah) / len(ah),
        "home_rest_days_capped": float(min(hrest, 7)),
        "away_rest_days_capped": float(min(arest, 7)),
        "home_back_to_back": float(hrest == 0),
        "away_back_to_back": float(arest == 0),
    }
    return (
        {name: float(value) for name, value in features.items()},
        {
            "feature_as_of": target_date.isoformat(),
            "home_prior_games": len(hh),
            "away_prior_games": len(ah),
            "market_features_used": False,
            "moneyline_probability_used": False,
            "spread_line_used_as_feature": False,
            "can_execute": False,
        },
    )


def run_wnba_forward_shadow(
    db: Any,
    *,
    event_id: str,
    event_start_time: str,
    home_team_id: str,
    away_team_id: str,
    home_spread: float,
) -> dict[str, Any]:
    identity = resolve_wnba_current_event_identity(
        event_id=event_id,
        event_start_time=event_start_time,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        fetcher=httpx.get,
    )
    target = _dt(identity["event_start_time"])
    games = load_basketball_games(db, "WNBA")
    features, audit = build_wnba_forward_features(
        games,
        target_date=target.astimezone(ZoneInfo("America/New_York")).date(),
        home_team_id=identity["home_team_id"],
        away_team_id=identity["away_team_id"],
    )
    artifact, latest = _fit_forward_artifact(db, sport="WNBA", target_start=target)
    features = _validate_feature_schema(artifact, features, sport="WNBA")
    scored = score_home_spread(artifact, features, home_spread=float(home_spread))
    audit.update(identity)
    return _common_result(
        sport="WNBA",
        event_id=identity["event_id"],
        event_start_time=target.isoformat(),
        home_team=str(identity["home_team_id"]),
        away_team=str(identity["away_team_id"]),
        home_spread=float(home_spread),
        artifact=artifact,
        latest_training=latest,
        scored=scored,
        feature_audit=audit,
    )


__all__ = [
    "CONTROLLING_SPREAD_SPECIALISTS",
    "MIN_TRAIN_ROWS",
    "RIDGE_ALPHA",
    "SETTLEMENT_BASIS",
    "build_wnba_forward_features",
    "run_nfl_forward_shadow",
    "run_wnba_forward_shadow",
]
