"""NCAAF result/form candidate-bound deterministic replay -- research evidence only.

Reads a *frozen* candidate and its prior training rows without mutating the DB.
A perfect numerical replay is not independent pregame source attestation:
result/form features were reconstructed retrospectively; certification remains HOLD.

No scoring endpoint, model publication, betting execution, or persistence.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isclose, isfinite
from typing import Any, Callable, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
VERIFIER_VERSION = "WOW_NCAAF_RESULT_FORM_REPLAY_RESEARCH_V1"
MODEL_FAMILY = "NCAAF_RESULT_FORM_LOGIT_V1"
FEATURE_SCHEMA = "NCAAF_RESULT_FORM_PRIOR_V1"
SOURCE_POLICY = "NCAAF_SETTLED_PRIOR_RESULTS_RECONSTRUCTION_V1"
FEATURE_NAMES = (
    "home_win_rate_prior", "away_win_rate_prior", "home_point_diff_prior",
    "away_point_diff_prior", "home_games_prior_log", "away_games_prior_log",
    "home_rest_days_capped", "away_rest_days_capped", "neutral_site",
)
SOURCE_HOLD = "NCAAF_RECONSTRUCTED_HISTORY_NOT_ARCHIVED_PREGAME"


class ReplayAuditError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _iso(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReplayAuditError("NCAAF_REPLAY_TIMESTAMP_INVALID") from exc
    if parsed.utcoffset() is None:
        raise ReplayAuditError("NCAAF_REPLAY_TIMESTAMP_INVALID")
    return parsed.astimezone(timezone.utc).isoformat()


def _numeric(value: Any) -> float:
    if type(value) not in (int, float) or not isfinite(float(value)):
        raise ReplayAuditError("NCAAF_REPLAY_NUMERIC_INVALID")
    return float(value)


def _require(ok: bool, code: str) -> None:
    if not ok:
        raise ReplayAuditError(code)


def _fail(code: str, *, candidate_id: str = "", detail: dict | None = None) -> dict:
    return {
        "status": "NCAAF_RESULT_FORM_REPLAY_FAILED_OR_BLOCKED",
        "code": code,
        "candidate_id": candidate_id,
        "detail": detail or {},
        "deterministic_replay_status": "BLOCKED",
        "source_attestation_status": "HOLD",
        "certification_granted": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _candidate_preflight(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    checks = (
        candidate.get("sport") == "NCAAF",
        candidate.get("league") == "NCAAF",
        candidate.get("model_family") == MODEL_FAMILY,
        candidate.get("feature_schema_version") == FEATURE_SCHEMA,
        candidate.get("source_policy_id") == SOURCE_POLICY,
        candidate.get("lifecycle_state") == "CANDIDATE",
        candidate.get("research_screen_pass") is True,
        candidate.get("promoted") is False,
        candidate.get("active") is False,
        candidate.get("probability_publishable") is False,
        candidate.get("can_execute") is False,
        candidate.get("automatic_certification") is False,
        candidate.get("automatic_promotion") is False,
    )
    _require(all(checks), "NCAAF_REPLAY_CANDIDATE_CONTRACT_UNSUPPORTED")
    payload = candidate.get("artifact_payload")
    cal = candidate.get("calibrator_payload")
    _require(isinstance(payload, Mapping) and isinstance(cal, Mapping), "NCAAF_REPLAY_FITTED_PACKAGE_MISSING")
    _require(tuple(payload.get("feature_names") or ()) == FEATURE_NAMES
             and payload.get("model_family") == MODEL_FAMILY
             and payload.get("artifact_format") == "STANDARDIZED_LOGISTIC_JSON_V1"
             and payload.get("split_policy") == "CHRONOLOGICAL_60_20_20",
             "NCAAF_REPLAY_ARTIFACT_SCHEMA_DRIFT")
    _require(_hash(payload) == str(candidate.get("artifact_checksum") or ""), "NCAAF_REPLAY_ARTIFACT_CHECKSUM_MISMATCH")
    _require(cal.get("method") == "IDENTITY_RAW_PROBABILITY_V1"
             and cal.get("bins") == []
             and cal.get("selection_version") == "FORWARD_CALIBRATION_SELECTION_V2"
             and (cal.get("selection") or {}).get("test_block_used_for_selection") is False,
             "NCAAF_REPLAY_CALIBRATOR_CONTRACT_UNSUPPORTED")
    stored_metrics = candidate.get("validation_metrics") or {}
    _require(stored_metrics.get("historical_reconstruction") is True
             and stored_metrics.get("archived_pregame_snapshot") is False
             and stored_metrics.get("market_features_used") is False,
             "NCAAF_REPLAY_RESEARCH_SOURCE_FLAGS_INVALID")
    code_sha = str(candidate.get("training_code_sha") or "")
    dataset_hash = str(candidate.get("training_dataset_hash") or "")
    version = f"{MODEL_FAMILY}_{dataset_hash[:16]}_{code_sha[:12]}"
    _require(len(code_sha) >= 12 and len(dataset_hash) == 64
             and candidate.get("model_artifact_version") == version,
             "NCAAF_REPLAY_VERSION_IDENTITY_INVALID")
    _iso(candidate.get("created_at"))
    return FEATURE_NAMES


def frozen_latest_rows(candidate: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Select exact candidate-era source versions; fork collisions are blocked."""
    bound = _iso(candidate.get("created_at"))
    latest: dict[str, dict] = {}
    for original in rows:
        row = dict(original)
        event_id = str(row.get("official_event_id") or "").strip()
        _require(bool(event_id), "NCAAF_REPLAY_EVENT_ID_MISSING")
        created_at = _iso(row.get("created_at"))
        if created_at > bound:
            continue
        prior = latest.get(event_id)
        if prior is None or created_at > _iso(prior["created_at"]):
            latest[event_id] = row
        elif created_at == _iso(prior["created_at"]):
            comparable = (
                "source_manifest_sha256", "features", "outcome_json", "event_start_time",
                "feature_as_of", "historical_reconstruction", "archived_pregame_snapshot",
                "market_features_used", "can_execute",
            )
            _require(all(row.get(k) == prior.get(k) for k in comparable),
                     "NCAAF_REPLAY_FROZEN_ROW_FORK")
    result = list(latest.values())
    result.sort(key=lambda r: (_iso(r.get("event_start_time")), str(r.get("official_event_id"))))
    return result


