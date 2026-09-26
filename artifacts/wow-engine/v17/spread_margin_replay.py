"""Read-only historical replay adapter for the V17 spread-margin challenger.

This adapter consumes existing governed historical feature/game tables and emits
research receipts. It performs no schema mutation, model registration,
probability publication, or production serving change.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from hashlib import sha256
import json
import os
from typing import Any, Mapping, Sequence

from v17.spread_margin_challenger import (
    CAN_EXECUTE,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    build_dynamic_margin_rows,
    research_receipt,
    train_margin_distribution_candidate,
)

PAGE_SIZE = 1000
SUPPORTED_REPLAY_SPORTS = ("NFL", "NBA", "WNBA", "NCAAF")


def _hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _client():
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not key:
        raise RuntimeError("SUPABASE service credential unavailable")
    return create_client(url, key)


def _date_end_utc(value: Any) -> str:
    raw = str(value or "").strip()
    day = datetime.fromisoformat(raw[:10]).date()
    return datetime.combine(day, time(23, 59, 59), tzinfo=timezone.utc).isoformat()


def _numeric_features(payload: Mapping[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in payload.items():
        if isinstance(value, bool):
            out[str(key)] = float(value)
        elif isinstance(value, (int, float)):
            out[str(key)] = float(value)
    if not out:
        raise SpreadChallengerUnavailable("SPREAD_REPLAY_FEATURES_EMPTY", "historical feature payload has no numeric features")
    return out


def adapt_nfl_rows(feature_rows: Sequence[Mapping[str, Any]], game_rows: Sequence[Mapping[str, Any]]) -> list[MarginTrainingRow]:
    games = {str(row["game_id"]): row for row in game_rows if row.get("game_id")}
    out: list[MarginTrainingRow] = []
    for feature in feature_rows:
        game_id = str(feature.get("game_id") or "")
        game = games.get(game_id)
        if not game or game.get("home_score") is None or game.get("away_score") is None:
            continue
        max_prior = feature.get("max_prior_gameday")
        gameday = feature.get("gameday") or game.get("gameday")
        if not max_prior or not gameday:
            continue
        features = _numeric_features(dict(feature.get("features") or {}))
        manifest = {
            "sport": "NFL", "game_id": game_id,
            "feature_schema_version": feature.get("feature_schema_version"),
            "max_prior_gameday": str(max_prior),
            "row_inputs_hash": feature.get("row_inputs_hash"),
            "source_content_sha256s": feature.get("source_content_sha256s"),
            "market_features_used": False,
            "moneyline_probability_used": False,
            "spread_line_used_as_feature": False,
        }
        out.append(MarginTrainingRow(
            event_id=game_id,
            event_start_time=_date_end_utc(gameday),
            feature_as_of=_date_end_utc(max_prior),
            margin=int(game["home_score"]) - int(game["away_score"]),
            features=features,
            source_manifest_sha256=_hash(manifest),
        ))
    return out


def adapt_basketball_rows(sport: str, feature_rows: Sequence[Mapping[str, Any]], game_rows: Sequence[Mapping[str, Any]]) -> list[MarginTrainingRow]:
    sport = str(sport).upper()
    if sport not in ("NBA", "WNBA"):
        raise SpreadChallengerUnavailable("SPREAD_REPLAY_SPORT_UNSUPPORTED", f"basketball adapter does not support {sport}")
    games = {str(row["game_id"]): row for row in game_rows if row.get("game_id")}
    out: list[MarginTrainingRow] = []
    for feature in feature_rows:
        game_id = str(feature.get("game_id") or "")
        game = games.get(game_id)
        if not game or game.get("home_score") is None or game.get("away_score") is None:
            continue
        payload = dict(feature.get("feature_payload") or {})
        raw_features = payload.get("features") if isinstance(payload.get("features"), Mapping) else {}
        features = _numeric_features(dict(raw_features or {}))
        as_of = feature.get("as_of")
        game_date = game.get("game_date") or payload.get("game_date")
        if not as_of or not game_date:
            continue
        feature_as_of = str(as_of).replace(" ", "T")
        if "+" not in feature_as_of and not feature_as_of.endswith("Z"):
            feature_as_of = feature_as_of + "+00:00"
        manifest = {
            "sport": sport, "game_id": game_id,
            "feature_schema_version": feature.get("feature_schema_version"),
            "feature_payload_sha256": feature.get("feature_payload_sha256"),
            "market_features_used": False,
            "moneyline_probability_used": False,
            "spread_line_used_as_feature": False,
        }
        out.append(MarginTrainingRow(
            event_id=game_id,
            event_start_time=_date_end_utc(game_date),
            feature_as_of=feature_as_of,
            margin=int(game["home_score"]) - int(game["away_score"]),
            features=features,
            source_manifest_sha256=_hash(manifest),
        ))
    return out


def adapt_ncaaf_rows(game_rows: Sequence[Mapping[str, Any]], *, min_prior_games: int = 5) -> list[MarginTrainingRow]:
    """Reconstruct leakage-safe NCAAF team-state rows from settled prior results.

    NCAAF currently has settled governed training games but no populated
    `wow_ncaaf_training_features` rows. This adapter therefore uses the same
    prior-only V17 team-state feature builder used by the challenger itself,
    rather than inventing advanced features or falling back to market inputs.
    """
    events: list[dict[str, Any]] = []
    for row in game_rows:
        if not row.get("official_event_id") or not row.get("event_start_time"):
            continue
        if row.get("home_points") is None or row.get("away_points") is None:
            continue
        events.append({
            "event_id": str(row["official_event_id"]),
            "event_start_time": str(row["event_start_time"]),
            "season": row.get("season"),
            "home_team": str(row.get("home_team") or ""),
            "away_team": str(row.get("away_team") or ""),
            "home_score": int(row["home_points"]),
            "away_score": int(row["away_points"]),
            "source_manifest": {
                "result_source": row.get("result_source"),
                "result_source_timestamp": row.get("result_source_timestamp"),
                "training_game_id": row.get("training_game_id"),
                "market_features_used": False,
                "moneyline_probability_used": False,
                "spread_line_used_as_feature": False,
            },
        })
    if not events:
        raise SpreadChallengerUnavailable("SPREAD_REPLAY_GAMES_EMPTY", "NCAAF settled training games are unavailable")
    rows, _ = build_dynamic_margin_rows(events, sport="NCAAF", min_prior_games=min_prior_games)
    return rows


def _paged_select(client: Any, table: str, fields: str, *, filters: Sequence[tuple[str, str, Any]] = (), order: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = client.table(table).select(fields)
        for method, column, value in filters:
            query = getattr(query, method)(column, value)
        batch = query.order(order).range(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(dict(row) for row in batch)
        if len(batch) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def load_replay_rows(client: Any, *, sport: str) -> list[MarginTrainingRow]:
    sport = str(sport).upper()
    if sport == "NFL":
        features = _paged_select(
            client, "wow_nfl_pregame_feature_rows",
            "game_id,gameday,feature_schema_version,max_prior_gameday,features,source_content_sha256s,row_inputs_hash,training_eligible",
            filters=(("eq", "training_eligible", True),), order="gameday",
        )
        games = _paged_select(
            client, "wow_nfl_training_games",
            "game_id,gameday,home_score,away_score,probability_publishable,can_execute",
            order="gameday",
        )
        return adapt_nfl_rows(features, games)
    if sport in ("NBA", "WNBA"):
        prefix = sport.lower()
        features = _paged_select(
            client, f"wow_{prefix}_pregame_feature_rows",
            "game_id,as_of,feature_schema_version,feature_payload,feature_payload_sha256",
            order="as_of",
        )
        games = _paged_select(
            client, f"wow_{prefix}_training_games",
            "game_id,game_date,home_score,away_score,settled",
            filters=(("eq", "settled", True),), order="game_date",
        )
        return adapt_basketball_rows(sport, features, games)
    if sport == "NCAAF":
        games = _paged_select(
            client, "wow_ncaaf_training_games",
            "training_game_id,official_event_id,season,event_start_time,home_team,away_team,home_points,away_points,result_source,result_source_timestamp,can_execute",
            order="event_start_time",
        )
        return adapt_ncaaf_rows(games)
    if sport == "NCAAB":
        raise SpreadChallengerUnavailable(
            "SPREAD_REPLAY_DATASET_UNAVAILABLE",
            "NCAAB governed training game/feature tables are not available for spread replay",
        )
    raise SpreadChallengerUnavailable("SPREAD_REPLAY_SPORT_UNSUPPORTED", f"unsupported replay sport: {sport}")


def run_historical_replay(*, sport: str, client: Any | None = None, min_rows: int = 300, ridge_alpha: float = 4.0) -> dict[str, Any]:
    db = client or _client()
    rows = load_replay_rows(db, sport=sport)
    artifact, metrics = train_margin_distribution_candidate(rows, sport=sport, min_rows=min_rows, ridge_alpha=ridge_alpha)
    receipt = research_receipt(artifact, metrics)
    receipt.update({
        "historical_replay": True,
        "historical_row_count": len(rows),
        "source_tables_read_only": True,
        "database_mutated": False,
        "production_registry_mutated": False,
        "can_execute": CAN_EXECUTE,
    })
    return {"artifact": artifact.payload(), "receipt": receipt}


__all__ = [
    "PAGE_SIZE", "SUPPORTED_REPLAY_SPORTS", "adapt_basketball_rows", "adapt_ncaaf_rows", "adapt_nfl_rows",
    "load_replay_rows", "run_historical_replay",
]
