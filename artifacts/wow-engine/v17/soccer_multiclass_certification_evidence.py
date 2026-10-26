"""Candidate-bound deterministic certification evidence for soccer 1X2 research.

This is not a publication or promotion path. A PASS means only that the
recorded OpenFootball candidate has reproducible provenance, training inputs,
artifact, calibrator and test metrics. Current-match XI hydration, predictive
bounds, independent review and terminal admission remain separate gates.
"""
from __future__ import annotations

from dataclasses import asdict
from math import isclose, isfinite
from typing import Any, Mapping

from v17.candidate_certification_evidence import _all_rows, _hash, _identity, _iso
from v17.multiclass_candidate_lifecycle import MulticlassTrainingRow, train_multiclass_candidate
from v17.model_source_entitlements import source_readiness

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
VERIFIER_VERSION = "V17_SOCCER_MULTICLASS_CANDIDATE_REPLAY_V1"
RECEIPT_TABLE = "wow_d1_certification_evidence_receipts"
SOURCE_ID = "OPENFOOTBALL_CC0"
SOURCE_POLICY = "OPENFOOTBALL_CC0_RESULTS_V1"
FEATURE_SCHEMA = "SOCCER_RESULT_FORM_1X2_PRIOR_V1"
CLASSES = frozenset(("HOME", "DRAW", "AWAY"))
SOURCE_URL_PREFIX = "https://raw.githubusercontent.com/openfootball/football.json/master/"


