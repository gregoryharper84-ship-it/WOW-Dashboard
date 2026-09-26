"""Provider-bound exact-line replay for the V17 spread challenger.

This runner composes the existing fitted margin challenger with persisted
TheRundown spread evidence. It is read-only with respect to model/prediction
state. The sportsbook line is supplied only after fitting as an evaluation
threshold.
"""
from __future__ import annotations

from typing import Any

from v17 import rundown_market_ledger as ledger
from v17.spread_exact_line_evidence import bind_exact_home_spreads, evaluate_exact_line_candidate
from v17.spread_margin_challenger import (
    SpreadChallengerUnavailable,
    _chronological_split,
    train_margin_distribution_candidate,
)
from v17.spread_margin_replay import load_replay_rows
from v17.spread_market_evidence import SPORT_KEYS

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
SUPPORTED_IDENTITY_SPORTS = ("NFL", "NCAAF")


def _paginate(query: Any, *, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        response = query.range(start, start + page_size - 1).execute()
        batch = getattr(response, "data", None) or []
        rows.extend(dict(row) for row in batch if isinstance(row, dict))
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def load_event_identities(client: Any, *, sport: str) -> list[dict[str, Any]]:
    sport = str(sport).upper()
    if sport == "NFL":
        rows = _paginate(
            client.table("wow_nfl_training_games")
            .select("game_id,gameday,home_team,away_team")
            .order("gameday")
            .order("game_id")
        )
        return [
            {
                "event_id": str(row["game_id"]),
                "event_start_time": f"{row['gameday']}T12:00:00+00:00",
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
            }
            for row in rows
            if row.get("game_id") and row.get("gameday") and row.get("home_team") and row.get("away_team")
        ]
    if sport == "NCAAF":
        rows = _paginate(
            client.table("wow_ncaaf_training_games")
            .select("official_event_id,event_start_time,home_team,away_team")
            .order("event_start_time")
            .order("official_event_id")
        )
        return [
            {
                "event_id": str(row["official_event_id"]),
                "event_start_time": str(row["event_start_time"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
            }
            for row in rows
            if row.get("official_event_id") and row.get("event_start_time") and row.get("home_team") and row.get("away_team")
        ]
    if sport in {"NBA", "WNBA"}:
        raise SpreadChallengerUnavailable(
            "SPREAD_EXACT_LINE_EVENT_IDENTITY_UNAVAILABLE",
            f"{sport} training corpus currently persists provider team IDs without a reviewed market-provider team-name crosswalk",
        )
    if sport == "NCAAB":
        raise SpreadChallengerUnavailable(
            "SPREAD_REPLAY_DATASET_UNAVAILABLE",
            "NCAAB spread replay still lacks governed score-margin labels despite available D1 feature rows",
        )
    raise SpreadChallengerUnavailable("SPREAD_REPLAY_SPORT_UNSUPPORTED", f"unsupported spread replay sport: {sport}")


def load_spread_market_rows(client: Any, *, sport: str) -> list[dict[str, Any]]:
    sport = str(sport).upper()
    sport_key = SPORT_KEYS.get(sport)
    if sport_key is None:
        raise SpreadChallengerUnavailable("SPREAD_REPLAY_SPORT_UNSUPPORTED", f"unsupported spread replay sport: {sport}")
    return _paginate(
        client.table(ledger.TABLE)
        .select(
            "provider_event_id,sport_key,event_start_utc,market_id,market_name,participant_id,participant_name,"
            "participant_type,selection,line_id,line_value,affiliate_id,sportsbook,price_updated_at,fetched_at,"
            "snapshot_kind,is_live,is_main_line,is_available,closed_at"
        )
        .eq("provider", ledger.PROVIDER)
        .eq("sport_key", sport_key)
        .order("event_start_utc")
        .order("provider_event_id")
    )


def run_exact_line_replay(
    *,
    sport: str,
    client: Any,
    min_rows: int = 300,
    ridge_alpha: float = 4.0,
) -> dict[str, Any]:
    """Fit on sporting data, then evaluate only overlapping provider-bound lines."""
    sport = str(sport).upper()
    rows = load_replay_rows(client, sport=sport)
    artifact, synthetic_metrics = train_margin_distribution_candidate(
        rows,
        sport=sport,
        min_rows=min_rows,
        ridge_alpha=ridge_alpha,
    )
    _train, _calibration, test = _chronological_split(rows, min_rows=min_rows)
    test_ids = {row.event_id for row in test}
    identities = [row for row in load_event_identities(client, sport=sport) if str(row.get("event_id")) in test_ids]
    if not identities:
        raise SpreadChallengerUnavailable(
            "SPREAD_EXACT_LINE_EVENT_IDENTITY_UNAVAILABLE",
            "no exact event identities overlap the held-out replay rows",
        )
    market_rows = load_spread_market_rows(client, sport=sport)
    bound, binding_audit = bind_exact_home_spreads(
        sport=sport,
        event_identities=identities,
        market_rows=market_rows,
    )
    exact_metrics = evaluate_exact_line_candidate(artifact, test, bound)
    return {
        "status": "EXPERIMENT_CREATED",
        "sport": sport,
        "model_family": artifact.model_family,
        "training_dataset_hash": artifact.training_dataset_hash,
        "artifact_train_rows": artifact.train_rows,
        "artifact_calibration_rows": artifact.calibration_rows,
        "artifact_test_rows": artifact.test_rows,
        "synthetic_grid_diagnostic": {
            "margin_mae": synthetic_metrics.get("margin_mae"),
            "margin_rmse": synthetic_metrics.get("margin_rmse"),
            "cover_brier": synthetic_metrics.get("cover_brier"),
            "cover_log_loss": synthetic_metrics.get("cover_log_loss"),
            "cover_ece": synthetic_metrics.get("cover_ece"),
        },
        "exact_line_metrics": exact_metrics,
        "binding_audit": binding_audit,
        "market_features_used": False,
        "spread_line_used_as_feature": False,
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "database_mutated": False,
        "production_registry_mutated": False,
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "SUPPORTED_IDENTITY_SPORTS",
    "load_event_identities",
    "load_spread_market_rows",
    "run_exact_line_replay",
]
