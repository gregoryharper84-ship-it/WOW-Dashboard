"""NHL regular-season candidate-bound certification evidence (Fix C).

Narrow verifier for NHL_PUBLIC_WEB_API / NHL_REGULAR_SEASON_LOGISTIC_V1.
This is not publication, promotion, Independent QA, SIRT, or Release authority.
Engineering cannot mint independent source-review PASS. can_execute stays false.
"""
from __future__ import annotations

from dataclasses import asdict
from math import isclose, isfinite
from typing import Any, Mapping

from v17.candidate_certification_evidence import _all_rows, _hash, _identity, _iso
from v17.model_source_entitlements import source_readiness

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
VERIFIER_VERSION = "V17_NHL_REGULAR_SEASON_CANDIDATE_REPLAY_V1"
RECEIPT_TABLE = "wow_d1_certification_evidence_receipts"
SOURCE_ID = "NHL_PUBLIC_WEB_API"
SOURCE_POLICY = "NHL_PUBLIC_WEB_API"
MODEL_FAMILY = "NHL_REGULAR_SEASON_LOGISTIC_V1"
MODEL_ARTIFACT_VERSION_PREFIX = "NHL_RS_LOGISTIC_V1"
FEATURE_SCHEMA = "NHL_REGULAR_SEASON_FEATURES_V1"
REQUIRED_TRAINING_ROWS = 3676
REQUIRED_CALIBRATION_ROWS = 1226
REQUIRED_TEST_ROWS = 1226
REQUIRED_CORPUS_ROWS = REQUIRED_TRAINING_ROWS + REQUIRED_CALIBRATION_ROWS + REQUIRED_TEST_ROWS
IDENTITY_CALIBRATION_METHOD = "IDENTITY_RAW_PROBABILITY_V1"


def expected_model_artifact_version(training_dataset_hash: str, training_code_sha: str) -> str:
    """Match nhl_candidate_pipeline.candidate_record version convention exactly."""
    dataset = str(training_dataset_hash or "").strip().lower()
    code = str(training_code_sha or "").strip().lower()
    return f"{MODEL_ARTIFACT_VERSION_PREFIX}-{dataset[:16]}-{code[:12]}"


def _validated_row(
    row: Mapping[str, Any], feature_names: tuple[str, ...]
) -> tuple[Any | None, list[str]]:
    from v17.binary_candidate_lifecycle import BinaryTrainingRow

    blockers: list[str] = []
    manifest = row.get("source_manifest")
    if (
        not isinstance(manifest, Mapping)
        or _hash(manifest) != str(row.get("source_manifest_sha256") or "").lower()
    ):
        return None, ["SOURCE_MANIFEST_INVALID"]
    nested = manifest.get("source_manifest") if isinstance(manifest.get("source_manifest"), Mapping) else manifest
    source_token = str(
        (nested or {}).get("source")
        or (nested or {}).get("source_id")
        or (nested or {}).get("provider")
        or (nested or {}).get("policy")
        or ""
    ).split(":", 1)[0].strip().upper()
    if source_token not in {SOURCE_ID, SOURCE_POLICY}:
        blockers.append("SOURCE_MANIFEST_INVALID")
    if row.get("market_features_used") is not False or row.get("can_execute") is not False:
        blockers.append("TRAINING_ROW_GOVERNANCE_INVALID")
    try:
        start, as_of = _iso(row["event_start_time"]), _iso(row["feature_as_of"])
        if as_of >= start:
            blockers.append("BINARY_TEMPORAL_LEAKAGE")
        outcome = row.get("outcome_json") or {}
        # NHL persistence writes {"positive_outcome": bool} via d1_bulk_candidate_registry.
        # Do not accept the generic home_win key as a silent alias.
        if "positive_outcome" not in outcome:
            raise KeyError("positive_outcome")
        positive = outcome["positive_outcome"]
        if not isinstance(positive, bool):
            raise TypeError("positive_outcome")
        features = {}
        for name in feature_names:
            value = row["features"][name]
            if isinstance(value, bool):
                raise TypeError("boolean feature")
            number = float(value)
            if not isfinite(number):
                raise ValueError("nonfinite")
            features[name] = number
        event_id = str(row.get("official_event_id") or "").strip()
        if not event_id:
            raise ValueError("missing event")
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None, sorted(set(blockers + ["TRAINING_ROW_SHAPE_INVALID"]))
    if blockers:
        return None, sorted(set(blockers))
    return (
        BinaryTrainingRow(
            event_id=event_id,
            event_start_time=start,
            feature_as_of=as_of,
            positive_outcome=positive,
            features=features,
            source_manifest_sha256=str(row["source_manifest_sha256"]).lower(),
        ),
        [],
    )


