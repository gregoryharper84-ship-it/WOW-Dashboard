"""Leakage-safe NCAAF result/form candidate built only from settled prior games.

This is deliberately separate from the richer ``NCAAF_FEATURES_V1`` lane. It
never invents historical QB/injury/OL/weather evidence and never consumes market
prices. Every feature for a game is reconstructed from games that occurred
strictly earlier for the two participants. The resulting artifact is research
CANDIDATE evidence only; certification/promotion/publication remain separate.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

from v17.binary_candidate_lifecycle import (
    BinaryCandidateError,
    BinaryTrainingRow,
    train_binary_candidate,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SPORT = "NCAAF"
LEAGUE = "NCAAF"
MARKET_FAMILY = "OUTRIGHT_WINNER"
MODEL_FAMILY = "NCAAF_RESULT_FORM_LOGIT_V1"
FEATURE_SCHEMA_VERSION = "NCAAF_RESULT_FORM_PRIOR_V1"
SOURCE_POLICY_ID = "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1"
FEATURE_NAMES = (
    "home_win_rate_prior",
    "away_win_rate_prior",
    "home_point_diff_prior",
    "away_point_diff_prior",
    "home_games_prior_log",
    "away_games_prior_log",
    "home_rest_days_capped",
    "away_rest_days_capped",
    "neutral_site",
)


class NCAAFResultFormUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_EVENT_TIME_MISSING", "event time missing")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _paged_games(client: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        result = (
            client.table("wow_ncaaf_training_games")
            .select(
                "training_game_id,official_event_id,season,week,event_start_time,neutral_site,"
                "home_team,away_team,home_points,away_points,home_won,result_source,result_source_timestamp"
            )
            .order("event_start_time")
            .order("official_event_id")
            .range(offset, offset + 999)
            .execute()
        )
        batch = list(result.data or [])
        rows.extend(dict(row) for row in batch if isinstance(row, Mapping))
        if len(batch) < 1000:
            break
        offset += 1000
    if not rows:
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_GAMES_EMPTY", "no settled NCAAF games")
    return rows


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _team_summary(history: list[dict[str, Any]], as_of: datetime) -> tuple[dict[str, float], list[str]] | None:
    prior = [g for g in history if g["event_start"] < as_of]
    if len(prior) < 3:
        return None
    wins = sum(1 for g in prior if g["won"])
    point_diff = sum(float(g["point_diff"]) for g in prior) / len(prior)
    last_start = max(g["event_start"] for g in prior)
    rest_days = max(0.0, min(28.0, (as_of - last_start).total_seconds() / 86400.0))
    import math

    return (
        {
            "win_rate": wins / len(prior),
            "point_diff": point_diff,
            "games_prior_log": math.log1p(len(prior)),
            "rest_days_capped": rest_days,
        },
        [str(g["event_id"]) for g in prior],
    )


def build_training_rows(games: list[dict[str, Any]]) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]]]:
    ordered = sorted(games, key=lambda g: (_parse_time(g.get("event_start_time")), str(g.get("official_event_id") or "")))
    team_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    output: list[BinaryTrainingRow] = []
    metadata: list[dict[str, Any]] = []

    for game in ordered:
        event_id = str(game.get("official_event_id") or "").strip()
        home = str(game.get("home_team") or "").strip()
        away = str(game.get("away_team") or "").strip()
        if not event_id or not home or not away or game.get("home_won") is None:
            continue
        event_start = _parse_time(game.get("event_start_time"))
        home_summary = _team_summary(team_history[home], event_start)
        away_summary = _team_summary(team_history[away], event_start)
        if home_summary is not None and away_summary is not None:
            h, home_ids = home_summary
            a, away_ids = away_summary
            features = {
                "home_win_rate_prior": h["win_rate"],
                "away_win_rate_prior": a["win_rate"],
                "home_point_diff_prior": h["point_diff"],
                "away_point_diff_prior": a["point_diff"],
                "home_games_prior_log": h["games_prior_log"],
                "away_games_prior_log": a["games_prior_log"],
                "home_rest_days_capped": h["rest_days_capped"],
                "away_rest_days_capped": a["rest_days_capped"],
                "neutral_site": 1.0 if bool(game.get("neutral_site")) else 0.0,
            }
            manifest = {
                "policy": SOURCE_POLICY_ID,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "event_id": event_id,
                "result_source": str(game.get("result_source") or ""),
                "home_prior_event_ids": home_ids,
                "away_prior_event_ids": away_ids,
                "historical_reconstruction": True,
                "market_features_used": False,
            }
            manifest_sha = _canonical_hash(manifest)
            feature_as_of = (event_start - timedelta(seconds=1)).isoformat()
            output.append(BinaryTrainingRow(
                event_id=event_id,
                event_start_time=event_start.isoformat(),
                feature_as_of=feature_as_of,
                positive_outcome=bool(game.get("home_won")),
                features=features,
                source_manifest_sha256=manifest_sha,
            ))
            metadata.append({
                "event_id": event_id,
                "source_manifest": manifest,
                "source_manifest_sha256": manifest_sha,
                "season": game.get("season"),
            })

        hp = int(game.get("home_points") or 0)
        ap = int(game.get("away_points") or 0)
        team_history[home].append({
            "event_id": event_id,
            "event_start": event_start,
            "won": hp > ap,
            "point_diff": hp - ap,
        })
        team_history[away].append({
            "event_id": event_id,
            "event_start": event_start,
            "won": ap > hp,
            "point_diff": ap - hp,
        })

    if len(output) < 300:
        raise NCAAFResultFormUnavailable(
            "NCAAF_RESULT_FORM_SAMPLE_INSUFFICIENT", f"eligible_rows={len(output)}; minimum=300"
        )
    return output, metadata


def _persist_training_rows(client: Any, rows: list[BinaryTrainingRow], metadata: list[dict[str, Any]]) -> None:
    payloads: list[dict[str, Any]] = []
    for row, meta in zip(rows, metadata):
        payloads.append({
            "sport": SPORT,
            "league": LEAGUE,
            "official_event_id": row.event_id,
            "event_start_time": row.event_start_time,
            "feature_as_of": row.feature_as_of,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "model_family": MODEL_FAMILY,
            "features": dict(row.features),
            "outcome_json": {"home_win": bool(row.positive_outcome)},
            "source_manifest": meta["source_manifest"],
            "source_manifest_sha256": row.source_manifest_sha256,
            "historical_reconstruction": True,
            "archived_pregame_snapshot": False,
            "market_features_used": False,
            "can_execute": False,
        })
    for offset in range(0, len(payloads), 250):
        client.table("wow_d1_training_rows").upsert(
            payloads[offset:offset + 250],
            on_conflict="sport,model_family,official_event_id,feature_schema_version",
        ).execute()


def train_and_persist(client: Any, *, training_code_sha: str) -> dict[str, Any]:
    code_sha = str(training_code_sha or "").strip().lower()
    if len(code_sha) < 7:
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_CODE_SHA_REQUIRED", "training code SHA required")
    games = _paged_games(client)
    rows, metadata = build_training_rows(games)
    try:
        candidate = train_binary_candidate(
            rows,
            model_family=MODEL_FAMILY,
            feature_names=FEATURE_NAMES,
            min_rows=300,
        )
    except BinaryCandidateError as exc:
        raise NCAAFResultFormUnavailable(exc.code, str(exc)) from exc

    _persist_training_rows(client, rows, metadata)
    artifact_payload = dict(candidate.artifact_payload)
    calibrator_payload = dict(candidate.calibrator_payload)
    artifact_checksum = _canonical_hash(artifact_payload)
    version = f"NCAAF_RESULT_FORM_LOGIT_V1_{candidate.dataset_hash[:16]}_{code_sha[:12]}"
    metrics = asdict(candidate.metrics)
    metrics.update({
        "research_screen_pass": candidate.research_screen_pass,
        "split_policy": "CHRONOLOGICAL_60_20_20",
        "untouched_test": True,
        "historical_reconstruction": True,
        "archived_pregame_snapshot": False,
        "market_features_used": False,
    })
    client.table("wow_d1_candidate_artifacts").insert({
        "sport": SPORT,
        "league": LEAGUE,
        "market_family": MARKET_FAMILY,
        "model_family": MODEL_FAMILY,
        "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "source_policy_id": SOURCE_POLICY_ID,
        "training_dataset_hash": candidate.dataset_hash,
        "training_code_sha": code_sha,
        "artifact_checksum": artifact_checksum,
        "artifact_payload": artifact_payload,
        "calibrator_payload": calibrator_payload,
        "validation_metrics": metrics,
        "training_rows": candidate.metrics.train_n,
        "calibration_rows": candidate.metrics.calibration_n,
        "test_rows": candidate.metrics.test_n,
        "research_screen_pass": candidate.research_screen_pass,
        "source_review_status": "RECONSTRUCTED_PRIOR_RESULTS_PROVENANCE_READY",
        "lifecycle_state": "CANDIDATE",
        "promoted": False,
        "active": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }).execute()
    return {
        "ok": True,
        "code": "NCAAF_RESULT_FORM_CANDIDATE_PERSISTED",
        "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "eligible_rows": len(rows),
        "metrics": metrics,
        "research_screen_pass": candidate.research_screen_pass,
        "lifecycle_state": "CANDIDATE",
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "FEATURE_NAMES",
    "FEATURE_SCHEMA_VERSION",
    "MODEL_FAMILY",
    "NCAAFResultFormUnavailable",
    "SOURCE_POLICY_ID",
    "build_training_rows",
    "train_and_persist",
]
