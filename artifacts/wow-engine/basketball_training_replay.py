"""Governed NBA/WNBA specialist training replay.

This wrapper turns the persisted settled-game corpus into an auditable training
chain before fitting:

  source provenance -> deterministic pregame features -> persisted feature hash
  -> independent league fit -> V17 calibration -> SHADOW evidence

It never promotes/activates an artifact and never enables execution.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
import hashlib
import json
from typing import Any

from basketball_specialist_pipeline import _client, fit_and_persist, load_games
from basketball_team_event_specialist import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, build_pregame_features

can_execute: bool = False

TRAIN_TABLES = {
    "NBA": "wow_nba_training_games",
    "WNBA": "wow_wnba_training_games",
}
FEATURE_TABLES = {
    "NBA": "wow_nba_pregame_feature_rows",
    "WNBA": "wow_wnba_pregame_feature_rows",
}


def verify_training_provenance(client: Any, sport: str) -> dict[str, Any]:
    sport = sport.upper().strip()
    table = TRAIN_TABLES[sport]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        result = (
            client.table(table)
            .select("game_id,source_provider,source_endpoint,source_retrieved_at,source_payload_sha256")
            .eq("settled", True)
            .order("game_id")
            .range(offset, offset + 999)
            .execute()
        )
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000

    if not rows:
        raise RuntimeError(f"{sport}_TRAINING_CORPUS_EMPTY")

    providers: set[str] = set()
    endpoints: set[str] = set()
    bad: list[str] = []
    for row in rows:
        provider = str(row.get("source_provider") or "").strip()
        endpoint = str(row.get("source_endpoint") or "").strip()
        retrieved_at = str(row.get("source_retrieved_at") or "").strip()
        payload_hash = str(row.get("source_payload_sha256") or "").strip().lower()
        if provider:
            providers.add(provider)
        if endpoint:
            endpoints.add(endpoint)
        if (
            not provider
            or not endpoint
            or not retrieved_at
            or len(payload_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in payload_hash)
        ):
            bad.append(str(row.get("game_id") or "UNKNOWN"))
            if len(bad) >= 10:
                break

    if bad:
        raise RuntimeError(
            f"{sport}_TRAINING_PROVENANCE_INCOMPLETE sample_game_ids={','.join(bad)}"
        )

    return {
        "settled_rows_checked": len(rows),
        "source_providers": sorted(providers),
        "source_endpoints": sorted(endpoints),
        "provenance_complete": True,
    }


def persist_feature_replay(client: Any, sport: str) -> dict[str, Any]:
    sport = sport.upper().strip()
    games = load_games(client, sport)
    features = build_pregame_features(games, sport)
    table = FEATURE_TABLES[sport]
    payloads: list[dict[str, Any]] = []

    for row in features:
        values = {name: float(value) for name, value in zip(FEATURE_NAMES, row.values)}
        feature_payload = {
            "sport": sport,
            "game_id": row.game_id,
            "game_date": row.game_date.isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "home_games_prior": row.home_games_prior,
            "away_games_prior": row.away_games_prior,
            "features": values,
        }
        canonical = json.dumps(feature_payload, sort_keys=True, separators=(",", ":"))
        payloads.append({
            "game_id": row.game_id,
            "as_of": datetime.combine(row.game_date, time.min, tzinfo=timezone.utc).isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "home_games_prior": row.home_games_prior,
            "away_games_prior": row.away_games_prior,
            "home_win_rate_prior": values["home_win_rate_prior"],
            "away_win_rate_prior": values["away_win_rate_prior"],
            "home_point_diff_prior": values["home_point_diff_prior"],
            "away_point_diff_prior": values["away_point_diff_prior"],
            "home_rest_days": int(values["home_rest_days_capped"]),
            "away_rest_days": int(values["away_rest_days_capped"]),
            "home_back_to_back": bool(values["home_back_to_back"]),
            "away_back_to_back": bool(values["away_back_to_back"]),
            "feature_payload": feature_payload,
            "feature_payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        })

    seen: set[str] = set()
    duplicates: list[str] = []
    for payload in payloads:
        gid = str(payload["game_id"])
        if gid in seen:
            duplicates.append(gid)
            if len(duplicates) >= 10:
                break
        seen.add(gid)
    if duplicates:
        raise RuntimeError(
            f"{sport}_FEATURE_REPLAY_DUPLICATE_GAME_IDS sample={','.join(duplicates)}"
        )

    for offset in range(0, len(payloads), 250):
        client.table(table).upsert(payloads[offset:offset + 250], on_conflict="game_id").execute()

    return {
        "sport": sport,
        "settled_game_rows": len(games),
        "persisted_feature_rows": len(payloads),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
    }


def run_training_replay(sport: str, client: Any | None = None) -> dict[str, Any]:
    sport = sport.upper().strip()
    if sport not in TRAIN_TABLES:
        raise ValueError(f"unsupported sport {sport}")
    client = client or _client()
    provenance = verify_training_provenance(client, sport)
    feature_replay = persist_feature_replay(client, sport)
    fit = fit_and_persist(
        sport,
        client=client,
        provenance_complete=bool(provenance.get("provenance_complete")),
    )
    fit["provenance_preflight"] = provenance
    fit["feature_replay"] = feature_replay
    fit["promotion_attempted"] = False
    fit["can_execute"] = False
    return fit