def _persist(
    db: Any,
    candidate: Mapping[str, Any],
    source_pass: bool,
    replay_pass: bool,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _identity(candidate)
    digest = _hash(payload)
    match = (
        db.table(RECEIPT_TABLE)
        .select("*")
        .eq("candidate_id", identity["candidate_id"])
        .eq("model_artifact_version", identity["model_artifact_version"])
        .eq("training_dataset_hash", identity["training_dataset_hash"])
        .eq("artifact_checksum", identity["artifact_checksum"])
        .eq("evidence_sha256", digest)
        .limit(1)
        .execute()
    )
    old = list(getattr(match, "data", None) or [])
    if old:
        return dict(old[0])
    record = {
        **identity,
        "source_review_status": "PASS" if source_pass else "FAIL",
        "replay_status": "PASS" if replay_pass else "FAIL",
        "verifier_version": VERIFIER_VERSION,
        "verification_payload": dict(payload),
        "evidence_sha256": digest,
        "probability_publishable": False,
        "can_execute": False,
    }
    result = db.table(RECEIPT_TABLE).insert(record).execute()
    data = list(getattr(result, "data", None) or [])
    return dict(data[0]) if data else record


def verify_nhl_candidate_certification_evidence(db: Any, candidate_id: str) -> dict[str, Any]:
    """Replay one NHL candidate; never publish or execute."""
    from v17.binary_candidate_lifecycle import train_binary_candidate

    found = (
        db.table("wow_d1_candidate_artifacts")
        .select("*")
        .eq("candidate_id", candidate_id)
        .limit(2)
        .execute()
    )
    candidates = [dict(row) for row in (getattr(found, "data", None) or [])]
    if len(candidates) != 1:
        return {
            "status": "CERTIFICATION_EVIDENCE_BLOCKED",
            "code": "CANDIDATE_ARTIFACT_NOT_FOUND",
            "candidate_id": candidate_id,
            "probability_publishable": False,
            "can_execute": False,
        }
    c = candidates[0]
    blockers: list[str] = []
    for flag in (
        "promoted",
        "active",
        "automatic_certification",
        "automatic_promotion",
        "probability_publishable",
    ):
        if bool(c.get(flag)):
            blockers.append(f"CANDIDATE_{flag.upper()}_FORBIDDEN")
    if c.get("can_execute") is not False or str(c.get("lifecycle_state") or "").upper() != "CANDIDATE":
        blockers.append("CANDIDATE_INERTNESS_VIOLATION")
    if c.get("research_screen_pass") is not True:
        blockers.append("RESEARCH_SCREEN_FAILED")

    family = str(c.get("model_family") or "")
    if (
        str(c.get("sport") or "").upper() != "NHL"
        or family != MODEL_FAMILY
        or str(c.get("source_policy_id") or "") != SOURCE_POLICY
        or str(c.get("feature_schema_version") or "") != FEATURE_SCHEMA
    ):
        blockers.append("CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED")

    train_n = int(c.get("training_rows") or 0)
    calib_n = int(c.get("calibration_rows") or 0)
    test_n = int(c.get("test_rows") or 0)
    if (
        train_n != REQUIRED_TRAINING_ROWS
        or calib_n != REQUIRED_CALIBRATION_ROWS
        or test_n != REQUIRED_TEST_ROWS
    ):
        blockers.append("NHL_HISTORICAL_TARGET_SPLIT_MISMATCH")

    calibrator = c.get("calibrator_payload") if isinstance(c.get("calibrator_payload"), Mapping) else {}
    if str(calibrator.get("method") or "") == IDENTITY_CALIBRATION_METHOD:
        blockers.append("NHL_IDENTITY_CALIBRATOR_FORBIDDEN")

    readiness = source_readiness(SOURCE_ID)
    if not readiness.ready_for_candidate_training:
        blockers.extend(readiness.blockers or ("MODEL_SOURCE_UNCLASSIFIED",))

    artifact = c.get("artifact_payload") if isinstance(c.get("artifact_payload"), Mapping) else {}
    feature_names = artifact.get("feature_names") if isinstance(artifact, Mapping) else None
    if (
        not isinstance(feature_names, list)
        or not feature_names
        or any(not isinstance(name, str) or not name for name in feature_names)
        or len(set(feature_names)) != len(feature_names)
    ):
        blockers.append("CANDIDATE_REPLAY_CONTRACT_UNSUPPORTED")
        feature_names = []

    try:
        rows = _all_rows(db, c)
    except (ValueError, KeyError, TypeError) as exc:
        blockers.append(str(exc) if str(exc) else "TRAINING_ROW_SHAPE_INVALID")
        rows = []
    expected = train_n + calib_n + test_n
    if len(rows) != expected or expected != REQUIRED_CORPUS_ROWS:
        blockers.append("TRAINING_ROW_COUNT_MISMATCH")

    replay_rows = []
    for row in rows:
        item, row_blockers = _validated_row(row, tuple(feature_names))
        blockers.extend(row_blockers)
        if item is not None:
            replay_rows.append(item)

    replay_blockers: list[str] = []
    metrics: dict[str, Any] = {}
    if not blockers and len(replay_rows) == expected:
        try:
            fitted = train_binary_candidate(
                replay_rows,
                model_family=family,
                feature_names=feature_names,
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
                    other = recorded[key]
                    same = (
                        int(other) == value
                        if isinstance(value, int)
                        else isclose(float(other), float(value), rel_tol=0.0, abs_tol=1e-12)
                    )
                    if not same:
                        replay_blockers.append(f"REPLAY_METRIC_MISMATCH:{key}")
                except (KeyError, TypeError, ValueError):
                    replay_blockers.append(f"REPLAY_METRIC_MISMATCH:{key}")
            version = expected_model_artifact_version(
                str(c.get("training_dataset_hash") or ""),
                str(c.get("training_code_sha") or ""),
            )
            if version != str(c.get("model_artifact_version") or ""):
                replay_blockers.append("MODEL_ARTIFACT_VERSION_IDENTITY_MISMATCH")
        except Exception as exc:  # research verifier: never bypass invalid training
            replay_blockers.append("DETERMINISTIC_REPLAY_FAILED:" + type(exc).__name__)
    else:
        replay_blockers.append("DETERMINISTIC_REPLAY_PREREQUISITES_FAILED")

    governed_review = str(c.get("source_review_status") or "REQUIRED").strip().upper()
    if governed_review != "PASS":
        blockers.append("SOURCE_REVIEW_FAILED" if governed_review == "FAIL" else "SOURCE_REVIEW_REQUIRED")

    source_integrity = not any(
        b
        for b in blockers
        if b
        not in {
            "SOURCE_REVIEW_REQUIRED",
            "SOURCE_REVIEW_FAILED",
            "NHL_IDENTITY_CALIBRATOR_FORBIDDEN",
        }
    )
    # Source review and non-identity calibrator are mandatory for overall PASS.
    source_pass = source_integrity and governed_review == "PASS" and "NHL_IDENTITY_CALIBRATOR_FORBIDDEN" not in blockers
    replay_pass = (
        source_integrity
        and "NHL_IDENTITY_CALIBRATOR_FORBIDDEN" not in blockers
        and not replay_blockers
        and "DETERMINISTIC_REPLAY_PREREQUISITES_FAILED" not in replay_blockers
    )
    overall_pass = source_pass and replay_pass
    payload = {
        "verifier_version": VERIFIER_VERSION,
        "candidate_identity": _identity(c),
        "source_ids": [SOURCE_ID],
        "training_row_count": len(rows),
        "required_split": {
            "training_rows": REQUIRED_TRAINING_ROWS,
            "calibration_rows": REQUIRED_CALIBRATION_ROWS,
            "test_rows": REQUIRED_TEST_ROWS,
        },
        "blockers": sorted(set(blockers)),
        "replay_blockers": sorted(set(replay_blockers)),
        "replay_metrics": metrics,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    receipt = _persist(db, c, source_pass, replay_pass, payload)
    return {
        "status": "CERTIFICATION_EVIDENCE_PASS" if overall_pass else "CERTIFICATION_EVIDENCE_FAILED",
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


__all__ = [
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "VERIFIER_VERSION",
    "SOURCE_ID",
    "SOURCE_POLICY",
    "MODEL_FAMILY",
    "MODEL_ARTIFACT_VERSION_PREFIX",
    "FEATURE_SCHEMA",
    "REQUIRED_TRAINING_ROWS",
    "REQUIRED_CALIBRATION_ROWS",
    "REQUIRED_TEST_ROWS",
    "IDENTITY_CALIBRATION_METHOD",
    "expected_model_artifact_version",
    "_validated_row",
    "verify_nhl_candidate_certification_evidence",
]