def audit_candidate(
    candidate: Mapping[str, Any], raw_training_rows: Sequence[Mapping[str, Any]],
    *, trainer: Callable | None = None,
) -> dict:
    """Recreate *exact* model/metrics; retain mandatory source certification hold."""
    cid = str(candidate.get("candidate_id") or "")
    try:
        names = _candidate_preflight(candidate)
        rows = frozen_latest_rows(candidate, raw_training_rows)
        expected = sum(int(candidate.get(k) or 0) for k in ("training_rows", "calibration_rows", "test_rows"))
        _require(expected >= 300 and len(rows) == expected, "NCAAF_REPLAY_FROZEN_CORPUS_COUNT_MISMATCH")
        # The source is intentionally retrospective. Neither as-of=start-1s nor
        # created_at after the event can magically attest pregame availability.
        _require(all(row.get("historical_reconstruction") is True
                     and row.get("archived_pregame_snapshot") is False for row in rows),
                 "NCAAF_REPLAY_SOURCE_REGIME_CHANGED")
        from v17.binary_candidate_lifecycle import BinaryTrainingRow, train_binary_candidate
        replay_rows = []
        for row in rows:
            manifest = row.get("source_manifest")
            _require(isinstance(manifest, Mapping)
                     and manifest.get("policy") == SOURCE_POLICY
                     and manifest.get("event_id") == str(row["official_event_id"])
                     and manifest.get("result_source") == "CFBD:/games"
                     and manifest.get("historical_reconstruction") is True
                     and manifest.get("market_features_used") is False
                     and isinstance(manifest.get("home_prior_event_ids"), list)
                     and isinstance(manifest.get("away_prior_event_ids"), list)
                     and len(manifest["home_prior_event_ids"]) >= 3
                     and len(manifest["away_prior_event_ids"]) >= 3
                     and str(row["official_event_id"]) not in manifest["home_prior_event_ids"]
                     and str(row["official_event_id"]) not in manifest["away_prior_event_ids"],
                     "NCAAF_REPLAY_SOURCE_MANIFEST_INVALID")
            _require(_hash(manifest) == str(row.get("source_manifest_sha256") or ""),
                     "NCAAF_REPLAY_SOURCE_MANIFEST_HASH_MISMATCH")
            _require(row.get("market_features_used") is False and row.get("can_execute") is False,
                     "NCAAF_REPLAY_ROW_EXECUTION_OR_MARKET_INVALID")
            start, asof = _iso(row["event_start_time"]), _iso(row["feature_as_of"])
            _require(asof < start, "NCAAF_REPLAY_TEMPORAL_ORDER_INVALID")
            # Exact historical as-of is reconstructed and not independently attested.
            fields = row.get("features")
            _require(isinstance(fields, Mapping) and set(fields) == set(names),
                     "NCAAF_REPLAY_FEATURE_SET_INVALID")
            features = {k: _numeric(fields[k]) for k in names}
            outcome = row.get("outcome_json") or {}
            _require(type(outcome.get("home_win")) is bool, "NCAAF_REPLAY_OUTCOME_INVALID")
            replay_rows.append(BinaryTrainingRow(str(row["official_event_id"]), start, asof,
                                                 outcome["home_win"], features,
                                                 str(row["source_manifest_sha256"])))
        fn = trainer or train_binary_candidate
        recreated = fn(replay_rows, model_family=MODEL_FAMILY, feature_names=names, min_rows=300)
        _require(str(recreated.dataset_hash) == str(candidate["training_dataset_hash"]),
                 "NCAAF_REPLAY_DATASET_HASH_MISMATCH")
        _require(_hash(recreated.artifact_payload) == str(candidate["artifact_checksum"]),
                 "NCAAF_REPLAY_FITTED_ARTIFACT_MISMATCH")
        _require(_hash(recreated.calibrator_payload) == _hash(candidate["calibrator_payload"]),
                 "NCAAF_REPLAY_CALIBRATOR_MISMATCH")
        actual_metrics = asdict(recreated.metrics)
        stored = candidate.get("validation_metrics") or {}
        for key, val in actual_metrics.items():
            earlier = stored.get(key)
            _require(type(earlier) in (int, float) and (
                int(earlier) == val if type(val) is int else
                isclose(float(earlier), val, rel_tol=0, abs_tol=1e-12)
            ), "NCAAF_REPLAY_RESEARCH_METRICS_MISMATCH")
        return {
            "status": "NCAAF_RESULT_FORM_DETERMINISTIC_REPLAY_PASS_SOURCE_HOLD",
            "candidate_id": cid,
            "model_artifact_version": str(candidate["model_artifact_version"]),
            "artifact_checksum": str(candidate["artifact_checksum"]),
            "dataset_hash": str(candidate["training_dataset_hash"]),
            "training_rows_replayed": len(replay_rows),
            "deterministic_replay_status": "PASS",
            "source_attestation_status": "HOLD",
            "source_hold_code": SOURCE_HOLD,
            "historical_reconstruction": True,
            "archived_pregame_snapshot": False,
            "calibration_status": "RESEARCH_ONLY_NOT_CERTIFIED",
            "certification_granted": False,
            "model_probability": None,
            "calibrated_lower_bound": None,
            "probability_publishable": False,
            "can_execute": False,
        }
    except ReplayAuditError as exc:
        return _fail(exc.code, candidate_id=cid)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        return _fail("NCAAF_REPLAY_INPUT_SHAPE_INVALID", candidate_id=cid,
                     detail={"error_type": type(exc).__name__})
    except Exception as exc:
        # Genuine selected-trainer errors must not be rewritten as model absent.
        return _fail("NCAAF_REPLAY_SCORER_EXECUTION_FAILED", candidate_id=cid,
                     detail={"error_type": type(exc).__name__})


