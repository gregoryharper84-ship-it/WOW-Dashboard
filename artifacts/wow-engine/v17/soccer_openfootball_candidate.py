"""Scoped soccer 1X2 research candidates from OpenFootball CC0 results.

Each supported competition is fit independently. Only prior settled results are
features; bookmaker prices are never acquired or consumed. The outcome space is
HOME/DRAW/AWAY and therefore uses the governed multiclass lifecycle rather than
a binary shortcut. Artifacts remain CANDIDATE-only pending lifecycle review.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Any

import requests

from v17.d1_candidate_registry import D1RegistryError, persist_candidate
from v17.multiclass_candidate_lifecycle import (
    MulticlassCandidateError,
    MulticlassTrainingRow,
    train_multiclass_candidate,
)

CAN_EXECUTE = False
SPORT = "SOCCER"
MARKET_FAMILY = "OUTRIGHT_WINNER"
MODEL_FAMILY = "SOCCER_1X2_MULTINOMIAL_LOGIT_V1"
FEATURE_SCHEMA_VERSION = "SOCCER_RESULT_FORM_1X2_PRIOR_V1"
SOURCE_POLICY_ID = "OPENFOOTBALL_CC0_RESULTS_V1"
SOURCE_LICENSE = "CC0-1.0"
BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"
SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27")
COMPETITIONS = {
    "EPL": "en.1",
    "BUNDESLIGA": "de.1",
    "LALIGA": "es.1",
    "SERIE_A": "it.1",
    "LIGUE_1": "fr.1",
}
FEATURE_NAMES = (
    "home_recent_points_per_game", "away_recent_points_per_game",
    "home_recent_goal_diff", "away_recent_goal_diff",
    "home_recent_win_rate", "away_recent_win_rate",
    "home_recent_draw_rate", "away_recent_draw_rate",
    "home_games_prior_log", "away_games_prior_log",
    "home_rest_days_capped", "away_rest_days_capped",
)


class SoccerCandidateUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _hash_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _hash_json(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _score(match: dict[str, Any]) -> tuple[int, int] | None:
    score = match.get("score")
    if isinstance(score, dict):
        score = score.get("ft")
    if isinstance(score, list) and len(score) >= 2 and all(isinstance(v, (int, float)) for v in score[:2]):
        return int(score[0]), int(score[1])
    return None


def _event_time(match: dict[str, Any]) -> datetime:
    date_text = str(match.get("date") or "").strip()
    time_text = str(match.get("time") or "12:00").strip() or "12:00"
    if not date_text:
        raise SoccerCandidateUnavailable("SOCCER_EVENT_DATE_MISSING", "match date missing")
    try:
        return datetime.fromisoformat(f"{date_text}T{time_text}:00+00:00").astimezone(timezone.utc)
    except ValueError:
        return datetime.fromisoformat(f"{date_text}T12:00:00+00:00").astimezone(timezone.utc)


def fetch_competition(code: str, *, seasons: tuple[str, ...] = SEASONS) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for season in seasons:
        url = f"{BASE_URL}/{season}/{code}.json"
        response = requests.get(url, timeout=30)
        if response.status_code == 404:
            continue
        if response.status_code != 200:
            raise SoccerCandidateUnavailable("SOCCER_OPENFOOTBALL_HTTP_FAILED", f"{response.status_code}:{url}")
        digest = _hash_bytes(response.content)
        body = response.json()
        season_matches = body.get("matches") if isinstance(body, dict) else None
        if not isinstance(season_matches, list):
            raise SoccerCandidateUnavailable("SOCCER_OPENFOOTBALL_SCHEMA_INVALID", url)
        sources.append({"url": url, "sha256": digest, "season": season, "license": SOURCE_LICENSE})
        for raw in season_matches:
            if not isinstance(raw, dict) or _score(raw) is None:
                continue
            matches.append({**raw, "_season": season, "_source_sha256": digest, "_source_url": url})
    if not matches:
        raise SoccerCandidateUnavailable("SOCCER_OPENFOOTBALL_EMPTY", code)
    return matches, sources


def _summary(history: list[dict[str, Any]], start: datetime):
    prior = [row for row in history if row["start"] < start]
    if len(prior) < 5:
        return None
    recent = prior[-10:]
    return {
        "points_per_game": sum(row["points"] for row in recent) / len(recent),
        "goal_diff": sum(row["goal_diff"] for row in recent) / len(recent),
        "win_rate": sum(1 for row in recent if row["points"] == 3) / len(recent),
        "draw_rate": sum(1 for row in recent if row["points"] == 1) / len(recent),
        "games_prior_log": math.log1p(len(prior)),
        "rest_days_capped": max(0.0, min(28.0, (start - prior[-1]["start"]).total_seconds() / 86400.0)),
        "prior_event_ids": [row["event_id"] for row in prior],
    }


def build_training_rows(matches: list[dict[str, Any]], *, competition: str) -> tuple[list[MulticlassTrainingRow], list[dict[str, Any]]]:
    ordered = sorted(matches, key=lambda row: (_event_time(row), str(row.get("team1") or ""), str(row.get("team2") or "")))
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[MulticlassTrainingRow] = []
    metadata: list[dict[str, Any]] = []
    for match in ordered:
        scored = _score(match)
        if scored is None:
            continue
        home, away = str(match.get("team1") or "").strip(), str(match.get("team2") or "").strip()
        if not home or not away:
            continue
        start = _event_time(match)
        hs, aws = _summary(histories[home], start), _summary(histories[away], start)
        home_goals, away_goals = scored
        event_id = _hash_json({"competition": competition, "date": start.isoformat(), "home": home, "away": away})[:32]
        if hs and aws:
            outcome = "HOME" if home_goals > away_goals else ("AWAY" if away_goals > home_goals else "DRAW")
            features = {
                "home_recent_points_per_game": hs["points_per_game"], "away_recent_points_per_game": aws["points_per_game"],
                "home_recent_goal_diff": hs["goal_diff"], "away_recent_goal_diff": aws["goal_diff"],
                "home_recent_win_rate": hs["win_rate"], "away_recent_win_rate": aws["win_rate"],
                "home_recent_draw_rate": hs["draw_rate"], "away_recent_draw_rate": aws["draw_rate"],
                "home_games_prior_log": hs["games_prior_log"], "away_games_prior_log": aws["games_prior_log"],
                "home_rest_days_capped": hs["rest_days_capped"], "away_rest_days_capped": aws["rest_days_capped"],
            }
            manifest = {
                "policy": SOURCE_POLICY_ID, "license": SOURCE_LICENSE, "competition": competition,
                "source_url": match["_source_url"], "source_sha256": match["_source_sha256"],
                "home_prior_event_ids": hs["prior_event_ids"], "away_prior_event_ids": aws["prior_event_ids"],
                "market_features_used": False, "outcome_space": ["HOME", "DRAW", "AWAY"],
            }
            manifest_sha = _hash_json(manifest)
            rows.append(MulticlassTrainingRow(
                event_id=f"{competition}:{event_id}", event_start_time=start.isoformat(),
                feature_as_of=(start - timedelta(seconds=1)).isoformat(), outcome=outcome,
                features=features, source_manifest_sha256=manifest_sha,
            ))
            metadata.append({"source_manifest": manifest})
        if home_goals == away_goals:
            hp = ap = 1
        elif home_goals > away_goals:
            hp, ap = 3, 0
        else:
            hp, ap = 0, 3
        histories[home].append({"event_id": event_id, "start": start, "points": hp, "goal_diff": home_goals - away_goals})
        histories[away].append({"event_id": event_id, "start": start, "points": ap, "goal_diff": away_goals - home_goals})
    if len(rows) < 500:
        raise SoccerCandidateUnavailable("SOCCER_CANDIDATE_SAMPLE_INSUFFICIENT", f"competition={competition};rows={len(rows)}")
    return rows, metadata


def _persist_rows(client: Any, competition: str, rows: list[MulticlassTrainingRow], metadata: list[dict[str, Any]]) -> None:
    payloads = [{
        "sport": SPORT, "league": competition, "official_event_id": row.event_id,
        "event_start_time": row.event_start_time, "feature_as_of": row.feature_as_of,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "model_family": f"{MODEL_FAMILY}_{competition}",
        "features": dict(row.features), "outcome_json": {"outcome": row.outcome},
        "source_manifest": meta["source_manifest"], "source_manifest_sha256": row.source_manifest_sha256,
        "historical_reconstruction": True, "archived_pregame_snapshot": False,
        "market_features_used": False, "can_execute": False,
    } for row, meta in zip(rows, metadata)]
    for offset in range(0, len(payloads), 250):
        client.table("wow_d1_training_rows").upsert(
            payloads[offset:offset + 250],
            on_conflict="sport,official_event_id,feature_schema_version,source_manifest_sha256",
            ignore_duplicates=True,
        ).execute()


def train_and_persist_competition(client: Any, *, competition: str, code: str, training_code_sha: str) -> dict[str, Any]:
    matches, sources = fetch_competition(code)
    rows, metadata = build_training_rows(matches, competition=competition)
    family = f"{MODEL_FAMILY}_{competition}"
    try:
        candidate = train_multiclass_candidate(
            rows, model_family=family, feature_names=FEATURE_NAMES,
            expected_classes=("HOME", "DRAW", "AWAY"), min_rows=500,
        )
    except MulticlassCandidateError as exc:
        raise SoccerCandidateUnavailable(exc.code, str(exc)) from exc
    _persist_rows(client, competition, rows, metadata)
    artifact = dict(candidate.artifact_payload)
    version = f"{family}_{candidate.dataset_hash[:16]}_{training_code_sha[:12]}"
    checksum = _hash_json(artifact)
    metrics = asdict(candidate.metrics) | {
        "research_screen_pass": candidate.research_screen_pass,
        "source_license": SOURCE_LICENSE,
        "source_provenance_status": "CC0_PUBLIC_DOMAIN_PROVENANCE_READY",
        "market_features_used": False,
        "outcome_space": ["HOME", "DRAW", "AWAY"],
    }
    candidate_row = {
        "sport": SPORT, "league": competition, "market_family": MARKET_FAMILY,
        "model_family": family, "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "source_policy_id": SOURCE_POLICY_ID,
        "training_dataset_hash": candidate.dataset_hash, "training_code_sha": training_code_sha,
        "artifact_checksum": checksum, "artifact_payload": artifact,
        "calibrator_payload": dict(candidate.calibrator_payload), "validation_metrics": metrics,
        "training_rows": candidate.metrics.train_n, "calibration_rows": candidate.metrics.calibration_n,
        "test_rows": candidate.metrics.test_n, "research_screen_pass": candidate.research_screen_pass,
        "source_review_status": "REQUIRED", "lifecycle_state": "CANDIDATE",
        "promoted": False, "active": False, "automatic_certification": False,
        "automatic_promotion": False, "probability_publishable": False, "can_execute": False,
    }
    try:
        registration = persist_candidate(client, candidate_row)
    except D1RegistryError as exc:
        raise SoccerCandidateUnavailable(exc.code, str(exc)) from exc
    return {
        "competition": competition, "model_artifact_version": version,
        "eligible_rows": len(rows), "source_assets": len(sources), "metrics": metrics,
        "research_screen_pass": candidate.research_screen_pass,
        "source_review_status": "REQUIRED", "registry_status": registration.get("status"),
        "lifecycle_state": "CANDIDATE",
        "probability_publishable": False, "can_execute": False,
    }


def train_all(client: Any, *, training_code_sha: str) -> dict[str, Any]:
    code_sha = str(training_code_sha or "").strip().lower()
    if len(code_sha) < 7:
        raise SoccerCandidateUnavailable("SOCCER_TRAINING_CODE_SHA_REQUIRED", "training code SHA required")
    rows = [train_and_persist_competition(client, competition=name, code=code, training_code_sha=code_sha)
            for name, code in COMPETITIONS.items()]
    return {
        "ok": True, "code": "SOCCER_1X2_CANDIDATES_PERSISTED", "rows": rows,
        "research_screen_pass_n": sum(bool(row["research_screen_pass"]) for row in rows),
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "COMPETITIONS", "FEATURE_SCHEMA_VERSION", "MODEL_FAMILY",
           "SOURCE_LICENSE", "SOURCE_POLICY_ID", "SoccerCandidateUnavailable", "build_training_rows",
           "fetch_competition", "train_all", "train_and_persist_competition"]
