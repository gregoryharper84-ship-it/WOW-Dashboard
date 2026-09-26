"""Leakage-safe NCAAF result/form research candidate.

This lane is intentionally separate from rich ``NCAAF_FEATURES_V1``. It uses
only settled games strictly prior to each event, never market prices and never
invented historical injury/QB/OL evidence. It may persist CANDIDATE evidence;
certification, activation, publication and execution remain separate.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Any, Mapping

from v17.binary_candidate_lifecycle import BinaryCandidateError, BinaryTrainingRow, train_binary_candidate

CAN_EXECUTE = False
SPORT = LEAGUE = "NCAAF"
MARKET_FAMILY = "OUTRIGHT_WINNER"
MODEL_FAMILY = "NCAAF_RESULT_FORM_LOGIT_V1"
FEATURE_SCHEMA_VERSION = "NCAAF_RESULT_FORM_PRIOR_V1"
SOURCE_POLICY_ID = "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1"
FEATURE_NAMES = (
    "home_win_rate_prior", "away_win_rate_prior",
    "home_point_diff_prior", "away_point_diff_prior",
    "home_games_prior_log", "away_games_prior_log",
    "home_rest_days_capped", "away_rest_days_capped", "neutral_site",
)


class NCAAFResultFormUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _dt(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_EVENT_TIME_MISSING", "event time missing")
    value_dt = datetime.fromisoformat(text)
    return (value_dt if value_dt.tzinfo else value_dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _hash(payload: Mapping[str, Any]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _load_games(client: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    columns = (
        "training_game_id,official_event_id,season,week,event_start_time,neutral_site,"
        "home_team,away_team,home_points,away_points,home_won,result_source,result_source_timestamp"
    )
    while True:
        result = (client.table("wow_ncaaf_training_games").select(columns)
                  .order("event_start_time").order("official_event_id")
                  .range(offset, offset + 999).execute())
        batch = list(result.data or [])
        rows.extend(dict(row) for row in batch if isinstance(row, Mapping))
        if len(batch) < 1000:
            break
        offset += 1000
    if not rows:
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_GAMES_EMPTY", "no NCAAF training games")
    return rows


def _summary(history: list[dict[str, Any]], event_start: datetime):
    prior = [row for row in history if row["event_start"] < event_start]
    if len(prior) < 3:
        return None
    return ({
        "win_rate": sum(1 for row in prior if row["won"]) / len(prior),
        "point_diff": sum(float(row["point_diff"]) for row in prior) / len(prior),
        "games_prior_log": math.log1p(len(prior)),
        "rest_days_capped": max(0.0, min(28.0, (event_start - prior[-1]["event_start"]).total_seconds() / 86400.0)),
    }, [str(row["event_id"]) for row in prior])


def build_training_rows(games: list[dict[str, Any]]) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]]]:
    ordered = sorted(games, key=lambda row: (_dt(row.get("event_start_time")), str(row.get("official_event_id") or "")))
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[BinaryTrainingRow] = []
    meta: list[dict[str, Any]] = []
    for game in ordered:
        event_id = str(game.get("official_event_id") or "").strip()
        home, away = str(game.get("home_team") or "").strip(), str(game.get("away_team") or "").strip()
        if not event_id or not home or not away or game.get("home_won") is None:
            continue
        start = _dt(game.get("event_start_time"))
        hs, aws = _summary(history[home], start), _summary(history[away], start)
        if hs and aws:
            h, home_ids = hs
            a, away_ids = aws
            features = {
                "home_win_rate_prior": h["win_rate"], "away_win_rate_prior": a["win_rate"],
                "home_point_diff_prior": h["point_diff"], "away_point_diff_prior": a["point_diff"],
                "home_games_prior_log": h["games_prior_log"], "away_games_prior_log": a["games_prior_log"],
                "home_rest_days_capped": h["rest_days_capped"], "away_rest_days_capped": a["rest_days_capped"],
                "neutral_site": 1.0 if bool(game.get("neutral_site")) else 0.0,
            }
            manifest = {
                "policy": SOURCE_POLICY_ID, "event_id": event_id,
                "result_source": str(game.get("result_source") or ""),
                "home_prior_event_ids": home_ids, "away_prior_event_ids": away_ids,
                "historical_reconstruction": True, "market_features_used": False,
            }
            manifest_sha = _hash(manifest)
            rows.append(BinaryTrainingRow(
                event_id=event_id, event_start_time=start.isoformat(),
                feature_as_of=(start - timedelta(seconds=1)).isoformat(),
                positive_outcome=bool(game.get("home_won")), features=features,
                source_manifest_sha256=manifest_sha,
            ))
            meta.append({"source_manifest": manifest, "season": game.get("season")})
        hp, ap = int(game.get("home_points") or 0), int(game.get("away_points") or 0)
        history[home].append({"event_id": event_id, "event_start": start, "won": hp > ap, "point_diff": hp - ap})
        history[away].append({"event_id": event_id, "event_start": start, "won": ap > hp, "point_diff": ap - hp})
    if len(rows) < 300:
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_SAMPLE_INSUFFICIENT", f"eligible_rows={len(rows)}; minimum=300")
    return rows, meta


def _persist_training_rows(client: Any, rows: list[BinaryTrainingRow], meta: list[dict[str, Any]]) -> None:
    payloads = [{
        "sport": SPORT, "league": LEAGUE, "official_event_id": row.event_id,
        "event_start_time": row.event_start_time, "feature_as_of": row.feature_as_of,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "model_family": MODEL_FAMILY,
        "features": dict(row.features), "outcome_json": {"home_win": bool(row.positive_outcome)},
        "source_manifest": info["source_manifest"], "source_manifest_sha256": row.source_manifest_sha256,
        "historical_reconstruction": True, "archived_pregame_snapshot": False,
        "market_features_used": False, "can_execute": False,
    } for row, info in zip(rows, meta)]
    for offset in range(0, len(payloads), 250):
        client.table("wow_d1_training_rows").upsert(
            payloads[offset:offset + 250],
            on_conflict="sport,official_event_id,feature_schema_version,source_manifest_sha256",
            ignore_duplicates=True,
        ).execute()


def train_and_persist(client: Any, *, training_code_sha: str) -> dict[str, Any]:
    code_sha = str(training_code_sha or "").strip().lower()
    if len(code_sha) < 7:
        raise NCAAFResultFormUnavailable("NCAAF_RESULT_FORM_CODE_SHA_REQUIRED", "training code SHA required")
    rows, meta = build_training_rows(_load_games(client))
    try:
        candidate = train_binary_candidate(rows, model_family=MODEL_FAMILY, feature_names=FEATURE_NAMES, min_rows=300)
    except BinaryCandidateError as exc:
        raise NCAAFResultFormUnavailable(exc.code, str(exc)) from exc
    _persist_training_rows(client, rows, meta)
    artifact_payload, calibrator_payload = dict(candidate.artifact_payload), dict(candidate.calibrator_payload)
    version = f"NCAAF_RESULT_FORM_LOGIT_V1_{candidate.dataset_hash[:16]}_{code_sha[:12]}"
    metrics = asdict(candidate.metrics) | {
        "research_screen_pass": candidate.research_screen_pass,
        "split_policy": "CHRONOLOGICAL_60_20_20", "untouched_test": True,
        "historical_reconstruction": True, "archived_pregame_snapshot": False,
        "market_features_used": False,
    }
    client.table("wow_d1_candidate_artifacts").upsert({
        "sport": SPORT, "league": LEAGUE, "market_family": MARKET_FAMILY,
        "model_family": MODEL_FAMILY, "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "source_policy_id": SOURCE_POLICY_ID,
        "training_dataset_hash": candidate.dataset_hash, "training_code_sha": code_sha,
        "artifact_checksum": _hash(artifact_payload), "artifact_payload": artifact_payload,
        "calibrator_payload": calibrator_payload, "validation_metrics": metrics,
        "training_rows": candidate.metrics.train_n, "calibration_rows": candidate.metrics.calibration_n,
        "test_rows": candidate.metrics.test_n, "research_screen_pass": candidate.research_screen_pass,
        "source_review_status": "RECONSTRUCTED_PRIOR_RESULTS_PROVENANCE_READY",
        "lifecycle_state": "CANDIDATE", "promoted": False, "active": False,
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }, on_conflict="model_artifact_version", ignore_duplicates=True).execute()
    return {
        "ok": True, "code": "NCAAF_RESULT_FORM_CANDIDATE_PERSISTED",
        "model_artifact_version": version, "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "eligible_rows": len(rows), "metrics": metrics,
        "research_screen_pass": candidate.research_screen_pass,
        "lifecycle_state": "CANDIDATE", "automatic_certification": False,
        "automatic_promotion": False, "probability_publishable": False, "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "FEATURE_NAMES", "FEATURE_SCHEMA_VERSION", "MODEL_FAMILY",
           "NCAAFResultFormUnavailable", "SOURCE_POLICY_ID", "build_training_rows", "train_and_persist"]
