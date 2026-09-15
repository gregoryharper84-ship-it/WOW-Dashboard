"""NCAAB team/event research candidate using SportsDataverse release assets.

The source repository publishes NCAA men's basketball team box data under
CC BY 4.0. Current-game box statistics are outcomes only; model features use
strictly prior games for each team. No market prices are acquired or used.
Candidates remain inert until deterministic replay/lifecycle review.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import csv
import io
import json
import math
from typing import Any

import requests

from v17.binary_candidate_lifecycle import BinaryCandidateError, BinaryTrainingRow, train_binary_candidate

CAN_EXECUTE = False
SPORT = LEAGUE = "NCAAB"
MARKET_FAMILY = "OUTRIGHT_WINNER"
MODEL_FAMILY = "NCAAB_RESULT_FORM_LOGIT_V1"
FEATURE_SCHEMA_VERSION = "NCAAB_TEAM_FORM_PRIOR_V1"
SOURCE_POLICY_ID = "SPORTSDATAVERSE_NCAAB_TEAM_BOX_CC_BY_4_V1"
SOURCE_LICENSE = "CC-BY-4.0"
RELEASE_TAG = "espn_mens_college_basketball_team_boxscores"
BASE_URL = f"https://github.com/sportsdataverse/sportsdataverse-data/releases/download/{RELEASE_TAG}"
SEASONS = (2022, 2023, 2024, 2025, 2026)
FEATURE_NAMES = (
    "home_recent_win_rate", "away_recent_win_rate",
    "home_recent_point_diff", "away_recent_point_diff",
    "home_recent_points_for", "away_recent_points_for",
    "home_recent_rebound_margin_proxy", "away_recent_rebound_margin_proxy",
    "home_recent_turnovers", "away_recent_turnovers",
    "home_recent_fg_pct", "away_recent_fg_pct",
    "home_recent_3p_pct", "away_recent_3p_pct",
    "home_games_prior_log", "away_games_prior_log",
    "home_rest_days_capped", "away_rest_days_capped",
)


class NCAABCandidateUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or str(value).strip() == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    return str(value or "").strip().lower() in {"true", "t", "1", "yes"}


def _event_time(row: dict[str, Any]) -> datetime:
    text = str(row.get("game_date_time") or "").strip().replace("Z", "+00:00")
    if text:
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    date_text = str(row.get("game_date") or "").strip()
    if not date_text:
        raise NCAABCandidateUnavailable("NCAAB_EVENT_DATE_MISSING", str(row.get("game_id") or ""))
    return datetime.fromisoformat(f"{date_text}T12:00:00+00:00")


def fetch_games(*, seasons: tuple[int, ...] = SEASONS) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sources: list[dict[str, Any]] = []
    for season in seasons:
        url = f"{BASE_URL}/team_box_{season}.csv"
        response = requests.get(url, timeout=60)
        if response.status_code != 200:
            raise NCAABCandidateUnavailable("NCAAB_SPORTSDATAVERSE_HTTP_FAILED", f"{response.status_code}:{url}")
        digest = _digest(response.content)
        sources.append({"season": season, "url": url, "sha256": digest, "license": SOURCE_LICENSE})
        text = response.content.decode("utf-8-sig", errors="replace")
        for row in csv.DictReader(io.StringIO(text)):
            game_id = str(row.get("game_id") or "").strip()
            if game_id:
                grouped[game_id].append({**row, "_source_url": url, "_source_sha256": digest})

    games: list[dict[str, Any]] = []
    for game_id, team_rows in grouped.items():
        home = next((row for row in team_rows if str(row.get("team_home_away") or "").lower() == "home"), None)
        away = next((row for row in team_rows if str(row.get("team_home_away") or "").lower() == "away"), None)
        if not home or not away:
            continue
        if home.get("team_score") in (None, "") or away.get("team_score") in (None, ""):
            continue
        games.append({
            "game_id": game_id, "event_start": _event_time(home), "season": int(_float(home.get("season"))),
            "home_team_id": str(home.get("team_id") or home.get("team_display_name") or "").strip(),
            "away_team_id": str(away.get("team_id") or away.get("team_display_name") or "").strip(),
            "home_score": int(_float(home.get("team_score"))), "away_score": int(_float(away.get("team_score"))),
            "home": home, "away": away, "source_url": home["_source_url"], "source_sha256": home["_source_sha256"],
        })
    if not games:
        raise NCAABCandidateUnavailable("NCAAB_SPORTSDATAVERSE_EMPTY", "no settled games")
    return sorted(games, key=lambda row: (row["event_start"], row["game_id"])), sources


def _history_entry(team: dict[str, Any], opponent: dict[str, Any], *, game_id: str, start: datetime) -> dict[str, Any]:
    team_score, opp_score = _float(team.get("team_score")), _float(opponent.get("team_score"))
    return {
        "event_id": game_id, "start": start, "win": team_score > opp_score,
        "point_diff": team_score - opp_score, "points_for": team_score,
        "rebound_margin_proxy": _float(team.get("total_rebounds")) - _float(opponent.get("total_rebounds")),
        "turnovers": _float(team.get("turnovers") or team.get("total_turnovers")),
        "fg_pct": _float(team.get("field_goal_pct")), "three_pct": _float(team.get("three_point_field_goal_pct")),
    }


def _summary(history: list[dict[str, Any]], start: datetime):
    prior = [row for row in history if row["start"] < start]
    if len(prior) < 5:
        return None
    recent = prior[-10:]
    mean = lambda key: sum(float(row[key]) for row in recent) / len(recent)
    return {
        "win_rate": sum(1 for row in recent if row["win"]) / len(recent),
        "point_diff": mean("point_diff"), "points_for": mean("points_for"),
        "rebound_margin_proxy": mean("rebound_margin_proxy"), "turnovers": mean("turnovers"),
        "fg_pct": mean("fg_pct"), "three_pct": mean("three_pct"),
        "games_prior_log": math.log1p(len(prior)),
        "rest_days_capped": max(0.0, min(21.0, (start - prior[-1]["start"]).total_seconds() / 86400.0)),
        "prior_event_ids": [row["event_id"] for row in prior],
    }


def build_training_rows(games: list[dict[str, Any]]) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]]]:
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows: list[BinaryTrainingRow] = []
    metadata: list[dict[str, Any]] = []
    for game in sorted(games, key=lambda row: (row["event_start"], row["game_id"])):
        start, home_id, away_id = game["event_start"], game["home_team_id"], game["away_team_id"]
        if not home_id or not away_id:
            continue
        hs, aws = _summary(histories[home_id], start), _summary(histories[away_id], start)
        if hs and aws:
            features = {
                "home_recent_win_rate": hs["win_rate"], "away_recent_win_rate": aws["win_rate"],
                "home_recent_point_diff": hs["point_diff"], "away_recent_point_diff": aws["point_diff"],
                "home_recent_points_for": hs["points_for"], "away_recent_points_for": aws["points_for"],
                "home_recent_rebound_margin_proxy": hs["rebound_margin_proxy"], "away_recent_rebound_margin_proxy": aws["rebound_margin_proxy"],
                "home_recent_turnovers": hs["turnovers"], "away_recent_turnovers": aws["turnovers"],
                "home_recent_fg_pct": hs["fg_pct"], "away_recent_fg_pct": aws["fg_pct"],
                "home_recent_3p_pct": hs["three_pct"], "away_recent_3p_pct": aws["three_pct"],
                "home_games_prior_log": hs["games_prior_log"], "away_games_prior_log": aws["games_prior_log"],
                "home_rest_days_capped": hs["rest_days_capped"], "away_rest_days_capped": aws["rest_days_capped"],
            }
            manifest = {
                "policy": SOURCE_POLICY_ID, "license": SOURCE_LICENSE, "source_url": game["source_url"],
                "source_sha256": game["source_sha256"], "game_id": game["game_id"],
                "home_prior_event_ids": hs["prior_event_ids"], "away_prior_event_ids": aws["prior_event_ids"],
                "market_features_used": False,
            }
            manifest_sha = _json_hash(manifest)
            rows.append(BinaryTrainingRow(
                event_id=f"NCAAB:{game['game_id']}", event_start_time=start.isoformat(),
                feature_as_of=(start - timedelta(seconds=1)).isoformat(),
                positive_outcome=game["home_score"] > game["away_score"], features=features,
                source_manifest_sha256=manifest_sha,
            ))
            metadata.append({"source_manifest": manifest})
        histories[home_id].append(_history_entry(game["home"], game["away"], game_id=game["game_id"], start=start))
        histories[away_id].append(_history_entry(game["away"], game["home"], game_id=game["game_id"], start=start))
    if len(rows) < 500:
        raise NCAABCandidateUnavailable("NCAAB_CANDIDATE_SAMPLE_INSUFFICIENT", f"rows={len(rows)}")
    return rows, metadata


def train_and_persist(client: Any, *, training_code_sha: str) -> dict[str, Any]:
    code_sha = str(training_code_sha or "").strip().lower()
    if len(code_sha) < 7:
        raise NCAABCandidateUnavailable("NCAAB_TRAINING_CODE_SHA_REQUIRED", "training code SHA required")
    games, sources = fetch_games()
    rows, metadata = build_training_rows(games)
    try:
        candidate = train_binary_candidate(rows, model_family=MODEL_FAMILY, feature_names=FEATURE_NAMES, min_rows=500)
    except BinaryCandidateError as exc:
        raise NCAABCandidateUnavailable(exc.code, str(exc)) from exc
    payloads = [{
        "sport": SPORT, "league": LEAGUE, "official_event_id": row.event_id,
        "event_start_time": row.event_start_time, "feature_as_of": row.feature_as_of,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "model_family": MODEL_FAMILY,
        "features": dict(row.features), "outcome_json": {"home_win": bool(row.positive_outcome)},
        "source_manifest": meta["source_manifest"], "source_manifest_sha256": row.source_manifest_sha256,
        "historical_reconstruction": True, "archived_pregame_snapshot": False,
        "market_features_used": False, "can_execute": False,
    } for row, meta in zip(rows, metadata)]
    for offset in range(0, len(payloads), 250):
        client.table("wow_d1_training_rows").upsert(
            payloads[offset:offset + 250],
            on_conflict="sport,official_event_id,feature_schema_version,source_manifest_sha256",
        ).execute()
    artifact = dict(candidate.artifact_payload)
    version = f"NCAAB_RESULT_FORM_LOGIT_V1_{candidate.dataset_hash[:16]}_{code_sha[:12]}"
    metrics = asdict(candidate.metrics) | {
        "research_screen_pass": candidate.research_screen_pass, "source_license": SOURCE_LICENSE,
        "market_features_used": False, "untouched_test": True,
    }
    client.table("wow_d1_candidate_artifacts").upsert({
        "sport": SPORT, "league": LEAGUE, "market_family": MARKET_FAMILY,
        "model_family": MODEL_FAMILY, "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION, "source_policy_id": SOURCE_POLICY_ID,
        "training_dataset_hash": candidate.dataset_hash, "training_code_sha": code_sha,
        "artifact_checksum": _json_hash(artifact), "artifact_payload": artifact,
        "calibrator_payload": dict(candidate.calibrator_payload), "validation_metrics": metrics,
        "training_rows": candidate.metrics.train_n, "calibration_rows": candidate.metrics.calibration_n,
        "test_rows": candidate.metrics.test_n, "research_screen_pass": candidate.research_screen_pass,
        "source_review_status": "CC_BY_4_0_PROVENANCE_READY", "lifecycle_state": "CANDIDATE",
        "promoted": False, "active": False, "automatic_certification": False,
        "automatic_promotion": False, "probability_publishable": False, "can_execute": False,
    }, on_conflict="model_artifact_version").execute()
    return {
        "ok": True, "code": "NCAAB_CANDIDATE_PERSISTED", "model_artifact_version": version,
        "eligible_rows": len(rows), "source_assets": sources, "metrics": metrics,
        "research_screen_pass": candidate.research_screen_pass, "lifecycle_state": "CANDIDATE",
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "FEATURE_SCHEMA_VERSION", "MODEL_FAMILY", "NCAABCandidateUnavailable",
           "SOURCE_LICENSE", "SOURCE_POLICY_ID", "build_training_rows", "fetch_games", "train_and_persist"]
