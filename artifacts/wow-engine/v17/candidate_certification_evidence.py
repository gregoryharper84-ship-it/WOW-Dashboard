"""Candidate-bound source review and deterministic replay evidence. Evidence only."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isclose
from typing import Any, Mapping

from fastapi import FastAPI
from github_actions_oidc import scout_route_auth_dependency
from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate
from v17.model_source_entitlements import SOURCES, source_readiness

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
VERIFIER_VERSION = "V17_CANDIDATE_BOUND_BINARY_REPLAY_V1"
RECEIPT_TABLE = "wow_d1_certification_evidence_receipts"


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _iso(value: Any) -> str:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _all_rows(db: Any, candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            db.table("wow_d1_training_rows")
            .select("official_event_id,event_start_time,feature_as_of,features,outcome_json,source_manifest,source_manifest_sha256,market_features_used,can_execute")
            .eq("sport", candidate["sport"])
            .eq("league", candidate["league"])
            .eq("model_family", candidate["model_family"])
            .eq("feature_schema_version", candidate["feature_schema_version"])
            .range(offset, offset + 999)
            .execute()
        )
        batch = [dict(row) for row in (getattr(page, "data", None) or [])]
        rows.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    rows.sort(key=lambda row: (_iso(row["event_start_time"]), str(row["official_event_id"])))
    return rows


def _source_id(manifest: Mapping[str, Any]) -> str:
    nested = manifest.get("source_manifest") if isinstance(manifest.get("source_manifest"), Mapping) else manifest
    raw = str((nested or {}).get("source") or (nested or {}).get("provider") or "")
    return raw.split(":", 1)[0].strip().upper()


def _identity(candidate: Mapping[str, Any]) -> dict[str, str]:
    return {
        "candidate_id": str(candidate["candidate_id"]),
        "sport": str(candidate["sport"]).upper(),
        "league": str(candidate["league"]).upper(),
        "model_family": str(candidate["model_family"]),
        "model_artifact_version": str(candidate["model_artifact_version"]),
        "training_dataset_hash": str(candidate["training_dataset_hash"]).lower(),
        "artifact_checksum": str(candidate["artifact_checksum"]).lower(),
    }


def _persist(db: Any, candidate: Mapping[str, Any], source_pass: bool, replay_pass: bool, payload: Mapping[str, Any]) -> dict[str, Any]:
    ident = _identity(candidate)
    evidence_sha = _hash(payload)
    found = (
        db.table(RECEIPT_TABLE).select("*")
        .eq("candidate_id", ident["candidate_id"])
        .eq("model_artifact_version", ident["model_artifact_version"])
        .eq("training_dataset_hash", ident["training_dataset_hash"])
        .eq("artifact_checksum", ident["artifact_checksum"])
        .eq("evidence_sha256", evidence_sha).limit(1).execute()
    )
    data = list(getattr(found, "data", None) or [])
    if data:
        return dict(data[0])
    record = {
        **ident,
        "source_review_status": "PASS" if source_pass else "FAIL",
        "replay_status": "PASS" if replay_pass else "FAIL",
        "verifier_version": VERIFIER_VERSION,
        "verification_payload": dict(payload),
        "evidence_sha256": evidence_sha,
        "probability_publishable": False,
        "can_execute": False,
    }
    result = db.table(RECEIPT_TABLE).insert(record).execute()
    inserted = list(getattr(result, "data", None) or [])
    return dict(inserted[0]) if inserted else record


def verify_candidate_certification_evidence(db: Any, candidate_id: str) -> dict[str, Any]:
    result = db.table("wow_d1_candidate_artifacts").select("*").eq("candidate_id", candidate_id).limit(2).execute()
    candidates = [dict(row) for row in (getattr(result, "data", None) or [])]
    if len(candidates) != 1:
        return {"status": "CERTIFICATION_EVIDENCE_BLOCKED", "code": "CANDIDATE_ARTIFACT_NOT_FOUND", "candidate_id": candidate_id, "probability_publishable": False, "can_execute": False}
    c = candidates[0]
    blockers: list[str] = []
    for flag in ("promoted", "active", "automatic_certification", "automatic_promotion", "probability_publishable"):
        if bool(c.get(flag)):
            blockers.append(f"CANDIDATE_{flag.upper()}_FORBIDDEN")
    if c.get("can_execute") is not False or str(c.get("lifecycle_state") or "").upper() != "CANDIDATE":
        blockers.append("CANDIDATE_INERTNESS_VIOLATION")
    if c.get("research_screen_pass") is not True:
        blockers.append("RESEARCH_SCREEN_FAILED")
    if str(c.get("source_policy_id") or "") != "TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1" or not str(c.get("model_family") or "").endswith("_DYNAMIC_TEAM_STATE_LOGIT_V2"):
        blockers.append("CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED")

    rows = _all_rows(db, c)
    expected = sum(int(c.get(k) or 0) for k in ("training_rows", "calibration_rows", "test_rows"))
    if len(rows) != expected:
        blockers.append("TRAINING_ROW_COUNT_MISMATCH")
    source_ids: set[str] = set()
    feature_names = tuple(str(x) for x in ((c.get("artifact_payload") or {}).get("feature_names") or []))
    replay_rows: list[BinaryTrainingRow] = []
    for row in rows:
        event_id = str(row.get("official_event_id") or "")
        manifest = row.get("source_manifest") or {}
        if not isinstance(manifest, Mapping) or _hash(manifest) != str(row.get("source_manifest_sha256") or "").lower():
            blockers.append("SOURCE_MANIFEST_INVALID")
            continue
        sid = _source_id(manifest)
        source_ids.add(sid)
        entitlement = SOURCES.get(sid)
        readiness = source_readiness(sid)
        if entitlement is None or str(c["sport"]).upper() not in entitlement.sports or not readiness.ready_for_candidate_training:
            blockers.extend(readiness.blockers or ("MODEL_SOURCE_UNCLASSIFIED",))
        if row.get("market_features_used") is not False or row.get("can_execute") is not False:
            blockers.append("TRAINING_ROW_GOVERNANCE_INVALID")
        start, as_of = _iso(row["event_start_time"]), _iso(row["feature_as_of"])
        if as_of >= start:
            blockers.append("BINARY_TEMPORAL_LEAKAGE")
        outcome = row.get("outcome_json") or {}
        try:
            features = {name: float(row["features"][name]) for name in feature_names}
            positive = outcome["home_win"]
            if not isinstance(positive, bool):
                raise TypeError("home_win")
        except (KeyError, TypeError, ValueError):
            blockers.append("TRAINING_ROW_SHAPE_INVALID")
            continue
        replay_rows.append(BinaryTrainingRow(event_id, start, as_of, positive, features, str(row["source_manifest_sha256"]).lower()))

    replay = None
    replay_blockers: list[str] = []
    if not blockers and len(replay_rows) == expected:
        try:
            replay = train_binary_candidate(replay_rows, model_family=str(c["model_family"]), feature_names=feature_names)
        except Exception as exc:
            replay_blockers.append(f"DETERMINISTIC_REPLAY_FAILED:{type(exc).__name__}")
    else:
        replay_blockers.append("DETERMINISTIC_REPLAY_PREREQUISITES_FAILED")

    replay_metrics: dict[str, Any] = {}
    if replay is not None:
        replay_metrics = asdict(replay.metrics)
        if replay.dataset_hash != str(c["training_dataset_hash"]).lower():
            replay_blockers.append("REPLAY_DATASET_HASH_MISMATCH")
        if _hash(replay.artifact_payload) != str(c["artifact_checksum"]).lower():
            replay_blockers.append("REPLAY_ARTIFACT_CHECKSUM_MISMATCH")
        if _hash(replay.calibrator_payload) != _hash(c.get("calibrator_payload") or {}):
            replay_blockers.append("REPLAY_CALIBRATOR_MISMATCH")
        stored_metrics = c.get("validation_metrics") or {}
        for key, value in replay_metrics.items():
            other = stored_metrics.get(key)
            same = int(other) == value if isinstance(value, int) else isclose(float(other), float(value), rel_tol=0.0, abs_tol=1e-12)
            if not same:
                replay_blockers.append(f"REPLAY_METRIC_MISMATCH:{key}")
        expected_version = f"{c['model_family']}_{str(c['training_dataset_hash'])[:16]}_{str(c['training_code_sha'])[:12]}"
        if expected_version != str(c["model_artifact_version"]):
            replay_blockers.append("MODEL_ARTIFACT_VERSION_IDENTITY_MISMATCH")

    source_pass = not blockers
    replay_pass = source_pass and not replay_blockers
    payload = {
        "verifier_version": VERIFIER_VERSION,
        "candidate_identity": _identity(c),
        "source_ids": sorted(source_ids),
        "training_row_count": len(rows),
        "blockers": sorted(set(blockers)),
        "replay_blockers": sorted(set(replay_blockers)),
        "replay_metrics": replay_metrics,
        "probability_publishable": False,
        "can_execute": False,
    }
    receipt = _persist(db, c, source_pass, replay_pass, payload)
    return {
        "status": "CERTIFICATION_EVIDENCE_PASS" if replay_pass else "CERTIFICATION_EVIDENCE_FAILED",
        "candidate_id": str(c["candidate_id"]),
        "source_review_status": receipt.get("source_review_status"),
        "replay_status": receipt.get("replay_status"),
        "receipt_id": receipt.get("receipt_id"),
        "evidence_sha256": receipt.get("evidence_sha256"),
        "blockers": sorted(set(blockers + replay_blockers)),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_candidate_certification_evidence_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any) -> None:
    path = "/internal/v17/team-event-certification-evidence/{candidate_id}"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(path, dependencies=[scout_route_auth_dependency(auth_dependency)], operation_id="verifyWowV17TeamEventCandidateCertificationEvidence")
    def verify(candidate_id: str) -> dict[str, Any]:
        return verify_candidate_certification_evidence(db_client_fn(), candidate_id)


__all__ = ["CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "VERIFIER_VERSION", "install_candidate_certification_evidence_route", "verify_candidate_certification_evidence"]
