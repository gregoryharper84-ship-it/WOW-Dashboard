"""NCAAB_RESULT_FORM_LOGIT_V1 frozen-candidate replay verifier (Issue #1644).

Research-only, candidate-specific evidence route.  It does NOT extend or
overload the shared TEAM_STATE_DYNAMIC replay verifier.

Given the persisted candidate record (``wow_d1_candidate_artifacts``) and its
frozen training rows (``wow_d1_training_rows``), it deterministically refits the
candidate with the unchanged shared lifecycle and verifies:

* exact candidate identity (sport, model family, feature schema, source policy)
* frozen-row hygiene (no market features, can_execute=false, no temporal
  leakage, unique canonical events, exact feature order, finite numbers)
* exact 64-hex dataset hash
* exact artifact checksum (coefficients, scaler, split indices)
* exact chronological split counts and stored test metrics

Every disagreement is recorded as a typed mismatch.  The receipt is immutable
(frozen dict + content hash).  A PASS receipt never certifies, promotes, or
publishes: ``probability_publishable`` and ``can_execute`` stay False.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from v17.binary_candidate_lifecycle import BinaryCandidateError, BinaryTrainingRow, train_binary_candidate
from v17.ncaab_sportsdataverse_candidate import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    SOURCE_POLICY_ID,
    SPORT,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RECEIPT_VERSION = "NCAAB_ML_FROZEN_REPLAY_RECEIPT_V1"
METRIC_TOLERANCE = 1e-9
_HEX = set("0123456789abcdef")


class NCAABReplayInputError(RuntimeError):
    """Typed failure: replay inputs are missing or structurally unusable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _json_hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and set(text) <= _HEX


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _rows_from_frozen(payloads: Iterable[Mapping[str, Any]], mismatches: list[dict[str, str]]) -> list[BinaryTrainingRow]:
    rows: list[BinaryTrainingRow] = []
    seen: set[str] = set()
    for p in payloads:
        event_id = str(p.get("official_event_id") or "").strip()
        if not event_id.startswith("NCAAB:"):
            mismatches.append({"code": "NCAAB_REPLAY_EVENT_ID_INVALID", "detail": event_id})
            continue
        if event_id in seen:
            mismatches.append({"code": "NCAAB_REPLAY_DUPLICATE_EVENT", "detail": event_id})
            continue
        seen.add(event_id)
        if str(p.get("sport") or "") != SPORT:
            mismatches.append({"code": "NCAAB_REPLAY_WRONG_SPORT", "detail": event_id})
        if p.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
            mismatches.append({"code": "NCAAB_REPLAY_FEATURE_SCHEMA_MISMATCH", "detail": event_id})
        if p.get("model_family") not in (None, MODEL_FAMILY):
            mismatches.append({"code": "NCAAB_REPLAY_MODEL_FAMILY_MISMATCH", "detail": event_id})
        if p.get("market_features_used") is not False:
            mismatches.append({"code": "NCAAB_REPLAY_MARKET_FEATURES_USED", "detail": event_id})
        if p.get("can_execute") is not False:
            mismatches.append({"code": "NCAAB_REPLAY_CAN_EXECUTE_VIOLATION", "detail": event_id})
        start, as_of = str(p.get("event_start_time") or ""), str(p.get("feature_as_of") or "")
        if not start or not as_of or as_of >= start:
            mismatches.append({"code": "NCAAB_REPLAY_TEMPORAL_LEAKAGE", "detail": event_id})
        features = dict(p.get("features") or {})
        if tuple(sorted(features)) != tuple(sorted(FEATURE_NAMES)):
            mismatches.append({"code": "NCAAB_REPLAY_FEATURE_SET_MISMATCH", "detail": event_id})
            continue
        bad = [n for n in FEATURE_NAMES if isinstance(features[n], bool)
               or not isinstance(features[n], (int, float)) or not isfinite(float(features[n]))]
        if bad:
            mismatches.append({"code": "NCAAB_REPLAY_FEATURE_INVALID", "detail": f"{event_id}:{bad[0]}"})
            continue
        outcome = (p.get("outcome_json") or {}).get("home_win")
        if not isinstance(outcome, bool):
            mismatches.append({"code": "NCAAB_REPLAY_OUTCOME_INVALID", "detail": event_id})
            continue
        manifest_sha = str(p.get("source_manifest_sha256") or "")
        if not _is_sha256(manifest_sha):
            mismatches.append({"code": "NCAAB_REPLAY_SOURCE_MANIFEST_HASH_INVALID", "detail": event_id})
            continue
        manifest = p.get("source_manifest")
        if manifest is not None and _json_hash(manifest) != manifest_sha:
            mismatches.append({"code": "NCAAB_REPLAY_SOURCE_MANIFEST_TAMPERED", "detail": event_id})
        rows.append(BinaryTrainingRow(
            event_id=event_id, event_start_time=start, feature_as_of=as_of, positive_outcome=outcome,
            features={n: float(features[n]) for n in FEATURE_NAMES}, source_manifest_sha256=manifest_sha,
        ))
    # Original build order: (event_start, game_id).  UTC isoformat sorts lexically.
    rows.sort(key=lambda r: (r.event_start_time, r.event_id[len("NCAAB:"):]))
    return rows


