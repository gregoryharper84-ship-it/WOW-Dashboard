"""Current-slate NCAAF point-spread forward shadow scoring.

This is a Class C challenger serving surface. It re-fits the existing governed
NCAAF spread-margin challenger from immutable prior-only team-state training
rows, constructs the target matchup from settled prior sporting results only,
and applies the requested spread solely as a post-fit threshold.

It cannot certify, promote, publish, rank as production, or execute a wager.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping, Sequence

from v17.spread_margin_challenger import (
    AUTOMATIC_CERTIFICATION,
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
    GLOBAL_TERMINAL_REDUCER,
    MODEL_PROGRAM,
    PROBABILITY_PUBLISHABLE,
    RUNTIME_GENERATION,
    SPORT_CONFIG,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    _append_history,
    _dt,
    score_home_spread,
)
from v17.spread_margin_forward_fit import fit_margin_distribution_artifact
from v17.spread_margin_replay import (
    _load_ncaaf_persisted_feature_rows,
    _load_ncaaf_persisted_game_rows,
    _ncaaf_events_from_games,
    adapt_ncaaf_persisted_rows,
)
from v17.team_state_intelligence import FEATURE_FAMILY_VERSION, build_team_state, paired_matchup_features

SPORT = "NCAAF"
MIN_PRIOR_GAMES = 5
MIN_TRAIN_ROWS = 300
RIDGE_ALPHA = 4.0


def load_ncaaf_settled_events(client: Any) -> list[dict[str, Any]]:
    """Load settled NCAAF sporting history with stable event-id pagination."""
    events = _ncaaf_events_from_games(_load_ncaaf_persisted_game_rows(client))
    if not events:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_HISTORY_UNAVAILABLE",
            "NCAAF settled sporting history is unavailable for forward shadow scoring",
        )
    return events


def load_ncaaf_forward_context(client: Any) -> tuple[list[MarginTrainingRow], list[dict[str, Any]]]:
    """Load immutable training rows and settled matchup history once.

    The pre-#919 path loaded the entire NCAAF settled-game table once while
    binding immutable training features and then loaded it again to construct
    the target matchup.  This context preserves the same integrity/staleness
    checks while reusing the already-loaded settled corpus.
    """
    feature_rows = _load_ncaaf_persisted_feature_rows(client)
    game_rows = _load_ncaaf_persisted_game_rows(client)
    replay_rows = adapt_ncaaf_persisted_rows(feature_rows, game_rows)
    settled_events = _ncaaf_events_from_games(game_rows)
    if not replay_rows:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_TRAINING_UNAVAILABLE",
            "NCAAF spread replay rows are unavailable",
        )
    if not settled_events:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_HISTORY_UNAVAILABLE",
            "NCAAF settled sporting history is unavailable for forward shadow scoring",
        )

    latest_feature = max(_dt(row.event_start_time) for row in replay_rows)
    latest_settled = max(_dt(row["event_start_time"]) for row in settled_events)
    if latest_feature != latest_settled:
        raise SpreadChallengerUnavailable(
            "SPREAD_REPLAY_PERSISTED_NCAAF_FEATURES_STALE",
            "immutable NCAAF dynamic team-state rows lag the latest settled training game",
        )
    return replay_rows, settled_events


def build_forward_matchup_features(
    settled_events: Sequence[Mapping[str, Any]],
    *,
    target_event: Mapping[str, Any],
    min_prior_games: int = MIN_PRIOR_GAMES,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Build the same prior-only team-state feature family used by replay."""
    target_start = _dt(target_event.get("event_start_time"))
    home = str(target_event.get("home_team") or "").strip()
    away = str(target_event.get("away_team") or "").strip()
    if not home or not away:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_EVENT_IDENTITY_INCOMPLETE",
            "home_team and away_team are required",
        )
    if home == away:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_EVENT_IDENTITY_INVALID",
            "home_team and away_team must differ",
        )

    history: dict[str, list[dict[str, Any]]] = {}
    consumed = 0
    latest_prior = None
    for event in sorted(settled_events, key=lambda row: (_dt(row["event_start_time"]), str(row.get("event_id") or ""))):
        start = _dt(event.get("event_start_time"))
        if start >= target_start:
            continue
        if event.get("home_score") is None or event.get("away_score") is None:
            continue
        event_home = str(event.get("home_team") or "")
        event_away = str(event.get("away_team") or "")
        if not event_home or not event_away:
            continue
        _append_history(
            history,
            event,
            home=event_home,
            away=event_away,
            start=start,
            home_score=int(event["home_score"]),
            away_score=int(event["away_score"]),
        )
        consumed += 1
        latest_prior = start if latest_prior is None or start > latest_prior else latest_prior

    home_history = history.get(home, [])
    away_history = history.get(away, [])
    if len(home_history) < min_prior_games or len(away_history) < min_prior_games:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_HISTORY_INSUFFICIENT",
            f"need at least {min_prior_games} prior games per team; home={len(home_history)} away={len(away_history)}",
        )

    expected = int(SPORT_CONFIG[SPORT]["expected_season_games"])
    home_state = build_team_state(
        home_history,
        target_time=target_start,
        expected_season_games=expected,
        current_roster=target_event.get("home_roster_ids"),
        current_lineup=target_event.get("home_lineup_ids"),
        target_season=target_event.get("season"),
    )
    away_state = build_team_state(
        away_history,
        target_time=target_start,
        expected_season_games=expected,
        current_roster=target_event.get("away_roster_ids"),
        current_lineup=target_event.get("away_lineup_ids"),
        target_season=target_event.get("season"),
    )
    paired = paired_matchup_features(home_state, away_state)
    features = {name: float(paired[name]) for name in sorted(paired)}
    return features, {
        "feature_family_version": FEATURE_FAMILY_VERSION,
        "feature_as_of": (target_start - timedelta(seconds=1)).isoformat(),
        "latest_settled_prior_event_time": latest_prior.isoformat() if latest_prior is not None else None,
        "settled_events_consumed": consumed,
        "home_prior_events": len(home_history),
        "away_prior_events": len(away_history),
        "market_features_used": False,
        "moneyline_probability_used": False,
        "spread_line_used_as_feature": False,
        "manual_probability_adjustments": False,
        "can_execute": False,
    }


