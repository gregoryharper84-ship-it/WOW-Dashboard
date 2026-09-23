"""Governed release of immutable D1 team/event candidates.

A research candidate is never mutated or self-promoted.  This module independently
replays its immutable chronological dataset, validates source/provenance policy,
and only then copies the exact fitted artifact into the certified registry and
activates the lane route.  It does not place wagers and can_execute is always
false.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isclose
from typing import Any, Mapping, Sequence

from fastapi import FastAPI

from github_actions_oidc import scout_route_auth_dependency
from basketball_training_replay import verify_training_provenance, verify_training_freshness
from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate
from v17.multiclass_candidate_lifecycle import MulticlassTrainingRow, train_multiclass_candidate

CAN_EXECUTE = False
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"
TRAINING_TABLE = "wow_d1_training_rows"
CERTIFIED_TABLE = "wow_team_event_certified_artifacts"
ROUTE_TABLE = "wow_team_event_certified_routes"
MAX_GENERIC_CORPUS_AGE_DAYS = 400
MIN_CERTIFICATION_TEST_ROWS = 75


class CandidateReleaseBlocked(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _canonical_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _page_rows(query_builder: Any, *, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = query_builder.range(offset, offset + page_size - 1).execute()
        batch = list(response.data or [])
        rows.extend(dict(row) for row in batch)
        if len(batch) < page_size:
            return rows
        offset += page_size


def _candidate_rows(db: Any, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    total = sum(int(candidate.get(name) or 0) for name in ("training_rows", "calibration_rows", "test_rows"))
    if total <= 0:
        raise CandidateReleaseBlocked("CERTIFICATION_PARTITIONS_INVALID", "candidate partition counts are empty")
    query = (
        db.table(TRAINING_TABLE)
        .select("official_event_id,event_start_time,feature_as_of,features,outcome_json,source_manifest,source_manifest_sha256,market_features_used,can_execute")
        .eq("sport", str(candidate["sport"]))
        .eq("league", str(candidate["league"]))
        .eq("model_family", str(candidate["model_family"]))
        .order("event_start_time")
        .order("official_event_id")
    )
    rows = _page_rows(query)
    if len(rows) < total:
        raise CandidateReleaseBlocked("CERTIFICATION_TRAINING_ROWS_MISSING", f"expected={total};actual={len(rows)}")
    selected = rows[:total]
    if any(bool(row.get("market_features_used")) for row in selected):
        raise CandidateReleaseBlocked("CERTIFICATION_MARKET_FEATURES_FORBIDDEN", "training rows use market features")
    if any(row.get("can_execute") is not False for row in selected):
        raise CandidateReleaseBlocked("CERTIFICATION_CAN_EXECUTE_FORBIDDEN", "training row execution authority invalid")
    return selected


def _source_review(db: Any, candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sport = str(candidate.get("sport") or "").upper()
    sources: set[str] = set()
    licenses: set[str] = set()
    bad: list[str] = []
    for row in rows:
        manifest = row.get("source_manifest") if isinstance(row.get("source_manifest"), Mapping) else {}
        if manifest.get("market_features_used") not in (None, False):
            bad.append("MARKET_FEATURES_USED")
            continue
        nested = manifest.get("source_manifest") if isinstance(manifest.get("source_manifest"), Mapping) else manifest
        source = str(nested.get("source") or "").strip()
        url = str(nested.get("url") or "").strip()
        license_id = str(nested.get("license") or "").strip().upper()
        if source:
            sources.add(source)
        if license_id:
            licenses.add(license_id)

        allowed = False
        if source == "OPENFOOTBALL_CC0":
            allowed = True; licenses.add("CC0-1.0")
        elif source == "CFBD:/games":
            allowed = True; licenses.add("CONFIGURED_CREDENTIALLED_PROVIDER")
        elif source in {"wow_nba_training_games", "wow_wnba_training_games"}:
            allowed = sport in {"NBA", "WNBA"}
        elif "sportsdataverse/sportsdataverse-data" in url.lower() and license_id in {"CC-BY-4.0", "CC BY 4.0"}:
            allowed = True; sources.add("SPORTSDATAVERSE_DATA")
        elif "valuebetennis.com" in url.lower() and license_id in {"CC-BY-4.0", "CC BY 4.0"}:
            allowed = True; sources.add("VALUEBETENNIS_CC_BY_4")
        if not allowed:
            bad.append(source or url or "SOURCE_UNIDENTIFIED")

    if sport in {"NBA", "WNBA"}:
        try:
            provenance = verify_training_provenance(db, sport)
            freshness = verify_training_freshness(db, sport)
        except RuntimeError as exc:
            raise CandidateReleaseBlocked("SOURCE_REVIEW_BASKETBALL_PROVENANCE_OR_FRESHNESS_FAILED", str(exc)) from exc
        sources.update(str(v) for v in provenance.get("source_providers", []))
        licenses.add("CC-BY-4.0:SPORTSDATAVERSE_ESPN")
    else:
        latest = max(str(row.get("event_start_time") or "") for row in rows)
        latest_date = datetime.fromisoformat(latest.replace("Z", "+00:00")).date()
        age_days = (datetime.now(timezone.utc).date() - latest_date).days
        freshness = {
            "latest_event_date": latest_date.isoformat(),
            "age_days": age_days,
            "max_age_days": MAX_GENERIC_CORPUS_AGE_DAYS,
            "status": "PASS" if 0 <= age_days <= MAX_GENERIC_CORPUS_AGE_DAYS else "STALE",
        }
        if freshness["status"] != "PASS":
            bad.append("TRAINING_CORPUS_STALE")
        provenance = {"provenance_complete": not bad}

    if bad:
        raise CandidateReleaseBlocked("SOURCE_REVIEW_FAILED", ",".join(sorted(set(bad))[:20]))
    return {
        "status": "PASS",
        "review_version": "TEAM_EVENT_SOURCE_REVIEW_V1",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "rows_reviewed": len(rows),
        "sources": sorted(sources),
        "licenses_or_entitlements": sorted(licenses),
        "market_features_used": False,
        "provenance": provenance,
        "freshness": freshness,
        "can_execute": False,
    }


def _replay_candidate(candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> tuple[Any, dict[str, Any]]:
    artifact = dict(candidate.get("artifact_payload") or {})
    feature_names = tuple(str(v) for v in artifact.get("feature_names") or [])
    if not feature_names:
        raise CandidateReleaseBlocked("CERTIFICATION_FEATURE_SCHEMA_EMPTY", "artifact feature names missing")
    model_family = str(candidate.get("model_family") or "")
    artifact_format = str(artifact.get("artifact_format") or "")

    if artifact_format == "STANDARDIZED_LOGISTIC_JSON_V1":
        replay_rows = [BinaryTrainingRow(
            event_id=str(row["official_event_id"]),
            event_start_time=str(row["event_start_time"]),
            feature_as_of=str(row["feature_as_of"]),
            positive_outcome=bool((row.get("outcome_json") or {}).get("home_win")),
            features={name: float((row.get("features") or {})[name]) for name in feature_names},
            source_manifest_sha256=str(row["source_manifest_sha256"]),
        ) for row in rows]
        replay = train_binary_candidate(replay_rows, model_family=model_family, feature_names=feature_names, min_rows=1)
        replay_metrics = asdict(replay.metrics)
    elif artifact_format == "STANDARDIZED_MULTINOMIAL_LOGISTIC_JSON_V1":
        replay_rows = [MulticlassTrainingRow(
            event_id=str(row["official_event_id"]),
            event_start_time=str(row["event_start_time"]),
            feature_as_of=str(row["feature_as_of"]),
            outcome=str((row.get("outcome_json") or {}).get("outcome") or ""),
            features={name: float((row.get("features") or {})[name]) for name in feature_names},
            source_manifest_sha256=str(row["source_manifest_sha256"]),
        ) for row in rows]
        expected = tuple(str(v) for v in artifact.get("classes") or ("HOME", "DRAW", "AWAY"))
        replay = train_multiclass_candidate(replay_rows, model_family=model_family, feature_names=feature_names, expected_classes=expected, min_rows=1)
        replay_metrics = asdict(replay.metrics)
    else:
        raise CandidateReleaseBlocked("CERTIFICATION_ARTIFACT_FORMAT_UNSUPPORTED", artifact_format)

    candidate_checksum = str(candidate.get("artifact_checksum") or "")
    if _canonical_hash(replay.artifact_payload) != candidate_checksum:
        raise CandidateReleaseBlocked("CERTIFICATION_ARTIFACT_REPLAY_MISMATCH", model_family)
    if replay.dataset_hash != str(candidate.get("training_dataset_hash") or ""):
        raise CandidateReleaseBlocked("CERTIFICATION_DATASET_REPLAY_MISMATCH", model_family)
    if _canonical_hash(replay.calibrator_payload) != _canonical_hash(candidate.get("calibrator_payload") or {}):
        raise CandidateReleaseBlocked("CERTIFICATION_CALIBRATOR_REPLAY_MISMATCH", model_family)

    stored_metrics = dict(candidate.get("validation_metrics") or {})
    for key, value in replay_metrics.items():
        if key not in stored_metrics:
            raise CandidateReleaseBlocked("CERTIFICATION_METRIC_MISSING", key)
        if isinstance(value, (int, float)) and not isclose(float(value), float(stored_metrics[key]), rel_tol=1e-9, abs_tol=1e-10):
            raise CandidateReleaseBlocked("CERTIFICATION_METRIC_REPLAY_MISMATCH", key)

    return replay, {
        "status": "PASS",
        "replay_version": "TEAM_EVENT_IMMUTABLE_HOLDOUT_REPLAY_V1",
        "replayed_at": datetime.now(timezone.utc).isoformat(),
        "dataset_hash": replay.dataset_hash,
        "artifact_checksum": candidate_checksum,
        "calibrator_checksum": _canonical_hash(candidate.get("calibrator_payload") or {}),
        "training_rows": int(candidate.get("training_rows") or 0),
        "calibration_rows": int(candidate.get("calibration_rows") or 0),
        "untouched_test_rows": int(candidate.get("test_rows") or 0),
        "metrics": replay_metrics,
        "market_features_used": False,
        "can_execute": False,
    }


def certify_candidate(db: Any, candidate: Mapping[str, Any], *, certification_code_sha: str) -> dict[str, Any]:
    if candidate.get("research_screen_pass") is not True:
        raise CandidateReleaseBlocked("RESEARCH_SCREEN_FAILED", str(candidate.get("model_artifact_version") or ""))
    if int(candidate.get("test_rows") or 0) < MIN_CERTIFICATION_TEST_ROWS:
        raise CandidateReleaseBlocked("CERTIFICATION_HOLDOUT_SAMPLE_INSUFFICIENT", str(candidate.get("test_rows") or 0))
    if candidate.get("can_execute") is not False or candidate.get("probability_publishable") is not False:
        raise CandidateReleaseBlocked("CANDIDATE_INERTNESS_VIOLATION", "candidate flags invalid")
    if any(bool(candidate.get(name)) for name in ("promoted", "active", "automatic_certification", "automatic_promotion")):
        raise CandidateReleaseBlocked("CANDIDATE_INERTNESS_VIOLATION", "candidate self-promotion detected")

    rows = _candidate_rows(db, candidate)
    source_receipt = _source_review(db, candidate, rows)
    _replay, replay_receipt = _replay_candidate(candidate, rows)
    version = str(candidate["model_artifact_version"])
    certified = {
        "sport": str(candidate["sport"]),
        "league": str(candidate["league"]),
        "market_family": str(candidate.get("market_family") or "OUTRIGHT_WINNER"),
        "model_family": str(candidate["model_family"]),
        "model_artifact_version": version,
        "candidate_id": str(candidate["candidate_id"]),
        "feature_schema_version": str(candidate["feature_schema_version"]),
        "source_policy_id": str(candidate["source_policy_id"]),
        "training_dataset_hash": str(candidate["training_dataset_hash"]),
        "training_code_sha": str(candidate["training_code_sha"]),
        "artifact_checksum": str(candidate["artifact_checksum"]),
        "artifact_payload": dict(candidate["artifact_payload"]),
        "calibrator_payload": dict(candidate.get("calibrator_payload") or {}),
        "validation_metrics": dict(candidate.get("validation_metrics") or {}),
        "source_review_receipt": source_receipt,
        "certification_replay_receipt": replay_receipt,
        "certification_code_sha": certification_code_sha,
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "promoted": True,
        "probability_publishable": True,
        "can_execute": False,
    }
    db.table(CERTIFIED_TABLE).upsert(certified, on_conflict="model_artifact_version", ignore_duplicates=True).execute()
    activation = {
        "sport": certified["sport"], "league": certified["league"], "model_family": certified["model_family"],
        "model_artifact_version": version,
        "activated_at": datetime.now(timezone.utc).isoformat(),
        "activation_receipt": {
            "reason": "SOURCE_REVIEW_PLUS_IMMUTABLE_HOLDOUT_REPLAY_PASS",
            "candidate_id": certified["candidate_id"],
            "certification_code_sha": certification_code_sha,
            "can_execute": False,
        },
        "probability_publishable": True,
        "can_execute": False,
    }
    db.table(ROUTE_TABLE).upsert(activation, on_conflict="sport,league,model_family").execute()
    return {
        "sport": certified["sport"], "league": certified["league"], "model_family": certified["model_family"],
        "model_artifact_version": version, "status": "PROSPECTIVE_CERTIFIED_AND_ROUTED",
        "source_review": source_receipt, "certification_replay": replay_receipt,
        "probability_publishable": True, "can_execute": False,
    }


def run_candidate_release(db: Any, *, certification_code_sha: str, sports: Sequence[str] | None = None) -> dict[str, Any]:
    query = db.table(CANDIDATE_TABLE).select("*").eq("research_screen_pass", True).order("created_at", desc=True).limit(1000)
    candidates = list(query.execute().data or [])
    allowed = {str(v).upper() for v in sports} if sports else None
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in candidates:
        sport = str(row.get("sport") or "").upper()
        if allowed is not None and sport not in allowed:
            continue
        key = (sport, str(row.get("league") or sport).upper(), str(row.get("model_family") or ""))
        if key not in latest:
            latest[key] = dict(row)

    results: list[dict[str, Any]] = []
    for candidate in latest.values():
        try:
            results.append(certify_candidate(db, candidate, certification_code_sha=certification_code_sha))
        except CandidateReleaseBlocked as exc:
            results.append({
                "sport": candidate.get("sport"), "league": candidate.get("league"),
                "model_family": candidate.get("model_family"), "model_artifact_version": candidate.get("model_artifact_version"),
                "status": "BLOCKED", "code": exc.code, "detail": str(exc),
                "probability_publishable": False, "can_execute": False,
            })
    certified_count = sum(row.get("status") == "PROSPECTIVE_CERTIFIED_AND_ROUTED" for row in results)
    return {
        "status": "COMPLETE" if certified_count == len(results) else ("PARTIAL" if certified_count else "BLOCKED"),
        "rows": results, "rows_certified": certified_count, "rows_blocked": len(results) - certified_count,
        "automatic_certification": False, "automatic_promotion": False,
        "can_execute": False,
    }


def install_team_event_candidate_release_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any, code_sha: str) -> None:
    path = "/internal/v17/team-event-candidate-release"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(path, dependencies=[scout_route_auth_dependency(auth_dependency)], operation_id="runWowV17TeamEventCandidateRelease")
    def release() -> dict[str, Any]:
        return run_candidate_release(db_client_fn(), certification_code_sha=code_sha)


__all__ = [
    "CAN_EXECUTE", "CandidateReleaseBlocked", "certify_candidate",
    "install_team_event_candidate_release_route", "run_candidate_release",
]
