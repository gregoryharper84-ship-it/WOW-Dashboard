"""ATP/WTA main-tour match-winner research candidates from Valuebetennis.

The public source is CC BY 4.0 and semicolon-delimited. Odds columns are present
in the source but are never read into model features. To keep the first governed
lane route-scoped and operationally bounded, only Main tour, Masters and Grand
Slam singles are eligible; ITF/Challenger rows are excluded rather than silently
sharing authority. Retirements/walkovers are excluded from training outcomes.
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
SPORT = "TENNIS"
MARKET_FAMILY = "OUTRIGHT_WINNER"
BASE_MODEL_FAMILY = "TENNIS_MAIN_TOUR_MATCH_WIN_LOGIT_V1"
FEATURE_SCHEMA_VERSION = "TENNIS_MAIN_TOUR_FORM_SURFACE_PRIOR_V1"
SOURCE_POLICY_ID = "VALUEBETENNIS_CC_BY_4_SETTLED_MAIN_TOUR_SINGLES_V1"
SOURCE_LICENSE = "CC-BY-4.0"
SOURCE_PAGE = "https://www.valuebetennis.com/en/donnees.htm"
BASE_URL = "https://www.valuebetennis.com/datasets"
CSV_DELIMITER = ";"
SEASONS = (2021, 2022, 2023, 2024, 2025, 2026)
TOURS = ("ATP", "WTA")
SUPPORTED_CATEGORIES = frozenset({"MAIN TOUR", "MASTERS", "GRAND SLAM"})
FEATURE_NAMES = (
    "p1_recent_win_rate", "p2_recent_win_rate",
    "p1_surface_win_rate", "p2_surface_win_rate",
    "p1_games_prior_log", "p2_games_prior_log",
    "p1_surface_games_prior_log", "p2_surface_games_prior_log",
    "p1_rest_days_capped", "p2_rest_days_capped",
    "p1_matches_last_14d", "p2_matches_last_14d",
    "p1_h2h_win_rate", "p2_h2h_win_rate",
)


class TennisCandidateUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _hash_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _hash_json(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _ids_hash(ids: list[str]) -> str:
    return sha256("\n".join(ids).encode()).hexdigest()


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        raise TennisCandidateUnavailable("TENNIS_MATCH_DATE_MISSING", "date missing")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.fromisoformat(text[:10] + "T12:00:00+00:00")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _completed_score(score: Any) -> bool:
    text = str(score or "").strip().lower()
    if not text:
        return False
    excluded = ("ret", "abn", "aband", "walk", "w/o", "wo ", "def.", "withdraw")
    return not any(token in text for token in excluded)


def fetch_matches(*, seasons: tuple[int, ...] = SEASONS) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for season in seasons:
        url = f"{BASE_URL}/valuebetennis-matchs-{season}.csv"
        response = requests.get(url, timeout=90)
        if response.status_code != 200:
            raise TennisCandidateUnavailable("TENNIS_VALUEBET_HTTP_FAILED", f"{response.status_code}:{url}")
        digest = _hash_bytes(response.content)
        sources.append({"season": season, "url": url, "sha256": digest, "license": SOURCE_LICENSE})
        text = response.content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text), delimiter=CSV_DELIMITER)
        required = {"match_id", "date", "categorie", "genre", "surface", "joueur1_id", "joueur2_id", "vainqueur_id", "score"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise TennisCandidateUnavailable("TENNIS_VALUEBET_SCHEMA_INVALID", f"season={season}")
        for row in reader:
            match_id = str(row.get("match_id") or "").strip()
            p1 = str(row.get("joueur1_id") or "").strip()
            p2 = str(row.get("joueur2_id") or "").strip()
            winner = str(row.get("vainqueur_id") or "").strip()
            tour = str(row.get("genre") or "").strip().upper()
            category = str(row.get("categorie") or "").strip().upper()
            surface = str(row.get("surface") or "UNKNOWN").strip().upper() or "UNKNOWN"
            if category not in SUPPORTED_CATEGORIES:
                continue
            if not match_id or not p1 or not p2 or winner not in {p1, p2} or tour not in TOURS:
                continue
            if not _completed_score(row.get("score")):
                continue
            matches.append({
                "match_id": match_id, "start": _parse_time(row.get("date")), "tour": tour,
                "category": category, "surface": surface, "p1": p1, "p2": p2, "winner": winner,
                "tournament": str(row.get("tournoi") or ""), "round": str(row.get("tour") or ""),
                "source_url": url, "source_sha256": digest,
            })
    if not matches:
        raise TennisCandidateUnavailable("TENNIS_VALUEBET_EMPTY", "no eligible completed main-tour singles matches")
    return sorted(matches, key=lambda row: (row["start"], row["match_id"])), sources


def _player_summary(history: list[dict[str, Any]], start: datetime, surface: str):
    prior = [row for row in history if row["start"] < start]
    if len(prior) < 5:
        return None
    recent = prior[-20:]
    surface_prior = [row for row in prior if row["surface"] == surface]
    surface_recent = surface_prior[-20:]
    matches_14 = sum(1 for row in prior if (start - row["start"]).total_seconds() <= 14 * 86400)
    prior_ids = [str(row["event_id"]) for row in prior]
    return {
        "recent_win_rate": sum(1 for row in recent if row["won"]) / len(recent),
        "surface_win_rate": (sum(1 for row in surface_recent if row["won"]) / len(surface_recent)) if surface_recent else 0.5,
        "games_prior_log": math.log1p(len(prior)), "surface_games_prior_log": math.log1p(len(surface_prior)),
        "rest_days_capped": max(0.0, min(28.0, (start - prior[-1]["start"]).total_seconds() / 86400.0)),
        "matches_last_14d": float(matches_14), "prior_event_count": len(prior),
        "prior_event_ids_sha256": _ids_hash(prior_ids),
    }


def build_training_rows(matches: list[dict[str, Any]], *, tour: str) -> tuple[list[BinaryTrainingRow], list[dict[str, Any]]]:
    tour = tour.upper()
    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    h2h: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    rows: list[BinaryTrainingRow] = []
    metadata: list[dict[str, Any]] = []
    for match in [row for row in matches if row["tour"] == tour]:
        start, p1, p2, surface = match["start"], match["p1"], match["p2"], match["surface"]
        s1, s2 = _player_summary(histories[p1], start, surface), _player_summary(histories[p2], start, surface)
        pair = tuple(sorted((p1, p2)))
        prior_h2h = h2h[pair]
        h2h_n = int(sum(prior_h2h.values()))
        if s1 and s2:
            p1_h2h = (prior_h2h[p1] / h2h_n) if h2h_n else 0.5
            p2_h2h = (prior_h2h[p2] / h2h_n) if h2h_n else 0.5
            features = {
                "p1_recent_win_rate": s1["recent_win_rate"], "p2_recent_win_rate": s2["recent_win_rate"],
                "p1_surface_win_rate": s1["surface_win_rate"], "p2_surface_win_rate": s2["surface_win_rate"],
                "p1_games_prior_log": s1["games_prior_log"], "p2_games_prior_log": s2["games_prior_log"],
                "p1_surface_games_prior_log": s1["surface_games_prior_log"], "p2_surface_games_prior_log": s2["surface_games_prior_log"],
                "p1_rest_days_capped": s1["rest_days_capped"], "p2_rest_days_capped": s2["rest_days_capped"],
                "p1_matches_last_14d": s1["matches_last_14d"], "p2_matches_last_14d": s2["matches_last_14d"],
                "p1_h2h_win_rate": p1_h2h, "p2_h2h_win_rate": p2_h2h,
            }
            manifest = {
                "policy": SOURCE_POLICY_ID, "license": SOURCE_LICENSE, "source_page": SOURCE_PAGE,
                "source_url": match["source_url"], "source_sha256": match["source_sha256"],
                "match_id": match["match_id"], "tour": tour, "category": match["category"],
                "surface": surface, "tournament": match["tournament"], "round": match["round"],
                "training_settlement_scope": "COMPLETED_MATCHES_ONLY",
                "p1_prior_event_count": s1["prior_event_count"], "p1_prior_event_ids_sha256": s1["prior_event_ids_sha256"],
                "p2_prior_event_count": s2["prior_event_count"], "p2_prior_event_ids_sha256": s2["prior_event_ids_sha256"],
                "h2h_prior_match_count": h2h_n, "market_columns_present_but_used": False,
            }
            manifest_sha = _hash_json(manifest)
            rows.append(BinaryTrainingRow(
                event_id=f"TENNIS:{tour}:{match['match_id']}", event_start_time=start.isoformat(),
                feature_as_of=(start - timedelta(seconds=1)).isoformat(),
                positive_outcome=match["winner"] == p1, features=features,
                source_manifest_sha256=manifest_sha,
            ))
            metadata.append({"source_manifest": manifest})
        histories[p1].append({"event_id": match["match_id"], "start": start, "surface": surface, "won": match["winner"] == p1})
        histories[p2].append({"event_id": match["match_id"], "start": start, "surface": surface, "won": match["winner"] == p2})
        h2h[pair][match["winner"]] += 1
    if len(rows) < 1000:
        raise TennisCandidateUnavailable("TENNIS_CANDIDATE_SAMPLE_INSUFFICIENT", f"tour={tour};rows={len(rows)}")
    return rows, metadata


def _persist_rows(client: Any, tour: str, family: str, rows: list[BinaryTrainingRow], metadata: list[dict[str, Any]]) -> None:
    for offset in range(0, len(rows), 250):
        payloads = []
        for row, meta in zip(rows[offset:offset + 250], metadata[offset:offset + 250]):
            payloads.append({
                "sport": SPORT, "league": tour, "official_event_id": row.event_id,
                "event_start_time": row.event_start_time, "feature_as_of": row.feature_as_of,
                "feature_schema_version": FEATURE_SCHEMA_VERSION, "model_family": family,
                "features": dict(row.features), "outcome_json": {"player1_win": bool(row.positive_outcome)},
                "source_manifest": meta["source_manifest"], "source_manifest_sha256": row.source_manifest_sha256,
                "historical_reconstruction": True, "archived_pregame_snapshot": False,
                "market_features_used": False, "can_execute": False,
            })
        client.table("wow_d1_training_rows").upsert(
            payloads, on_conflict="sport,official_event_id,feature_schema_version,source_manifest_sha256"
        ).execute()


def train_all(client: Any, *, training_code_sha: str) -> dict[str, Any]:
    code_sha = str(training_code_sha or "").strip().lower()
    if len(code_sha) < 7:
        raise TennisCandidateUnavailable("TENNIS_TRAINING_CODE_SHA_REQUIRED", "training code SHA required")
    matches, sources = fetch_matches()
    output: list[dict[str, Any]] = []
    for tour in TOURS:
        rows, metadata = build_training_rows(matches, tour=tour)
        family = f"{BASE_MODEL_FAMILY}_{tour}"
        try:
            candidate = train_binary_candidate(rows, model_family=family, feature_names=FEATURE_NAMES, min_rows=1000)
        except BinaryCandidateError as exc:
            raise TennisCandidateUnavailable(exc.code, str(exc)) from exc
        _persist_rows(client, tour, family, rows, metadata)
        artifact = dict(candidate.artifact_payload)
        version = f"{family}_{candidate.dataset_hash[:16]}_{code_sha[:12]}"
        metrics = asdict(candidate.metrics) | {
            "research_screen_pass": candidate.research_screen_pass, "source_license": SOURCE_LICENSE,
            "market_features_used": False, "training_settlement_scope": "COMPLETED_MATCHES_ONLY",
            "retirement_rows_excluded": True, "surface_feature_required": True,
            "supported_categories": sorted(SUPPORTED_CATEGORIES),
        }
        client.table("wow_d1_candidate_artifacts").upsert({
            "sport": SPORT, "league": tour, "market_family": MARKET_FAMILY,
            "model_family": family, "model_artifact_version": version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION, "source_policy_id": SOURCE_POLICY_ID,
            "training_dataset_hash": candidate.dataset_hash, "training_code_sha": code_sha,
            "artifact_checksum": _hash_json(artifact), "artifact_payload": artifact,
            "calibrator_payload": dict(candidate.calibrator_payload), "validation_metrics": metrics,
            "training_rows": candidate.metrics.train_n, "calibration_rows": candidate.metrics.calibration_n,
            "test_rows": candidate.metrics.test_n, "research_screen_pass": candidate.research_screen_pass,
            "source_review_status": "CC_BY_4_0_PROVENANCE_READY", "lifecycle_state": "CANDIDATE",
            "promoted": False, "active": False, "automatic_certification": False,
            "automatic_promotion": False, "probability_publishable": False, "can_execute": False,
        }, on_conflict="model_artifact_version").execute()
        output.append({
            "tour": tour, "model_artifact_version": version, "eligible_rows": len(rows),
            "metrics": metrics, "research_screen_pass": candidate.research_screen_pass,
            "lifecycle_state": "CANDIDATE", "supported_categories": sorted(SUPPORTED_CATEGORIES),
            "probability_publishable": False, "can_execute": False,
        })
    return {
        "ok": True, "code": "TENNIS_MAIN_TOUR_MATCH_WIN_CANDIDATES_PERSISTED", "source_assets": sources,
        "rows": output, "research_screen_pass_n": sum(bool(row["research_screen_pass"]) for row in output),
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }


__all__ = [
    "BASE_MODEL_FAMILY", "CAN_EXECUTE", "CSV_DELIMITER", "FEATURE_SCHEMA_VERSION", "SOURCE_LICENSE",
    "SOURCE_POLICY_ID", "SUPPORTED_CATEGORIES", "TennisCandidateUnavailable", "TOURS",
    "build_training_rows", "fetch_matches", "train_all",
]