def run_ncaaf_forward_shadow(
    client: Any,
    *,
    event_id: str,
    event_start_time: str,
    home_team: str,
    away_team: str,
    home_spread: float,
    season: int | None = None,
) -> dict[str, Any]:
    """Return research-only exact-line cover distribution for one future NCAAF event."""
    target_start = _dt(event_start_time)
    if season is None:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SEASON_REQUIRED",
            "season is required for current-slate spread shadow scoring",
        )
    if int(season) != int(target_start.year):
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SEASON_MISMATCH",
            "season must match the target event calendar year",
        )
    try:
        line = float(home_spread)
    except (TypeError, ValueError) as exc:
        raise SpreadChallengerUnavailable("SPREAD_FORWARD_LINE_INVALID", "home_spread must be numeric") from exc
    if not (-100.0 < line < 100.0):
        raise SpreadChallengerUnavailable("SPREAD_FORWARD_LINE_INVALID", "home_spread is outside supported sanity bounds")

    replay_rows, settled = load_ncaaf_forward_context(client)
    latest_training_event = max(_dt(row.event_start_time) for row in replay_rows)
    if latest_training_event >= target_start:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_TARGET_NOT_AFTER_TRAINING_CUTOFF",
            "forward-shadow target must be later than every fitted/replay sporting row",
        )

    artifact = fit_margin_distribution_artifact(
        replay_rows,
        sport=SPORT,
        min_rows=MIN_TRAIN_ROWS,
        ridge_alpha=RIDGE_ALPHA,
    )
    features, feature_audit = build_forward_matchup_features(
        settled,
        target_event={
            "event_id": str(event_id),
            "event_start_time": str(event_start_time),
            "home_team": str(home_team),
            "away_team": str(away_team),
            "season": int(season),
        },
    )
    if tuple(sorted(features)) != tuple(artifact.feature_names):
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_FEATURE_SCHEMA_MISMATCH",
            "forward feature schema does not match fitted artifact",
        )

    scored = score_home_spread(artifact, features, home_spread=line)
    return {
        "status": "EXPERIMENT_CREATED",
        "code": "SPREAD_FORWARD_SHADOW_COMPLETE",
        "sport": SPORT,
        "event_id": str(event_id),
        "event_start_time": str(event_start_time),
        "season": int(season),
        "home_team": str(home_team),
        "away_team": str(away_team),
        "home_spread": line,
        "model_program": MODEL_PROGRAM,
        "model_family": artifact.model_family,
        "feature_schema_version": artifact.feature_schema_version,
        "training_dataset_hash": artifact.training_dataset_hash,
        "artifact_train_rows": artifact.train_rows,
        "artifact_calibration_rows": artifact.calibration_rows,
        "artifact_test_rows": artifact.test_rows,
        "training_cutoff_event_time": latest_training_event.isoformat(),
        "predicted_home_margin_center": scored["predicted_home_margin_center"],
        "p_cover": scored["p_cover"],
        "p_push": scored["p_push"],
        "p_not_cover": scored["p_not_cover"],
        "p_cover_given_no_push": scored["p_cover_given_no_push"],
        "research_lower_bound_cover": scored["research_lower_bound_cover"],
        "distribution_sample_n": scored["distribution_sample_n"],
        "feature_audit": feature_audit,
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
        "runtime_generation": RUNTIME_GENERATION,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "dry_run_only_no_live_trading_no_market_orders": DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "MIN_PRIOR_GAMES",
    "MIN_TRAIN_ROWS",
    "RIDGE_ALPHA",
    "SPORT",
    "build_forward_matchup_features",
    "load_ncaaf_forward_context",
    "load_ncaaf_settled_events",
    "run_ncaaf_forward_shadow",
]