def audit_from_db(db: Any, candidate_id: str) -> dict:
    """SELECT-only adapter; no API route and no persistence side effects."""
    try:
        result = (db.table("wow_d1_candidate_artifacts")
                  .select("*").eq("candidate_id", str(candidate_id)).limit(2).execute())
        candidates = list(result.data or [])
        if len(candidates) != 1:
            return _fail("NCAAF_REPLAY_CANDIDATE_NOT_FOUND", candidate_id=str(candidate_id))
        c = candidates[0]
        _candidate_preflight(c)  # fail before paging unrelated model families
        bound = _iso(c["created_at"])
        rows: list[dict] = []
        start = 0
        while True:
            page = (db.table("wow_d1_training_rows")
                    .select("created_at,official_event_id,event_start_time,feature_as_of,features,outcome_json,source_manifest,source_manifest_sha256,historical_reconstruction,archived_pregame_snapshot,market_features_used,can_execute")
                    .eq("sport", "NCAAF").eq("league", "NCAAF")
                    .eq("model_family", MODEL_FAMILY)
                    .eq("feature_schema_version", FEATURE_SCHEMA)
                    .lte("created_at", bound)
                    .order("created_at").order("official_event_id")
                    .range(start, start + 999).execute())
            batch = list(page.data or [])
            rows.extend(batch)
            if len(batch) < 1000:
                break
            start += 1000
        return audit_candidate(c, rows)
    except ReplayAuditError as exc:
        return _fail(exc.code, candidate_id=str(candidate_id))
    except Exception as exc:
        return _fail("NCAAF_REPLAY_DATA_READ_FAILED", candidate_id=str(candidate_id),
                     detail={"error_type": type(exc).__name__})


def install_ncaaf_result_form_replay_route(app: Any, *, auth_dependency: Any, db_client_fn: Any) -> None:
    """Authenticated SELECT-only research diagnostic; no writer or certifier."""
    from github_actions_oidc import scout_route_auth_dependency

    path = "/internal/v17/ncaaf-result-form-candidate-replay/{candidate_id}"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    @app.post(
        path,
        dependencies=[scout_route_auth_dependency(auth_dependency)],
        operation_id="auditWowV17NcaafResultFormCandidateReplayResearch",
    )
    def audit(candidate_id: str) -> dict[str, Any]:
        return audit_from_db(db_client_fn(), candidate_id)


__all__ = [
    "CAN_EXECUTE", "PROBABILITY_PUBLISHABLE", "VERIFIER_VERSION", "SOURCE_HOLD",
    "MODEL_FAMILY", "FEATURE_SCHEMA", "FEATURE_NAMES",
    "ReplayAuditError", "frozen_latest_rows", "audit_candidate", "audit_from_db",
    "install_ncaaf_result_form_replay_route",
]