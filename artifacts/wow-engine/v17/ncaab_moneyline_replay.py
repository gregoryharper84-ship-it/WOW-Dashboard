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
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import sys
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
RECEIPT_VERSION = "NCAAB_ML_FROZEN_REPLAY_RECEIPT_V2"
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"
ROWS_TABLE = "wow_d1_training_rows"
PAGE_SIZE = 1000
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


def _is_git_sha(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 40 and set(text) <= _HEX


def _dt(value: Any) -> datetime | None:
    """Timezone-aware parse; naive values are treated as UTC. None if unparseable."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


def _rows_from_frozen(payloads: Iterable[Mapping[str, Any]], mismatches: list[dict[str, str]],
                      *, candidate_created_at: datetime | None) -> list[BinaryTrainingRow]:
    rows: list[tuple[datetime, str, BinaryTrainingRow]] = []
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
        start_dt, as_of_dt = _dt(start), _dt(as_of)
        if start_dt is None or as_of_dt is None:
            mismatches.append({"code": "NCAAB_REPLAY_EVENT_TIME_INVALID", "detail": event_id})
            continue
        if as_of_dt >= start_dt:
            mismatches.append({"code": "NCAAB_REPLAY_TEMPORAL_LEAKAGE", "detail": event_id})
        if candidate_created_at is not None:
            row_created = _dt(p.get("created_at"))
            if row_created is None:
                mismatches.append({"code": "NCAAB_REPLAY_ROW_CREATED_AT_MISSING", "detail": event_id})
            elif row_created > candidate_created_at:
                mismatches.append({"code": "NCAAB_REPLAY_ROW_POST_CANDIDATE", "detail": event_id})
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
        if not isinstance(manifest, Mapping) or not manifest:
            mismatches.append({"code": "NCAAB_REPLAY_SOURCE_MANIFEST_MISSING", "detail": event_id})
        elif _json_hash(manifest) != manifest_sha:
            mismatches.append({"code": "NCAAB_REPLAY_SOURCE_MANIFEST_TAMPERED", "detail": event_id})
        rows.append((start_dt, event_id[len("NCAAB:"):], BinaryTrainingRow(
            event_id=event_id, event_start_time=start, feature_as_of=as_of, positive_outcome=outcome,
            features={n: float(features[n]) for n in FEATURE_NAMES}, source_manifest_sha256=manifest_sha,
        )))
    # Original build order: (event_start, game_id), compared as aware datetimes.
    rows.sort(key=lambda r: (r[0], r[1]))
    return [r[2] for r in rows]


def corpus_identity(frozen_rows: Iterable[Mapping[str, Any]]) -> str:
    """Order-independent identity of the queried frozen corpus."""
    return _json_hash(sorted(
        [str(p.get("official_event_id") or ""), str(p.get("source_manifest_sha256") or "")] for p in frozen_rows
    ))


def verify_frozen_replay(candidate: Mapping[str, Any], frozen_rows: Iterable[Mapping[str, Any]],
                         *, expected_candidate_id: str, verifier_sha: str,
                         source_query: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    """Refit the frozen NCAAB candidate and return an immutable typed receipt.

    ``verifier_sha`` is the revision of THIS verifier code; it is recorded
    separately from the candidate's persisted ``training_code_sha`` and is never
    treated as training provenance.
    """
    vsha = str(verifier_sha or "").strip().lower()
    if not _is_git_sha(vsha):
        raise NCAABReplayInputError("NCAAB_REPLAY_VERIFIER_SHA_REQUIRED", "exact 40-hex verifier SHA required")
    want_id = str(expected_candidate_id or "").strip()
    if not want_id:
        raise NCAABReplayInputError("NCAAB_REPLAY_EXPECTED_CANDIDATE_ID_REQUIRED", "expected candidate_id required")
    if not isinstance(candidate, Mapping) or not candidate:
        raise NCAABReplayInputError("NCAAB_REPLAY_CANDIDATE_MISSING", "candidate record required")
    payloads = list(frozen_rows or [])
    if not payloads:
        raise NCAABReplayInputError("NCAAB_REPLAY_FROZEN_ROWS_MISSING", "frozen training rows required")

    mismatches: list[dict[str, str]] = []
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    if not candidate_id:
        mismatches.append({"code": "NCAAB_REPLAY_CANDIDATE_ID_MISSING", "detail": "candidate_id"})
    elif candidate_id != want_id:
        mismatches.append({"code": "NCAAB_REPLAY_CANDIDATE_ID_MISMATCH", "detail": candidate_id})
    training_sha = str(candidate.get("training_code_sha") or "").strip().lower()
    if not _is_git_sha(training_sha):
        mismatches.append({"code": "NCAAB_REPLAY_TRAINING_CODE_SHA_INVALID", "detail": f"len={len(training_sha)}"})
    candidate_created_at = _dt(candidate.get("created_at"))
    if candidate_created_at is None:
        mismatches.append({"code": "NCAAB_REPLAY_CANDIDATE_CREATED_AT_MISSING", "detail": "created_at"})
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

    rows = _rows_from_frozen(payloads, mismatches, candidate_created_at=candidate_created_at)
    observed: dict[str, Any] = {"frozen_rows_in": len(payloads), "rows_replayed": len(rows),
                                "corpus_identity_sha256": corpus_identity(payloads)}
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
                        calibrator_sha256=_json_hash(dict(refit.calibrator_payload)),
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
        "receipt_version": RECEIPT_VERSION, "issue": 1644,
        "verifier_sha": vsha, "candidate_training_code_sha": training_sha,
        "candidate_id": candidate_id or None, "expected_candidate_id": want_id,
        "candidate_created_at": candidate.get("created_at"),
        "model_artifact_version": candidate.get("model_artifact_version"),
        "source_query": dict(source_query or {}),
        "expected": {"dataset_hash": expected_hash, "artifact_checksum": expected_checksum},
        "observed": observed, "mismatches": mismatches,
        "status": "REPLAY_REPRODUCED" if not mismatches else "REPLAY_MISMATCH_HOLD",
        "research_only": True, "certification": False, "promotion": False,
        "probability_publishable": False, "can_execute": False,
    }
    body["receipt_sha256"] = _json_hash(body)
    return _freeze(body)


def load_frozen_inputs(client: Any, candidate_id: str) -> tuple[Mapping[str, Any], list[Mapping[str, Any]], dict[str, Any]]:
    """Read-only load of one candidate and its frozen corpus (select only; no writes)."""
    want = str(candidate_id or "").strip()
    if not want:
        raise NCAABReplayInputError("NCAAB_REPLAY_EXPECTED_CANDIDATE_ID_REQUIRED", "candidate_id required")
    found = client.table(CANDIDATE_TABLE).select("*").eq("candidate_id", want).execute().data or []
    if len(found) != 1:
        raise NCAABReplayInputError("NCAAB_REPLAY_CANDIDATE_MISSING", f"{CANDIDATE_TABLE} rows={len(found)}")
    candidate = found[0]
    query = {"table": ROWS_TABLE, "sport": SPORT, "model_family": MODEL_FAMILY,
             "feature_schema_version": FEATURE_SCHEMA_VERSION, "order": "official_event_id", "page_size": PAGE_SIZE}
    rows: list[Mapping[str, Any]] = []
    offset = 0
    while True:
        page = (client.table(ROWS_TABLE).select("*").eq("sport", SPORT).eq("model_family", MODEL_FAMILY)
                .eq("feature_schema_version", FEATURE_SCHEMA_VERSION).order("official_event_id")
                .range(offset, offset + PAGE_SIZE - 1).execute().data or [])
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    query["rows_returned"] = len(rows)
    return candidate, rows, query


def main(argv: list[str] | None = None) -> int:
    """Read-only CLI: prints the receipt JSON. Exit 0 only on REPLAY_REPRODUCED."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: python -m v17.ncaab_moneyline_replay <candidate_id> <verifier_sha>", file=sys.stderr)
        return 2
    from supabase import create_client
    # This lane accepts a dedicated SELECT-only credential, never the broad service-role key.
    # The provider role must enforce SELECT-only access to the two frozen evidence tables.
    key = os.getenv("NCAAB_REPLAY_READ_ONLY_KEY")
    if not os.getenv("SUPABASE_URL") or not key:
        print(json.dumps({"status": "BLOCKED_WITH_EXACT_REASON", "code": "NCAAB_REPLAY_READ_CREDENTIAL_UNAVAILABLE"}))
        return 3
    try:
        candidate, rows, query = load_frozen_inputs(create_client(os.environ["SUPABASE_URL"], key), args[0])
    except NCAABReplayInputError as exc:
        print(json.dumps({"status": "BLOCKED_WITH_EXACT_REASON", "code": exc.code}))
        return 3
    except Exception as exc:
        # No exception message is emitted: provider errors can contain request metadata.
        print(json.dumps({"status": "BLOCKED_WITH_EXACT_REASON", "code": "NCAAB_REPLAY_SOURCE_READ_FAILED",
                          "error_type": type(exc).__name__}))
        return 3
    receipt = verify_frozen_replay(candidate, rows, expected_candidate_id=args[0], verifier_sha=args[1], source_query=query)
    print(json.dumps(receipt, default=lambda o: dict(o) if isinstance(o, Mapping) else list(o), sort_keys=True))
    return 0 if receipt["status"] == "REPLAY_REPRODUCED" else 1


__all__ = ["CAN_EXECUTE", "NCAABReplayInputError", "PROBABILITY_PUBLISHABLE", "RECEIPT_VERSION",
           "corpus_identity", "load_frozen_inputs", "main", "verify_frozen_replay"]


if __name__ == "__main__":
    raise SystemExit(main())