def _hex64(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def _validated_row(
    row: Mapping[str, Any], candidate: Mapping[str, Any], feature_names: tuple[str, ...]
) -> tuple[MulticlassTrainingRow | None, list[str]]:
    blockers: list[str] = []
    manifest = row.get("source_manifest")
    if not isinstance(manifest, dict) or not _hex64(row.get("source_manifest_sha256")) or _hash(manifest) != str(row["source_manifest_sha256"]).lower():
        return None, ["SOURCE_MANIFEST_INVALID"]
    source_ok = (
        manifest.get("policy") == SOURCE_POLICY
        and manifest.get("license") == "CC0-1.0"
        and manifest.get("competition") == candidate.get("league")
        and isinstance(manifest.get("source_url"), str)
        and manifest["source_url"].startswith(SOURCE_URL_PREFIX)
        and _hex64(manifest.get("source_sha256"))
        and manifest.get("market_features_used") is False
        and manifest.get("outcome_space") == ["HOME", "DRAW", "AWAY"]
    )
    if not source_ok:
        blockers.append("SOURCE_MANIFEST_INVALID")
    if row.get("market_features_used") is not False or row.get("can_execute") is not False:
        blockers.append("TRAINING_ROW_GOVERNANCE_INVALID")
    try:
        start, as_of = _iso(row["event_start_time"]), _iso(row["feature_as_of"])
        if as_of >= start:
            blockers.append("MULTICLASS_TEMPORAL_LEAKAGE")
        outcome = row["outcome_json"]["outcome"]
        if not isinstance(outcome, str) or outcome not in CLASSES:
            raise ValueError("invalid 1X2 outcome")
        values = {}
        for name in feature_names:
            value = row["features"][name]
            if isinstance(value, bool):
                raise ValueError("boolean feature")
            number = float(value)
            if not isfinite(number):
                raise ValueError("nonfinite feature")
            values[name] = number
        event_id = str(row["official_event_id"]).strip()
        if not event_id or not event_id.startswith(str(candidate["league"]) + ":"):
            raise ValueError("invalid competition event identity")
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None, sorted(set(blockers + ["TRAINING_ROW_SHAPE_INVALID"]))
    return MulticlassTrainingRow(
        event_id=event_id, event_start_time=start, feature_as_of=as_of,
        outcome=outcome, features=values,
        source_manifest_sha256=str(row["source_manifest_sha256"]).lower(),
    ), sorted(set(blockers))


def _persist(db: Any, candidate: Mapping[str, Any], source_pass: bool, replay_pass: bool,
             payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity(candidate)
    digest = _hash(payload)
    match = (db.table(RECEIPT_TABLE).select("*")
             .eq("candidate_id", identity["candidate_id"])
             .eq("model_artifact_version", identity["model_artifact_version"])
             .eq("training_dataset_hash", identity["training_dataset_hash"])
             .eq("artifact_checksum", identity["artifact_checksum"])
             .eq("evidence_sha256", digest).limit(1).execute())
    old = list(getattr(match, "data", None) or [])
    if old:
        return dict(old[0])
    record = {
        **identity, "source_review_status": "PASS" if source_pass else "FAIL",
        "replay_status": "PASS" if replay_pass else "FAIL",
        "verifier_version": VERIFIER_VERSION,
        "verification_payload": dict(payload), "evidence_sha256": digest,
        "probability_publishable": False, "can_execute": False,
    }
    result = db.table(RECEIPT_TABLE).insert(record).execute()
    data = list(getattr(result, "data", None) or [])
    return dict(data[0]) if data else record


def verify_soccer_candidate_certification_evidence(db: Any, candidate_id: str) -> dict[str, Any]:
    found = (db.table("wow_d1_candidate_artifacts").select("*")
             .eq("candidate_id", candidate_id).limit(2).execute())
    candidates = [dict(row) for row in (getattr(found, "data", None) or [])]
    if len(candidates) != 1:
        return {"status": "CERTIFICATION_EVIDENCE_BLOCKED",
                "code": "CANDIDATE_ARTIFACT_NOT_FOUND", "candidate_id": candidate_id,
                "probability_publishable": False, "can_execute": False}
    c = candidates[0]
    blockers: list[str] = []
    for flag in ("promoted", "active", "automatic_certification",
                 "automatic_promotion", "probability_publishable"):
        if bool(c.get(flag)):
            blockers.append(f"CANDIDATE_{flag.upper()}_FORBIDDEN")
    if c.get("can_execute") is not False or c.get("lifecycle_state") != "CANDIDATE":
        blockers.append("CANDIDATE_INERTNESS_VIOLATION")
    if c.get("research_screen_pass") is not True:
        blockers.append("RESEARCH_SCREEN_FAILED")
    family = str(c.get("model_family") or "")
    if (c.get("sport") != "SOCCER"
            or not family.startswith("SOCCER_1X2_MULTINOMIAL_LOGIT_V1_")
            or family != "SOCCER_1X2_MULTINOMIAL_LOGIT_V1_" + str(c.get("league"))
            or c.get("source_policy_id") != SOURCE_POLICY
            or c.get("feature_schema_version") != FEATURE_SCHEMA):
        blockers.append("CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED")
    if not source_readiness(SOURCE_ID).ready_for_candidate_training:
        blockers.append("MODEL_SOURCE_UNCLASSIFIED")

    artifact = c.get("artifact_payload") or {}
    classes = artifact.get("classes") if isinstance(artifact, dict) else None
    feature_names = artifact.get("feature_names") if isinstance(artifact, dict) else None
    if (not isinstance(classes, list) or set(classes) != CLASSES or len(classes) != 3
            or not isinstance(feature_names, list) or not feature_names
            or any(not isinstance(name, str) or not name for name in feature_names)
            or len(set(feature_names)) != len(feature_names)):
        blockers.append("CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED")
        feature_names = []

    try:
        rows = _all_rows(db, c)
    except (ValueError, KeyError, TypeError) as exc:
        blockers.append(str(exc) if str(exc) else "TRAINING_ROW_SHAPE_INVALID")
        rows = []
    expected = sum(int(c.get(key) or 0) for key in ("training_rows", "calibration_rows", "test_rows"))
    if len(rows) != expected or expected < 500:
        blockers.append("TRAINING_ROW_COUNT_MISMATCH")

    replay_rows: list[MulticlassTrainingRow] = []
    for row in rows:
        item, row_blockers = _validated_row(row, c, tuple(feature_names))
        blockers.extend(row_blockers)
        if item is not None:
            replay_rows.append(item)
    replay_blockers: list[str] = []
    metrics: dict[str, Any] = {}
    if not blockers and len(replay_rows) == expected:
        try:
            fitted = train_multiclass_candidate(
                replay_rows, model_family=family, feature_names=feature_names,
                expected_classes=("HOME", "DRAW", "AWAY"), min_rows=500,
            )
            metrics = asdict(fitted.metrics)
            if fitted.dataset_hash != str(c.get("training_dataset_hash") or "").lower():
                replay_blockers.append("REPLAY_DATASET_HASH_MISMATCH")
            if _hash(fitted.artifact_payload) != str(c.get("artifact_checksum") or "").lower():
                replay_blockers.append("REPLAY_ARTIFACT_CHECKSUM_MISMATCH")
            if _hash(fitted.calibrator_payload) != _hash(c.get("calibrator_payload") or {}):
                replay_blockers.append("REPLAY_CALIBRATOR_MISMATCH")
            recorded = c.get("validation_metrics") or {}
            for key, value in metrics.items():
                try:
                    if not isclose(float(recorded[key]), float(value), rel_tol=0.0, abs_tol=1e-12):
                        replay_blockers.append("REPLAY_METRIC_MISMATCH:" + key)
                except (KeyError, TypeError, ValueError):
                    replay_blockers.append("REPLAY_METRIC_MISMATCH:" + key)
            version = f"{family}_{str(c.get('training_dataset_hash') or '')[:16]}_{str(c.get('training_code_sha') or '')[:12]}"
            if version != c.get("model_artifact_version"):
                replay_blockers.append("MODEL_ARTIFACT_VERSION_IDENTITY_MISMATCH")
        except Exception as exc:  # research verifier: never bypass invalid training
            replay_blockers.append("DETERMINISTIC_REPLAY_FAILED:" + type(exc).__name__)
    else:
        replay_blockers.append("DETERMINISTIC_REPLAY_PREREQUISITES_FAILED")
    source_pass = not blockers
    replay_pass = source_pass and not replay_blockers
    payload = {
        "verifier_version": VERIFIER_VERSION, "candidate_identity": _identity(c),
        "source_ids": [SOURCE_ID], "training_row_count": len(rows),
        "blockers": sorted(set(blockers)), "replay_blockers": sorted(set(replay_blockers)),
        "replay_metrics": metrics, "outcome_space": ["HOME", "DRAW", "AWAY"],
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }
    receipt = _persist(db, c, source_pass, replay_pass, payload)
    return {
        "status": "CERTIFICATION_EVIDENCE_PASS" if replay_pass else "CERTIFICATION_EVIDENCE_FAILED",
        "candidate_id": str(c["candidate_id"]), "source_review_status": receipt.get("source_review_status"),
        "replay_status": receipt.get("replay_status"), "receipt_id": receipt.get("receipt_id"),
        "evidence_sha256": receipt.get("evidence_sha256"),
        "blockers": sorted(set(blockers + replay_blockers)),
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "PROBABILITY_PUBLISHABLE",
           "VERIFIER_VERSION", "_validated_row", "verify_soccer_candidate_certification_evidence"]
