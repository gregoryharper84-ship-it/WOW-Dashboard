"""Candidate-bound WNBA TEAM_EVENT certification evidence.

This module is deliberately non-promoting.  It independently verifies the
latest WNBA dynamic-team-state candidate against persisted, pregame-only rows
and the governed SportsDataverse source contract, then writes one exact
candidate-bound evidence receipt.  The receipt can satisfy source-review and
certification-replay gates, but it cannot certify, promote, activate, publish,
rank, or execute a probability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import isclose
from typing import Any, Callable, Mapping, Sequence

from v17.binary_candidate_lifecycle import BinaryCandidateError, BinaryTrainingRow, train_binary_candidate
from v17.model_source_entitlements import SOURCES, source_readiness

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
SPORT = "WNBA"
LEAGUE = "WNBA"
MODEL_FAMILY = "WNBA_DYNAMIC_TEAM_STATE_LOGIT_V2"
FEATURE_SCHEMA_VERSION = "WNBA_DYNAMIC_TEAM_STATE_FEATURES_V2"
SOURCE_POLICY_ID = "TEAM_STATE_DYNAMIC_PRIOR_ONLY_V1"
SOURCE_ID = "SPORTSDATAVERSE_ESPN"
UPSTREAM_TABLE = "wow_wnba_training_games"
CANDIDATE_TABLE = "wow_d1_candidate_artifacts"
TRAINING_TABLE = "wow_d1_training_rows"
RECEIPT_TABLE = "wow_d1_certification_evidence_receipts"
EVIDENCE_VERSION = "WNBA_TEAM_EVENT_CERTIFICATION_EVIDENCE_V1"


class WNBACertificationEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _hash(payload: Any) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _iso(value: Any) -> datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_TIME_INVALID", raw) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _valid_sha256(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)


def _lane_id(candidate: Mapping[str, Any]) -> str:
    return f"{SPORT}:{LEAGUE}:{str(candidate.get('model_family') or '').strip().upper()}"


def _candidate_inert(candidate: Mapping[str, Any]) -> bool:
    return bool(
        str(candidate.get("lifecycle_state") or "").upper() == "CANDIDATE"
        and candidate.get("promoted") is False
        and candidate.get("active") is False
        and candidate.get("automatic_certification") is False
        and candidate.get("automatic_promotion") is False
        and candidate.get("probability_publishable") is False
        and candidate.get("can_execute") is False
    )


def _source_contract() -> dict[str, Any]:
    entitlement = SOURCES.get(SOURCE_ID)
    readiness = source_readiness(SOURCE_ID)
    if entitlement is None or not readiness.ready_for_candidate_training:
        raise WNBACertificationEvidenceError(
            "WNBA_CERT_EVIDENCE_SOURCE_NOT_ENTITLED",
            ",".join(readiness.blockers) if entitlement is not None else SOURCE_ID,
        )
    if SPORT not in entitlement.sports:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_SOURCE_SPORT_MISMATCH", SOURCE_ID)
    if entitlement.use != "TRAINING_OPEN_LICENSED" or not entitlement.fitted_training_allowed_when_ready:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_SOURCE_USE_FORBIDDEN", SOURCE_ID)
    if entitlement.license_id != "CC-BY-4.0" or not entitlement.license_url or not entitlement.attribution_required:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_LICENSE_CONTRACT_INVALID", SOURCE_ID)
    return {
        "source_id": SOURCE_ID,
        "use": entitlement.use,
        "license_id": entitlement.license_id,
        "license_url": entitlement.license_url,
        "attribution_required": entitlement.attribution_required,
        "ready_for_candidate_training": True,
    }


def _binary_rows(raw_rows: Sequence[Mapping[str, Any]]) -> tuple[list[BinaryTrainingRow], list[str]]:
    rows: list[BinaryTrainingRow] = []
    upstream_ids: list[str] = []
    seen_events: set[str] = set()
    for raw in raw_rows:
        event_id = str(raw.get("official_event_id") or "").strip()
        if not event_id or event_id in seen_events:
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_EVENT_ID_INVALID", event_id or "EMPTY")
        seen_events.add(event_id)
        if _iso(raw.get("feature_as_of")) >= _iso(raw.get("event_start_time")):
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_TEMPORAL_LEAKAGE", event_id)
        if raw.get("market_features_used") is not False:
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_MARKET_FEATURE_FORBIDDEN", event_id)
        if raw.get("can_execute") is not False:
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_EXECUTION_FLAG_FORBIDDEN", event_id)
        if raw.get("historical_reconstruction") is not True:
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_RECONSTRUCTION_FLAG_INVALID", event_id)
        manifest_hash = str(raw.get("source_manifest_sha256") or "").strip().lower()
        if not _valid_sha256(manifest_hash):
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_SOURCE_MANIFEST_HASH_INVALID", event_id)
        manifest = dict(raw.get("source_manifest") or {})
        inner = dict(manifest.get("source_manifest") or {})
        if inner.get("source") != UPSTREAM_TABLE:
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_UPSTREAM_SOURCE_INVALID", event_id)
        upstream_id = str(inner.get("game_id") or "").strip()
        if not upstream_id:
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_UPSTREAM_ID_MISSING", event_id)
        features = dict(raw.get("features") or {})
        outcome = dict(raw.get("outcome_json") or {})
        home_win = outcome.get("home_win")
        if not isinstance(home_win, bool):
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_OUTCOME_INVALID", event_id)
        rows.append(
            BinaryTrainingRow(
                event_id=event_id,
                event_start_time=_iso(raw.get("event_start_time")).isoformat(),
                feature_as_of=_iso(raw.get("feature_as_of")).isoformat(),
                positive_outcome=home_win,
                features=features,
                source_manifest_sha256=manifest_hash,
            )
        )
        upstream_ids.append(upstream_id)
    return rows, upstream_ids


def _verify_upstream(upstream_rows: Sequence[Mapping[str, Any]], required_ids: Sequence[str]) -> dict[str, Any]:
    by_id = {str(row.get("game_id") or "").strip(): dict(row) for row in upstream_rows}
    missing = [game_id for game_id in required_ids if game_id not in by_id]
    if missing:
        raise WNBACertificationEvidenceError(
            "WNBA_CERT_EVIDENCE_UPSTREAM_ROWS_MISSING",
            ",".join(missing[:10]),
        )
    bad_provider: list[str] = []
    bad_provenance: list[str] = []
    endpoints: set[str] = set()
    for game_id in required_ids:
        row = by_id[game_id]
        if row.get("settled") is not True or str(row.get("source_provider") or "") != SOURCE_ID:
            bad_provider.append(game_id)
            continue
        endpoint = str(row.get("source_endpoint") or "").strip()
        retrieved = str(row.get("source_retrieved_at") or "").strip()
        payload_hash = str(row.get("source_payload_sha256") or "").strip().lower()
        endpoints.add(endpoint)
        if (
            not endpoint
            or not retrieved
            or not _valid_sha256(payload_hash)
            or not (
                endpoint.startswith("https://github.com/sportsdataverse/")
                or endpoint.startswith("https://raw.githubusercontent.com/sportsdataverse/")
            )
        ):
            bad_provenance.append(game_id)
    if bad_provider:
        raise WNBACertificationEvidenceError(
            "WNBA_CERT_EVIDENCE_UPSTREAM_PROVIDER_MISMATCH",
            ",".join(bad_provider[:10]),
        )
    if bad_provenance:
        raise WNBACertificationEvidenceError(
            "WNBA_CERT_EVIDENCE_UPSTREAM_PROVENANCE_INVALID",
            ",".join(bad_provenance[:10]),
        )
    return {
        "required_rows": len(required_ids),
        "matched_rows": len(required_ids),
        "source_provider": SOURCE_ID,
        "source_endpoints": sorted(endpoints),
        "provenance_complete": True,
    }


def _metric(replayed: Any, key: str) -> float | int:
    return getattr(replayed.metrics, key)


def evaluate_candidate_evidence(
    candidate: Mapping[str, Any],
    training_rows: Sequence[Mapping[str, Any]],
    upstream_rows: Sequence[Mapping[str, Any]],
    *,
    trainer: Callable[..., Any] = train_binary_candidate,
) -> dict[str, Any]:
    """Verify one exact candidate without mutating its lifecycle state."""
    if str(candidate.get("sport") or "").upper() != SPORT or str(candidate.get("league") or "").upper() != LEAGUE:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_CANDIDATE_SCOPE_INVALID", str(candidate.get("sport")))
    if str(candidate.get("model_family") or "") != MODEL_FAMILY:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_MODEL_FAMILY_INVALID", str(candidate.get("model_family")))
    if str(candidate.get("feature_schema_version") or "") != FEATURE_SCHEMA_VERSION:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_FEATURE_SCHEMA_INVALID", str(candidate.get("feature_schema_version")))
    if str(candidate.get("source_policy_id") or "") != SOURCE_POLICY_ID:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_SOURCE_POLICY_INVALID", str(candidate.get("source_policy_id")))
    if candidate.get("research_screen_pass") is not True:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_RESEARCH_SCREEN_NOT_PASSED", "research screen")
    if not _candidate_inert(candidate):
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_CANDIDATE_INERTNESS_VIOLATION", "candidate flags")
    if not _valid_sha256(candidate.get("training_dataset_hash")) or not _valid_sha256(candidate.get("artifact_checksum")):
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_CANDIDATE_IDENTITY_INVALID", "hash identity")

    expected_total = sum(int(candidate.get(name) or 0) for name in ("training_rows", "calibration_rows", "test_rows"))
    if expected_total <= 0 or len(training_rows) < expected_total:
        raise WNBACertificationEvidenceError(
            "WNBA_CERT_EVIDENCE_TRAINING_ROWS_INSUFFICIENT",
            f"expected={expected_total};available={len(training_rows)}",
        )
    selected = list(training_rows)[:expected_total]
    binary_rows, upstream_ids = _binary_rows(selected)
    source_contract = _source_contract()
    upstream = _verify_upstream(upstream_rows, upstream_ids)

    artifact_payload = dict(candidate.get("artifact_payload") or {})
    feature_names = tuple(str(name) for name in (artifact_payload.get("feature_names") or ()))
    if not feature_names:
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_FEATURE_NAMES_MISSING", "artifact")
    try:
        replayed = trainer(
            binary_rows,
            model_family=MODEL_FAMILY,
            feature_names=feature_names,
            min_rows=300,
        )
    except BinaryCandidateError as exc:
        raise WNBACertificationEvidenceError(
            f"WNBA_CERT_EVIDENCE_REPLAY_{exc.code}", str(exc)
        ) from exc

    if replayed.dataset_hash != str(candidate.get("training_dataset_hash")):
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_DATASET_HASH_MISMATCH", replayed.dataset_hash)
    replay_artifact_checksum = _hash(dict(replayed.artifact_payload))
    if replay_artifact_checksum != str(candidate.get("artifact_checksum")):
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_ARTIFACT_CHECKSUM_MISMATCH", replay_artifact_checksum)
    if _hash(dict(replayed.calibrator_payload)) != _hash(dict(candidate.get("calibrator_payload") or {})):
        raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_CALIBRATOR_MISMATCH", "calibrator payload")

    validation = dict(candidate.get("validation_metrics") or {})
    metric_names = (
        "raw_brier", "calibrated_brier", "baseline_brier",
        "raw_log_loss", "calibrated_log_loss", "baseline_log_loss", "ece",
    )
    for name in metric_names:
        observed = float(validation.get(name))
        reproduced = float(_metric(replayed, name))
        if not isclose(observed, reproduced, rel_tol=0.0, abs_tol=1e-10):
            raise WNBACertificationEvidenceError(
                "WNBA_CERT_EVIDENCE_METRIC_MISMATCH",
                f"{name}:stored={observed};replayed={reproduced}",
            )
    for candidate_key, metric_name in (
        ("training_rows", "train_n"),
        ("calibration_rows", "calibration_n"),
        ("test_rows", "test_n"),
    ):
        if int(candidate.get(candidate_key) or 0) != int(_metric(replayed, metric_name)):
            raise WNBACertificationEvidenceError("WNBA_CERT_EVIDENCE_PARTITION_MISMATCH", candidate_key)

    return {
        "status": "CERTIFICATION_EVIDENCE_PASS",
        "evidence_version": EVIDENCE_VERSION,
        "candidate_id": str(candidate.get("candidate_id")),
        "sport": SPORT,
        "league": LEAGUE,
        "lane_id": _lane_id(candidate),
        "model_family": MODEL_FAMILY,
        "model_artifact_version": str(candidate.get("model_artifact_version")),
        "training_dataset_hash": str(candidate.get("training_dataset_hash")),
        "artifact_checksum": str(candidate.get("artifact_checksum")),
        "source_policy_id": SOURCE_POLICY_ID,
        "source_review_pass": True,
        "replay_evidence_pass": True,
        "source_review": source_contract,
        "upstream_provenance": upstream,
        "training_row_count": len(binary_rows),
        "replay_metrics": {
            name: float(_metric(replayed, name)) for name in metric_names
        } | {
            "train_n": int(_metric(replayed, "train_n")),
            "calibration_n": int(_metric(replayed, "calibration_n")),
            "test_n": int(_metric(replayed, "test_n")),
        },
        "candidate_mutated": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def _paged_rows(db: Any, table: str, selected: str, *, filters: Sequence[tuple[str, Any]] = (), orders: Sequence[str] = ()) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        query = db.table(table).select(selected)
        for field, value in filters:
            query = query.eq(field, value)
        for field in orders:
            query = query.order(field)
        batch = list(query.range(offset, offset + 999).execute().data or [])
        rows.extend(dict(row) for row in batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return rows


def run_wnba_team_event_certification_evidence(db: Any) -> dict[str, Any]:
    candidate_rows = (
        db.table(CANDIDATE_TABLE)
        .select(
            "candidate_id,created_at,sport,league,model_family,model_artifact_version,feature_schema_version,"
            "source_policy_id,training_dataset_hash,training_code_sha,artifact_checksum,artifact_payload,"
            "calibrator_payload,validation_metrics,training_rows,calibration_rows,test_rows,research_screen_pass,"
            "source_review_status,lifecycle_state,promoted,active,automatic_certification,automatic_promotion,"
            "probability_publishable,can_execute"
        )
        .eq("sport", SPORT)
        .eq("league", LEAGUE)
        .eq("model_family", MODEL_FAMILY)
        .eq("research_screen_pass", True)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not candidate_rows:
        return {
            "status": "BLOCKED",
            "code": "WNBA_CERT_EVIDENCE_CANDIDATE_MISSING",
            "source_review_pass": False,
            "replay_evidence_pass": False,
            "probability_publishable": False,
            "can_execute": False,
        }
    candidate = dict(candidate_rows[0])
    training_rows = _paged_rows(
        db,
        TRAINING_TABLE,
        "official_event_id,event_start_time,feature_as_of,features,outcome_json,source_manifest,source_manifest_sha256,"
        "historical_reconstruction,market_features_used,can_execute",
        filters=(("sport", SPORT), ("model_family", MODEL_FAMILY)),
        orders=("event_start_time", "official_event_id"),
    )
    upstream_rows = _paged_rows(
        db,
        UPSTREAM_TABLE,
        "game_id,settled,source_provider,source_endpoint,source_retrieved_at,source_payload_sha256",
        filters=(("settled", True),),
        orders=("game_date", "game_id"),
    )
    try:
        evidence = evaluate_candidate_evidence(candidate, training_rows, upstream_rows)
    except WNBACertificationEvidenceError as exc:
        return {
            "status": "BLOCKED",
            "code": exc.code,
            "detail": str(exc),
            "candidate_id": str(candidate.get("candidate_id")),
            "model_artifact_version": str(candidate.get("model_artifact_version")),
            "source_review_pass": False,
            "replay_evidence_pass": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    receipt = {
        key: evidence[key]
        for key in (
            "evidence_version", "candidate_id", "sport", "league", "lane_id", "model_family",
            "model_artifact_version", "training_dataset_hash", "artifact_checksum", "source_policy_id",
            "source_review_pass", "replay_evidence_pass", "probability_publishable", "can_execute",
        )
    }
    receipt["evidence_json"] = evidence
    db.table(RECEIPT_TABLE).upsert(
        receipt,
        on_conflict="candidate_id,evidence_version",
        ignore_duplicates=True,
    ).execute()
    evidence["receipt_persisted"] = True
    return evidence


__all__ = [
    "CAN_EXECUTE",
    "EVIDENCE_VERSION",
    "WNBACertificationEvidenceError",
    "evaluate_candidate_evidence",
    "run_wnba_team_event_certification_evidence",
]