def verify_frozen_replay(candidate: Mapping[str, Any], frozen_rows: Iterable[Mapping[str, Any]],
                         *, code_sha: str) -> Mapping[str, Any]:
    """Refit the frozen NCAAB candidate and return an immutable typed receipt."""
    sha = str(code_sha or "").strip().lower()
    if len(sha) != 40 or not set(sha) <= _HEX:
        raise NCAABReplayInputError("NCAAB_REPLAY_CODE_SHA_REQUIRED", "exact 40-hex code SHA required")
    if not isinstance(candidate, Mapping) or not candidate:
        raise NCAABReplayInputError("NCAAB_REPLAY_CANDIDATE_MISSING", "candidate record required")
    payloads = list(frozen_rows or [])
    if not payloads:
        raise NCAABReplayInputError("NCAAB_REPLAY_FROZEN_ROWS_MISSING", "frozen training rows required")

    mismatches: list[dict[str, str]] = []
    expect = {"sport": SPORT, "model_family": MODEL_FAMILY,
              "feature_schema_version": FEATURE_SCHEMA_VERSION, "source_policy_id": SOURCE_POLICY_ID}
    for key, value in expect.items():
        if candidate.get(key) != value:
            mismatches.append({"code": "NCAAB_REPLAY_CANDIDATE_IDENTITY_MISMATCH", "detail": key})
    for key in ("promoted", "active", "probability_publishable", "can_execute"):
        if candidate.get(key) is not False:
            mismatches.append({"code": "NCAAB_REPLAY_CANDIDATE_GOVERNANCE_VIOLATION", "detail": key})
    expected_hash = str(candidate.get("training_dataset_hash") or "").lower()
    expected_checksum = str(candidate.get("artifact_checksum") or "").lower()
    if not _is_sha256(expected_hash):
        mismatches.append({"code": "NCAAB_REPLAY_EXPECTED_DATASET_HASH_INVALID", "detail": f"len={len(expected_hash)}"})
    if not _is_sha256(expected_checksum):
        mismatches.append({"code": "NCAAB_REPLAY_EXPECTED_ARTIFACT_CHECKSUM_INVALID", "detail": f"len={len(expected_checksum)}"})

    rows = _rows_from_frozen(payloads, mismatches)
    observed: dict[str, Any] = {"frozen_rows_in": len(payloads), "rows_replayed": len(rows)}
    try:
        refit = train_binary_candidate(rows, model_family=MODEL_FAMILY, feature_names=FEATURE_NAMES, min_rows=500)
    except BinaryCandidateError as exc:
        mismatches.append({"code": "NCAAB_REPLAY_REFIT_FAILED", "detail": exc.code})
        refit = None

    if refit is not None:
        checksum = _json_hash(dict(refit.artifact_payload))
        metrics = asdict(refit.metrics)
        observed.update(dataset_hash=refit.dataset_hash, artifact_checksum=checksum,
                        splits={k: metrics[k] for k in ("train_n", "calibration_n", "test_n")},
                        test_metrics={k: metrics[k] for k in ("calibrated_brier", "ece", "raw_brier", "baseline_brier")},
                        calibrator_method=refit.calibrator_payload.get("method"),
                        test_window=[refit.test_start_event, refit.test_end_event],
                        research_screen_pass=refit.research_screen_pass)
        if refit.dataset_hash != expected_hash:
            mismatches.append({"code": "NCAAB_REPLAY_DATASET_HASH_MISMATCH", "detail": refit.dataset_hash})
        if checksum != expected_checksum:
            mismatches.append({"code": "NCAAB_REPLAY_ARTIFACT_CHECKSUM_MISMATCH", "detail": checksum})
        if candidate.get("artifact_payload") is not None and _json_hash(dict(candidate["artifact_payload"])) != expected_checksum:
            mismatches.append({"code": "NCAAB_REPLAY_STORED_ARTIFACT_TAMPERED", "detail": "artifact_payload"})
        for key, col in (("train_n", "training_rows"), ("calibration_n", "calibration_rows"), ("test_n", "test_rows")):
            if candidate.get(col) is not None and int(candidate[col]) != metrics[key]:
                mismatches.append({"code": "NCAAB_REPLAY_SPLIT_MISMATCH", "detail": f"{col}={candidate[col]}!={metrics[key]}"})
        stored = dict(candidate.get("validation_metrics") or {})
        for key in ("calibrated_brier", "ece"):
            if key not in stored or abs(float(stored[key]) - metrics[key]) > METRIC_TOLERANCE:
                mismatches.append({"code": "NCAAB_REPLAY_METRIC_MISMATCH", "detail": key})

    body = {
        "receipt_version": RECEIPT_VERSION, "issue": 1644, "code_sha": sha,
        "candidate_id": candidate.get("id"), "model_artifact_version": candidate.get("model_artifact_version"),
        "expected": {"dataset_hash": expected_hash, "artifact_checksum": expected_checksum},
        "observed": observed, "mismatches": mismatches,
        "status": "REPLAY_REPRODUCED" if not mismatches else "REPLAY_MISMATCH_HOLD",
        "research_only": True, "certification": False, "promotion": False,
        "probability_publishable": False, "can_execute": False,
    }
    body["receipt_sha256"] = _json_hash(body)
    return _freeze(body)


__all__ = ["CAN_EXECUTE", "NCAABReplayInputError", "PROBABILITY_PUBLISHABLE", "RECEIPT_VERSION", "verify_frozen_replay"]
