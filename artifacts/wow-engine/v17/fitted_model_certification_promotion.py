"""Unified V17 fitted-model certification replay and promotion decision.

This module composes the existing sport-specific certification contracts into
one deterministic, storage-agnostic lifecycle step:

    frozen fitted artifact + frozen replay corpus
        -> candidate-bound deterministic replay receipt
        -> ratified promotion-policy gate
        -> independent authorization gate
        -> governed-apply packet (never executed here)

It is deliberately not a scoring engine, a calibration fitter, a registry
writer, or an approval authority.  It never certifies, promotes, activates,
publishes, ranks, or executes a probability.  ``automatic_certification`` and
``automatic_promotion`` are permanently false, and every packet keeps
``probability_publishable`` and ``can_execute`` false.

The replay verifies evidence, not sporting coefficient values:

* exact immutable artifact/calibrator identity;
* a canonical digest over the complete frozen prediction corpus;
* chronological, disjoint train/calibration/test partitions;
* pre-event feature timing and per-row source-provenance binding;
* one controlling specialist per row;
* strict probability domains/normalisation and governed lower bounds;
* deterministic reproduction of stored Brier, log-loss and ECE metrics;
* settlement-state preservation for push/void outcomes.

Promotion thresholds are never invented.  A promotion policy must be supplied
explicitly and must be RATIFIED.  A promotion packet requires an independent
reviewer authorization bound to the exact replay receipt and artifact checksum,
with separation from the artifact author.  The result is an apply packet for a
privileged, separately reviewed registry mutation -- not a mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import math
from typing import Any, Iterable, Mapping, Sequence

from v17.prop_capability_manifest import (
    DECLARED_PROP_LANES,
    normalize_prop_sport,
    normalize_prop_stat,
)
from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    normalize_team_event_sport,
)
from v17.team_event_model_development_manifest import development_lane

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
SCHEMA_VERSION = "V17_FITTED_MODEL_CERTIFICATION_REPLAY_V1"
VERIFIER_VERSION = "V17_FITTED_MODEL_DETERMINISTIC_REPLAY_V1"
PROMOTION_PACKET_VERSION = "V17_FITTED_MODEL_PROMOTION_PACKET_V1"
MATRIX_VERSION = "V17_FITTED_MODEL_CROSS_SPORT_MATRIX_V1"

SURFACES = ("TEAM_EVENT", "PROP")
PARTITIONS = ("TRAIN", "CALIBRATION", "TEST")
SETTLEMENT_STATES = ("SETTLED", "PUSH", "VOID")
CERTIFIED_LIFECYCLE_STATES = frozenset(
    {"PROSPECTIVE_CERTIFIED", "CHAMPION", "CERTIFIED"}
)
PROMOTION_CANDIDATE_STATES = frozenset({"CANDIDATE", "SHADOW"})

REPLAY_PASS = "CERTIFICATION_REPLAY_PASS"
REPLAY_FAILED = "CERTIFICATION_REPLAY_FAILED"

PROMOTION_BLOCKED = "PROMOTION_BLOCKED"
REMAIN_SHADOW = "REMAIN_SHADOW"
PROMOTION_POLICY_REVIEW_REQUIRED = "PROMOTION_POLICY_REVIEW_REQUIRED"
PROMOTION_READY = "PROMOTION_READY_FOR_INDEPENDENT_REVIEW"
PROMOTION_AUTHORIZATION_INVALID = "PROMOTION_AUTHORIZATION_INVALID"
PROMOTION_APPROVED = "PROMOTION_APPROVED_FOR_GOVERNED_APPLY"
PROMOTION_ALREADY_APPLIED = "PROMOTION_ALREADY_APPLIED"

DEFAULT_REQUIRED_METRICS = ("brier", "log_loss", "ece")

_METRIC_ALIASES = {
    "brier": "brier",
    "brier_score": "brier",
    "log_loss": "log_loss",
    "logloss": "log_loss",
    "log-loss": "log_loss",
    "ece": "ece",
    "expected_calibration_error": "ece",
    "row_n": "row_n",
}

_HEX = frozenset("0123456789abcdef")

# Only this role may sign a promotion authorization.  The approval key is held by
# the independent governance boundary; callers of the internal route never hold
# it, so a request cannot invent a reviewer identity or role.
AUTHORIZED_REVIEWER_ROLES = frozenset({"INDEPENDENT_MODEL_REVIEWER"})
APPROVAL_KEY_ENV = "WOW_FITTED_PROMOTION_APPROVAL_KEY"
APPROVAL_SIGNATURE_PREFIX = "hmac-sha256:"
POLICY_SIGNATURE_VERSION = "V17_FITTED_MODEL_POLICY_SIGNATURE_V1"
AUTHORIZATION_SIGNATURE_VERSION = "V17_FITTED_MODEL_AUTHORIZATION_SIGNATURE_V1"


class FittedCertificationError(ValueError):
    """Typed contract rejection; never rewritten to MODEL_UNAVAILABLE."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def certification_sha256(value: Any) -> str:
    """Canonical digest used by replay/promotion packet identities."""
    return _sha(value)


def _text(value: Any, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise FittedCertificationError(code)
    return text


def _hash64(value: Any, code: str) -> str:
    text = _text(value, code).lower()
    if len(text) != 64 or any(ch not in _HEX for ch in text):
        raise FittedCertificationError(code)
    return text


def _code_sha(value: Any, code: str) -> str:
    text = _text(value, code).lower()
    if not 7 <= len(text) <= 64 or any(ch not in _HEX for ch in text):
        raise FittedCertificationError(code)
    return text


def _instant(value: Any, code: str) -> datetime:
    text = _text(value, code)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FittedCertificationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FittedCertificationError(code)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any, code: str) -> str:
    return _instant(value, code).isoformat()


def _number(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FittedCertificationError(code)
    result = float(value)
    if not math.isfinite(result):
        raise FittedCertificationError(code)
    return result


def _positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FittedCertificationError(code)
    return value


def _normalize_surface(value: Any) -> str:
    surface = str(value or "").strip().upper()
    if surface not in SURFACES:
        raise FittedCertificationError("FITTED_CERTIFICATION_SURFACE_UNSUPPORTED")
    return surface


def _normalize_sport(value: Any, surface: str) -> str:
    raw = str(value or "").strip().upper()
    sport = normalize_prop_sport(raw) if surface == "PROP" else normalize_team_event_sport(raw)
    if sport not in EXPECTED_TEAM_EVENT_SPORTS:
        raise FittedCertificationError("FITTED_CERTIFICATION_SPORT_UNSUPPORTED")
    return sport


def _normalize_partition(value: Any) -> str:
    partition = str(value or "").strip().upper()
    if partition not in PARTITIONS:
        raise FittedCertificationError("FITTED_CERTIFICATION_PARTITION_INVALID")
    return partition


def _normalize_settlement(value: Any) -> str:
    settlement = str(value or "").strip().upper()
    if settlement not in SETTLEMENT_STATES:
        raise FittedCertificationError("FITTED_CERTIFICATION_SETTLEMENT_STATE_INVALID")
    return settlement


@dataclass(frozen=True)
class FittedArtifactIdentity:
    artifact_id: str
    sport: str
    league: str
    surface: str
    model_family: str
    model_artifact_version: str
    stat_type: str | None
    feature_schema_version: str
    controlling_specialist: str
    artifact_author_id: str
    training_dataset_hash: str
    training_code_sha: str
    artifact_checksum: str
    calibrator_id: str
    calibrator_checksum: str
    calibration_method: str
    expected_corpus_sha256: str
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "sport": self.sport,
            "league": self.league,
            "surface": self.surface,
            "model_family": self.model_family,
            "model_artifact_version": self.model_artifact_version,
            "stat_type": self.stat_type,
            "feature_schema_version": self.feature_schema_version,
            "controlling_specialist": self.controlling_specialist,
            "artifact_author_id": self.artifact_author_id,
            "training_dataset_hash": self.training_dataset_hash,
            "training_code_sha": self.training_code_sha,
            "artifact_checksum": self.artifact_checksum,
            "calibrator_id": self.calibrator_id,
            "calibrator_checksum": self.calibrator_checksum,
            "calibration_method": self.calibration_method,
            "expected_corpus_sha256": self.expected_corpus_sha256,
            "can_execute": False,
        }

    @property
    def lane_id(self) -> str:
        if self.surface == "PROP":
            return f"{self.sport}:PROP:{self.stat_type}"
        return f"{self.sport}:TEAM_EVENT:{self.league}:{self.model_family}"


def parse_artifact_identity(payload: Mapping[str, Any]) -> FittedArtifactIdentity:
    if not isinstance(payload, Mapping):
        raise FittedCertificationError("FITTED_CERTIFICATION_IDENTITY_INVALID")
    surface = _normalize_surface(payload.get("surface"))
    sport = _normalize_sport(payload.get("sport"), surface)
    stat_type: str | None = None
    if surface == "PROP":
        stat_type = normalize_prop_stat(sport, _text(payload.get("stat_type"), "FITTED_CERTIFICATION_STAT_TYPE_REQUIRED"))
    elif str(payload.get("stat_type") or "").strip():
        raise FittedCertificationError("FITTED_CERTIFICATION_TEAM_EVENT_STAT_TYPE_FORBIDDEN")
    can_execute = payload.get("can_execute", False)
    if can_execute is not False:
        raise FittedCertificationError("FITTED_CERTIFICATION_CAN_EXECUTE_FORBIDDEN")
    return FittedArtifactIdentity(
        artifact_id=_text(payload.get("artifact_id"), "FITTED_CERTIFICATION_ARTIFACT_ID_REQUIRED"),
        sport=sport,
        league=str(payload.get("league") or sport).strip().upper(),
        surface=surface,
        model_family=_text(payload.get("model_family"), "FITTED_CERTIFICATION_MODEL_FAMILY_REQUIRED"),
        model_artifact_version=_text(payload.get("model_artifact_version"), "FITTED_CERTIFICATION_MODEL_VERSION_REQUIRED"),
        stat_type=stat_type,
        feature_schema_version=_text(payload.get("feature_schema_version"), "FITTED_CERTIFICATION_FEATURE_SCHEMA_REQUIRED"),
        controlling_specialist=_text(payload.get("controlling_specialist"), "FITTED_CERTIFICATION_SPECIALIST_REQUIRED"),
        artifact_author_id=_text(payload.get("artifact_author_id"), "FITTED_CERTIFICATION_ARTIFACT_AUTHOR_REQUIRED"),
        training_dataset_hash=_hash64(payload.get("training_dataset_hash"), "FITTED_CERTIFICATION_TRAINING_DATASET_HASH_INVALID"),
        training_code_sha=_code_sha(payload.get("training_code_sha"), "FITTED_CERTIFICATION_TRAINING_CODE_SHA_INVALID"),
        artifact_checksum=_hash64(payload.get("artifact_checksum"), "FITTED_CERTIFICATION_ARTIFACT_CHECKSUM_INVALID"),
        calibrator_id=_text(payload.get("calibrator_id"), "FITTED_CERTIFICATION_CALIBRATOR_ID_REQUIRED"),
        calibrator_checksum=_hash64(payload.get("calibrator_checksum"), "FITTED_CERTIFICATION_CALIBRATOR_CHECKSUM_INVALID"),
        calibration_method=_text(payload.get("calibration_method"), "FITTED_CERTIFICATION_CALIBRATION_METHOD_REQUIRED"),
        expected_corpus_sha256=_hash64(payload.get("expected_corpus_sha256"), "FITTED_CERTIFICATION_CORPUS_SHA256_INVALID"),
    )


@dataclass(frozen=True)
class FittedReplayRow:
    row_id: str
    official_event_id: str
    event_start_time: str
    feature_as_of: str
    partition: str
    settlement_state: str
    probabilities: tuple[float, ...]
    outcome: tuple[float, ...] | None
    lower_bounds: tuple[float, ...] | None
    source_manifest_sha256: str
    controlling_specialist: str
    market_features_used: bool = False
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "official_event_id": self.official_event_id,
            "event_start_time": self.event_start_time,
            "feature_as_of": self.feature_as_of,
            "partition": self.partition,
            "settlement_state": self.settlement_state,
            "probabilities": list(self.probabilities),
            "outcome": None if self.outcome is None else list(self.outcome),
            "lower_bounds": None if self.lower_bounds is None else list(self.lower_bounds),
            "source_manifest_sha256": self.source_manifest_sha256,
            "controlling_specialist": self.controlling_specialist,
            "market_features_used": False,
            "can_execute": False,
        }


def _probability_tuple(value: Any, code: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise FittedCertificationError(code)
    return tuple(_number(item, code) for item in value)


def parse_replay_row(payload: Mapping[str, Any]) -> FittedReplayRow:
    if not isinstance(payload, Mapping):
        raise FittedCertificationError("FITTED_CERTIFICATION_ROW_INVALID")
    if payload.get("market_features_used") is not False:
        raise FittedCertificationError("FITTED_CERTIFICATION_MARKET_FEATURES_FORBIDDEN")
    if payload.get("can_execute", False) is not False:
        raise FittedCertificationError("FITTED_CERTIFICATION_ROW_CAN_EXECUTE_FORBIDDEN")
    outcome_value = payload.get("outcome")
    outcome = None
    if outcome_value is not None:
        outcome = _probability_tuple(outcome_value, "FITTED_CERTIFICATION_OUTCOME_INVALID")
    lower_value = payload.get("lower_bounds")
    lower = None
    if lower_value is not None:
        lower = _probability_tuple(lower_value, "FITTED_CERTIFICATION_LOWER_BOUNDS_INVALID")
    return FittedReplayRow(
        row_id=_text(payload.get("row_id"), "FITTED_CERTIFICATION_ROW_ID_REQUIRED"),
        official_event_id=_text(payload.get("official_event_id"), "FITTED_CERTIFICATION_EVENT_ID_REQUIRED"),
        event_start_time=_iso(payload.get("event_start_time"), "FITTED_CERTIFICATION_EVENT_TIME_INVALID"),
        feature_as_of=_iso(payload.get("feature_as_of"), "FITTED_CERTIFICATION_FEATURE_TIME_INVALID"),
        partition=_normalize_partition(payload.get("partition")),
        settlement_state=_normalize_settlement(payload.get("settlement_state")),
        probabilities=_probability_tuple(payload.get("probabilities"), "FITTED_CERTIFICATION_PROBABILITIES_INVALID"),
        outcome=outcome,
        lower_bounds=lower,
        source_manifest_sha256=_hash64(payload.get("source_manifest_sha256"), "FITTED_CERTIFICATION_SOURCE_MANIFEST_HASH_INVALID"),
        controlling_specialist=_text(payload.get("controlling_specialist"), "FITTED_CERTIFICATION_ROW_SPECIALIST_REQUIRED"),
    )


@dataclass(frozen=True)
class FittedArtifactEvidence:
    identity: FittedArtifactIdentity
    rows: tuple[FittedReplayRow, ...]
    stored_metrics: Mapping[str, Any]
    split_counts: Mapping[str, int]
    lifecycle_state: str = "CANDIDATE"
    promoted: bool = False
    active: bool = False
    probability_publishable: bool = False
    automatic_certification: bool = False
    automatic_promotion: bool = False
    can_execute: bool = CAN_EXECUTE


def _split_count(payload: Mapping[str, Any], canonical: str, aliases: Sequence[str]) -> int:
    for key in (canonical, *aliases):
        if key in payload:
            return _positive_int(payload[key], "FITTED_CERTIFICATION_SPLIT_COUNT_INVALID")
    raise FittedCertificationError("FITTED_CERTIFICATION_SPLIT_COUNT_REQUIRED")


def parse_fitted_artifact(payload: Mapping[str, Any]) -> FittedArtifactEvidence:
    if not isinstance(payload, Mapping):
        raise FittedCertificationError("FITTED_CERTIFICATION_ARTIFACT_INVALID")
    identity_payload = payload.get("identity")
    if not isinstance(identity_payload, Mapping):
        raise FittedCertificationError("FITTED_CERTIFICATION_IDENTITY_REQUIRED")
    identity = parse_artifact_identity(identity_payload)
    rows_payload = payload.get("rows")
    if not isinstance(rows_payload, (list, tuple)) or not rows_payload:
        raise FittedCertificationError("FITTED_CERTIFICATION_ROWS_REQUIRED")
    rows = tuple(parse_replay_row(row) for row in rows_payload)
    metrics = payload.get("stored_metrics")
    if not isinstance(metrics, Mapping):
        raise FittedCertificationError("FITTED_CERTIFICATION_STORED_METRICS_REQUIRED")
    split_payload = payload.get("split_counts")
    if not isinstance(split_payload, Mapping):
        raise FittedCertificationError("FITTED_CERTIFICATION_SPLIT_COUNTS_REQUIRED")
    split_counts = {
        "TRAIN": _split_count(split_payload, "train_n", ("training_rows",)),
        "CALIBRATION": _split_count(split_payload, "calibration_n", ("calibration_rows",)),
        "TEST": _split_count(split_payload, "test_n", ("test_rows",)),
    }
    for name, expected in (
        ("automatic_certification", False),
        ("automatic_promotion", False),
    ):
        if payload.get(name, False) is not expected:
            raise FittedCertificationError(f"FITTED_CERTIFICATION_{name.upper()}_FORBIDDEN")
    if payload.get("can_execute", False) is not False:
        raise FittedCertificationError("FITTED_CERTIFICATION_CAN_EXECUTE_FORBIDDEN")
    for name in ("promoted", "active", "probability_publishable"):
        value = payload.get(name, False)
        if value is not True and value is not False:
            raise FittedCertificationError(f"FITTED_CERTIFICATION_{name.upper()}_INVALID")
    lifecycle = str(payload.get("lifecycle_state") or "CANDIDATE").strip().upper()
    if not lifecycle:
        raise FittedCertificationError("FITTED_CERTIFICATION_LIFECYCLE_INVALID")
    return FittedArtifactEvidence(
        identity=identity,
        rows=rows,
        stored_metrics=dict(metrics),
        split_counts=split_counts,
        lifecycle_state=lifecycle,
        promoted=payload.get("promoted") is True,
        active=payload.get("active") is True,
        probability_publishable=payload.get("probability_publishable") is True,
        automatic_certification=False,
        automatic_promotion=False,
        can_execute=False,
    )


def corpus_sha256(rows: Sequence[FittedReplayRow]) -> str:
    """Canonical digest of the complete frozen prediction corpus."""
    payload = sorted((row.as_dict() for row in rows), key=lambda item: item["row_id"])
    return _sha(payload)


def _ece(confidences: Sequence[float], outcomes: Sequence[float], bins: int = 10) -> float:
    n = len(confidences)
    if n == 0:
        return 0.0
    total = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [
            pos for pos, value in enumerate(confidences)
            if low <= value < high or (index == bins - 1 and value == 1.0)
        ]
        if not members:
            continue
        mean_conf = sum(confidences[pos] for pos in members) / len(members)
        mean_outcome = sum(outcomes[pos] for pos in members) / len(members)
        total += (len(members) / n) * abs(mean_conf - mean_outcome)
    return total


def compute_replay_metrics(
    rows: Sequence[FittedReplayRow],
    *,
    partition: str = "TEST",
) -> dict[str, Any]:
    """Recompute held-out metrics from frozen predictions in one partition.

    Certification and promotion gates use TEST only.  TRAIN and CALIBRATION
    rows remain part of corpus identity and chronological validation, but they
    are never allowed to improve or dilute the holdout score.
    """
    if partition not in PARTITIONS:
        raise FittedCertificationError("FITTED_CERTIFICATION_PARTITION_INVALID")
    settled = [
        row
        for row in rows
        if row.partition == partition and row.settlement_state == "SETTLED"
    ]
    if not settled:
        raise FittedCertificationError("FITTED_CERTIFICATION_NO_SETTLED_ROWS")
    arities = {len(row.probabilities) for row in settled}
    if len(arities) != 1:
        raise FittedCertificationError(
            "FITTED_CERTIFICATION_PROBABILITY_ARITY_INCONSISTENT"
        )
    binary = next(iter(arities)) == 1
    brier: list[float] = []
    log_loss: list[float] = []
    confidence: list[float] = []
    correct: list[float] = []
    realised: list[float] = []
    positives: list[float] = []
    for row in settled:
        probabilities = list(row.probabilities)
        if row.outcome is None or len(row.outcome) != len(probabilities):
            raise FittedCertificationError("FITTED_CERTIFICATION_OUTCOME_SHAPE_INVALID")
        if len(probabilities) == 1:
            p = probabilities[0]
            y = row.outcome[0]
            if y not in (0.0, 1.0):
                raise FittedCertificationError("FITTED_CERTIFICATION_OUTCOME_INVALID")
            brier.append((p - y) ** 2)
            log_loss.append(-math.log(max(p if y == 1.0 else 1.0 - p, 1e-15)))
            predicted_positive = p >= 0.5
            confidence.append(p if predicted_positive else 1.0 - p)
            hit = 1.0 if predicted_positive == (y == 1.0) else 0.0
            correct.append(hit)
            realised.append(p if y == 1.0 else 1.0 - p)
            positives.append(y)
        else:
            if any(value not in (0.0, 1.0) for value in row.outcome) or sum(row.outcome) != 1.0:
                raise FittedCertificationError("FITTED_CERTIFICATION_OUTCOME_INVALID")
            brier.append(sum((p - y) ** 2 for p, y in zip(probabilities, row.outcome)))
            true_index = next(i for i, value in enumerate(row.outcome) if value == 1.0)
            log_loss.append(-math.log(max(probabilities[true_index], 1e-15)))
            top_index = max(range(len(probabilities)), key=lambda i: probabilities[i])
            confidence.append(probabilities[top_index])
            correct.append(1.0 if top_index == true_index else 0.0)
            realised.append(probabilities[true_index])
    n = len(settled)
    mean_realised = sum(realised) / n
    accuracy = sum(correct) / n
    return {
        "row_n": n,
        "brier": sum(brier) / n,
        "log_loss": sum(log_loss) / n,
        "ece": _ece(confidence, correct),
        "mean_calibrated_probability": mean_realised,
        "top_label_accuracy": accuracy,
        "observed_positive_rate": (sum(positives) / n) if binary else None,
        "calibration_bias": mean_realised - accuracy,
    }


def _canonical_metric(name: Any) -> str | None:
    key = str(name or "").strip().lower()
    return _METRIC_ALIASES.get(key)


def _empty_receipt(
    *,
    status: str,
    identity: Mapping[str, Any] | None,
    blockers: Iterable[str],
    partition_counts: Mapping[str, int] | None = None,
    settlement_counts: Mapping[str, int] | None = None,
    partition_settled_counts: Mapping[str, int] | None = None,
    metrics: Mapping[str, Any] | None = None,
    received_corpus_sha256: str | None = None,
    verified_metrics: Sequence[str] = (),
    unverified_metrics: Sequence[str] = (),
    already_promoted: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "status": status,
        "identity": dict(identity) if identity is not None else None,
        "blockers": sorted(set(blockers)),
        "partition_counts": dict(partition_counts or {partition: 0 for partition in PARTITIONS}),
        "settlement_counts": dict(settlement_counts or {state: 0 for state in SETTLEMENT_STATES}),
        "partition_settled_counts": dict(
            partition_settled_counts or {partition: 0 for partition in PARTITIONS}
        ),
        "metrics": dict(metrics or {}),
        "verified_stored_metrics": sorted(set(verified_metrics)),
        "unverified_stored_metrics": sorted(set(unverified_metrics)),
        "received_corpus_sha256": received_corpus_sha256,
        "already_promoted": already_promoted,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }
    payload["receipt_sha256"] = _sha(payload)
    return payload


def replay_fitted_artifact(
    artifact: FittedArtifactEvidence | Mapping[str, Any],
    *,
    required_metrics: Sequence[str] = DEFAULT_REQUIRED_METRICS,
    max_rows: int = 100_000,
) -> dict[str, Any]:
    """Produce a deterministic, non-certifying replay receipt for one artifact."""
    try:
        parsed = artifact if isinstance(artifact, FittedArtifactEvidence) else parse_fitted_artifact(artifact)
    except FittedCertificationError as exc:
        return _empty_receipt(status=REPLAY_FAILED, identity=None, blockers=(exc.code,))

    identity = parsed.identity
    if len(parsed.rows) > max_rows:
        return _empty_receipt(
            status=REPLAY_FAILED,
            identity=identity.as_dict(),
            blockers=("FITTED_CERTIFICATION_ROW_LIMIT_EXCEEDED",),
            already_promoted=_already_promoted(parsed),
        )

    blockers: list[str] = []
    partition_counts = {partition: 0 for partition in PARTITIONS}
    settlement_counts = {state: 0 for state in SETTLEMENT_STATES}
    partition_settled_counts = {partition: 0 for partition in PARTITIONS}
    seen_row_ids: set[str] = set()
    event_partition: dict[str, str] = {}
    event_bounds: dict[str, tuple[str, datetime]] = {}
    settled_valid = True
    arity_consistent = True
    probability_arity: int | None = None

    for row in parsed.rows:
        partition_counts[row.partition] += 1
        settlement_counts[row.settlement_state] += 1
        if row.settlement_state == "SETTLED":
            partition_settled_counts[row.partition] += 1

        if row.row_id in seen_row_ids:
            blockers.append("FITTED_CERTIFICATION_ROW_ID_DUPLICATE")
        seen_row_ids.add(row.row_id)

        previous_partition = event_partition.get(row.official_event_id)
        if previous_partition is not None and previous_partition != row.partition:
            blockers.append("FITTED_CERTIFICATION_PARTITION_EVENT_OVERLAP")
        event_partition[row.official_event_id] = row.partition

        start = _instant(row.event_start_time, "FITTED_CERTIFICATION_EVENT_TIME_INVALID")
        as_of = _instant(row.feature_as_of, "FITTED_CERTIFICATION_FEATURE_TIME_INVALID")
        if as_of >= start:
            blockers.append("FITTED_CERTIFICATION_TEMPORAL_LEAKAGE")
        existing = event_bounds.get(row.official_event_id)
        if existing is not None and existing[1] != start:
            blockers.append("FITTED_CERTIFICATION_EVENT_TIME_CONFLICT")
        event_bounds.setdefault(row.official_event_id, (row.partition, start))

        if row.controlling_specialist != identity.controlling_specialist:
            blockers.append("FITTED_CERTIFICATION_CONTROLLING_SPECIALIST_MISMATCH")

        probabilities = row.probabilities
        if probability_arity is None:
            probability_arity = len(probabilities)
        elif len(probabilities) != probability_arity:
            blockers.append("FITTED_CERTIFICATION_PROBABILITY_ARITY_INCONSISTENT")
            arity_consistent = False
        if any(not 0.0 < value < 1.0 for value in probabilities):
            blockers.append("FITTED_CERTIFICATION_PROBABILITY_DOMAIN_INVALID")
        # Binary lanes carry one class-positive probability p; only an explicit
        # multi-outcome vector must normalize. Requiring sum(p)==1 for binary
        # would reject every legitimate fitted binary model.
        if len(probabilities) > 1 and abs(sum(probabilities) - 1.0) > 1e-9:
            blockers.append("FITTED_CERTIFICATION_PROBABILITY_NORMALISATION_INVALID")
        if row.lower_bounds is not None:
            if len(row.lower_bounds) != len(probabilities):
                blockers.append("FITTED_CERTIFICATION_LOWER_BOUND_SHAPE_INVALID")
            elif any(
                not 0.0 < lower <= probability
                for lower, probability in zip(row.lower_bounds, probabilities)
            ):
                blockers.append("FITTED_CERTIFICATION_LOWER_BOUND_ORDER_INVALID")

        if row.settlement_state == "SETTLED":
            if row.outcome is None or len(row.outcome) != len(probabilities):
                blockers.append("FITTED_CERTIFICATION_OUTCOME_SHAPE_INVALID")
                settled_valid = False
            elif len(probabilities) == 1:
                if row.outcome[0] not in (0.0, 1.0):
                    blockers.append("FITTED_CERTIFICATION_OUTCOME_INVALID")
                    settled_valid = False
            elif (
                any(value not in (0.0, 1.0) for value in row.outcome)
                or sum(row.outcome) != 1.0
            ):
                blockers.append("FITTED_CERTIFICATION_OUTCOME_INVALID")
                settled_valid = False
        elif row.outcome is not None:
            blockers.append("FITTED_CERTIFICATION_NON_SETTLED_OUTCOME_FORBIDDEN")

    for partition in PARTITIONS:
        if partition_counts[partition] != parsed.split_counts[partition]:
            blockers.append(f"FITTED_CERTIFICATION_SPLIT_COUNT_MISMATCH:{partition}")
        if partition_counts[partition] == 0:
            blockers.append(f"FITTED_CERTIFICATION_PARTITION_EMPTY:{partition}")

    by_partition: dict[str, list[datetime]] = {partition: [] for partition in PARTITIONS}
    for row in parsed.rows:
        by_partition[row.partition].append(
            _instant(row.event_start_time, "FITTED_CERTIFICATION_EVENT_TIME_INVALID")
        )
    if all(by_partition[partition] for partition in PARTITIONS):
        if max(by_partition["TRAIN"]) >= min(by_partition["CALIBRATION"]):
            blockers.append("FITTED_CERTIFICATION_PARTITION_TEMPORAL_ORDER_INVALID")
        if max(by_partition["CALIBRATION"]) >= min(by_partition["TEST"]):
            blockers.append("FITTED_CERTIFICATION_PARTITION_TEMPORAL_ORDER_INVALID")
    if partition_settled_counts["TEST"] == 0:
        blockers.append("FITTED_CERTIFICATION_TEST_SETTLED_ROWS_REQUIRED")

    received_corpus = corpus_sha256(parsed.rows)
    if received_corpus != identity.expected_corpus_sha256:
        blockers.append("FITTED_CERTIFICATION_CORPUS_SHA256_MISMATCH")

    metrics: dict[str, Any] = {}
    if settled_valid and arity_consistent and partition_settled_counts["TEST"] > 0:
        try:
            metrics = compute_replay_metrics(parsed.rows)
        except FittedCertificationError as exc:
            blockers.append(exc.code)

    requested: list[str] = []
    for name in required_metrics:
        canonical = _canonical_metric(name)
        if canonical is None:
            blockers.append(f"FITTED_CERTIFICATION_REQUIRED_METRIC_UNSUPPORTED:{name}")
        elif canonical not in requested:
            requested.append(canonical)
    if not requested:
        blockers.append("FITTED_CERTIFICATION_REQUIRED_METRICS_EMPTY")

    stored: dict[str, Any] = {}
    unverified: list[str] = []
    for raw_name, raw_value in parsed.stored_metrics.items():
        canonical = _canonical_metric(raw_name)
        if canonical is None:
            unverified.append(str(raw_name))
            continue
        if canonical in stored:
            blockers.append(f"FITTED_CERTIFICATION_STORED_METRIC_DUPLICATE:{canonical}")
        stored[canonical] = raw_value

    verified: list[str] = []
    for canonical in requested:
        if canonical not in stored:
            blockers.append(f"FITTED_CERTIFICATION_STORED_METRIC_MISSING:{canonical}")
            continue
        observed = metrics.get(canonical)
        declared = stored[canonical]
        if observed is None:
            blockers.append(f"FITTED_CERTIFICATION_METRIC_UNREPRODUCIBLE:{canonical}")
            continue
        if canonical == "row_n":
            if isinstance(declared, bool) or not isinstance(declared, int) or declared != observed:
                blockers.append("FITTED_CERTIFICATION_STORED_METRIC_MISMATCH:row_n")
                continue
            verified.append(canonical)
            continue
        if isinstance(declared, bool) or not isinstance(declared, (int, float)):
            blockers.append(f"FITTED_CERTIFICATION_STORED_METRIC_INVALID:{canonical}")
            continue
        if not math.isclose(float(declared), float(observed), rel_tol=0.0, abs_tol=1e-12):
            blockers.append(f"FITTED_CERTIFICATION_STORED_METRIC_MISMATCH:{canonical}")
            continue
        verified.append(canonical)

    unverified_metrics = sorted(set(unverified) | (set(stored) - set(verified)))
    status = REPLAY_PASS if not blockers else REPLAY_FAILED
    return _empty_receipt(
        status=status,
        identity=identity.as_dict(),
        blockers=blockers,
        partition_counts=partition_counts,
        settlement_counts=settlement_counts,
        partition_settled_counts=partition_settled_counts,
        metrics=metrics,
        received_corpus_sha256=received_corpus,
        verified_metrics=verified,
        unverified_metrics=unverified_metrics,
        already_promoted=_already_promoted(parsed),
    )


def _already_promoted(artifact: FittedArtifactEvidence) -> bool:
    return bool(
        artifact.promoted
        or artifact.active
        or artifact.lifecycle_state in CERTIFIED_LIFECYCLE_STATES
    )


@dataclass(frozen=True)
class PromotionPolicy:
    policy_id: str
    status: str
    scope_sport: str
    scope_surface: str
    min_settled_rows: int
    min_test_rows: int
    max_brier: float
    max_log_loss: float
    max_ece: float
    require_test_holdout: bool
    ratified_by: str
    ratified_at: str
    notes: str = ""
    signature: str = ""
    can_execute: bool = CAN_EXECUTE

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", str(self.status or "").strip().upper())
        sport = str(self.scope_sport or "").strip().upper()
        normalized_sport = "ALL" if sport == "ALL" else normalize_team_event_sport(sport)
        if normalized_sport != "ALL" and normalized_sport not in EXPECTED_TEAM_EVENT_SPORTS:
            raise FittedCertificationError("PROMOTION_POLICY_SPORT_UNSUPPORTED")
        object.__setattr__(self, "scope_sport", normalized_sport)
        surface = str(self.scope_surface or "").strip().upper()
        object.__setattr__(
            self,
            "scope_surface",
            "ALL" if surface == "ALL" else _normalize_surface(surface),
        )
        if not str(self.policy_id or "").strip():
            raise FittedCertificationError("PROMOTION_POLICY_ID_REQUIRED")
        _positive_int(self.min_settled_rows, "PROMOTION_POLICY_MIN_SETTLED_ROWS_INVALID")
        _positive_int(self.min_test_rows, "PROMOTION_POLICY_MIN_TEST_ROWS_INVALID")
        if not isinstance(self.require_test_holdout, bool):
            raise FittedCertificationError("PROMOTION_POLICY_HOLDOUT_FLAG_INVALID")
        for name in ("max_brier", "max_log_loss", "max_ece"):
            value = _number(getattr(self, name), f"PROMOTION_POLICY_THRESHOLD_INVALID:{name}")
            if value < 0.0:
                raise FittedCertificationError(f"PROMOTION_POLICY_THRESHOLD_INVALID:{name}")
        if self.can_execute is not False:
            raise FittedCertificationError("PROMOTION_POLICY_CAN_EXECUTE_FORBIDDEN")
        if self.status == "RATIFIED":
            if not str(self.ratified_by or "").strip():
                raise FittedCertificationError("PROMOTION_POLICY_RATIFIER_REQUIRED")
            _instant(self.ratified_at, "PROMOTION_POLICY_RATIFIED_AT_INVALID")

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "status": self.status,
            "scope_sport": self.scope_sport,
            "scope_surface": self.scope_surface,
            "min_settled_rows": self.min_settled_rows,
            "min_test_rows": self.min_test_rows,
            "max_brier": float(self.max_brier),
            "max_log_loss": float(self.max_log_loss),
            "max_ece": float(self.max_ece),
            "require_test_holdout": bool(self.require_test_holdout),
            "ratified_by": self.ratified_by,
            "ratified_at": self.ratified_at,
            "notes": self.notes,
            "signature": self.signature,
            "can_execute": False,
        }

    def signing_payload(self) -> dict[str, Any]:
        """Return the policy content covered by the governance signature."""
        return {key: value for key, value in self.as_dict().items() if key != "signature"}

    @property
    def policy_sha256(self) -> str:
        """Content-addressed policy identity, independent of signature encoding."""
        return _sha(self.signing_payload())

    def matches(self, sport: str, surface: str) -> bool:
        return (
            self.scope_sport in ("ALL", sport)
            and self.scope_surface in ("ALL", surface)
        )

    @property
    def specificity(self) -> int:
        return int(self.scope_sport != "ALL") + int(self.scope_surface != "ALL")


@dataclass(frozen=True)
class PromotionAuthorization:
    authorization_id: str
    policy_id: str
    evidence_sha256: str
    artifact_checksum: str
    reviewer_id: str
    reviewer_role: str
    decision: str
    decided_at: str
    signature: str = ""
    can_execute: bool = CAN_EXECUTE

    def __post_init__(self) -> None:
        for field, code in (
            ("authorization_id", "PROMOTION_AUTHORIZATION_ID_REQUIRED"),
            ("policy_id", "PROMOTION_AUTHORIZATION_POLICY_REQUIRED"),
            ("reviewer_id", "PROMOTION_AUTHORIZATION_REVIEWER_REQUIRED"),
            ("reviewer_role", "PROMOTION_AUTHORIZATION_REVIEWER_ROLE_REQUIRED"),
        ):
            if not str(getattr(self, field) or "").strip():
                raise FittedCertificationError(code)
        object.__setattr__(self, "decision", str(self.decision or "").strip().upper())
        object.__setattr__(
            self,
            "evidence_sha256",
            _hash64(self.evidence_sha256, "PROMOTION_AUTHORIZATION_EVIDENCE_HASH_INVALID"),
        )
        object.__setattr__(
            self,
            "artifact_checksum",
            _hash64(self.artifact_checksum, "PROMOTION_AUTHORIZATION_ARTIFACT_CHECKSUM_INVALID"),
        )
        role = str(self.reviewer_role or "").strip().upper()
        if role not in AUTHORIZED_REVIEWER_ROLES:
            raise FittedCertificationError("PROMOTION_AUTHORIZATION_REVIEWER_ROLE_NOT_AUTHORIZED")
        object.__setattr__(self, "reviewer_role", role)
        _instant(self.decided_at, "PROMOTION_AUTHORIZATION_DECIDED_AT_INVALID")
        if self.can_execute is not False:
            raise FittedCertificationError("PROMOTION_AUTHORIZATION_CAN_EXECUTE_FORBIDDEN")

    def as_dict(self) -> dict[str, Any]:
        return {
            "authorization_id": self.authorization_id,
            "policy_id": self.policy_id,
            "evidence_sha256": self.evidence_sha256,
            "artifact_checksum": self.artifact_checksum,
            "reviewer_id": self.reviewer_id,
            "reviewer_role": self.reviewer_role,
            "decision": self.decision,
            "decided_at": self.decided_at,
            "signature": self.signature,
            "can_execute": False,
        }

    def signing_payload(self) -> dict[str, Any]:
        """Return the authorization content covered by the approval signature."""
        return {key: value for key, value in self.as_dict().items() if key != "signature"}

    @property
    def authorization_sha256(self) -> str:
        """Content-addressed authorization identity, independent of signature encoding."""
        return _sha(self.signing_payload())


def _approval_signature(payload: Mapping[str, Any], key: str | None) -> str:
    """Return the keyed approval digest for an exact policy/auth payload."""
    if not isinstance(key, str) or not key:
        raise FittedCertificationError("PROMOTION_APPROVAL_KEY_UNAVAILABLE")
    digest = hmac.new(
        key.encode("utf-8"),
        _canonical(payload),
        sha256,
    ).hexdigest()
    return f"{APPROVAL_SIGNATURE_PREFIX}{digest}"


def sign_policy(policy: PromotionPolicy, key: str) -> PromotionPolicy:
    """Return a copy signed by the independent governance approval boundary."""
    return replace(policy, signature=_approval_signature(policy.signing_payload(), key))


def sign_authorization(
    authorization: PromotionAuthorization,
    key: str,
) -> PromotionAuthorization:
    """Return a copy signed by the independent governance approval boundary."""
    return replace(
        authorization,
        signature=_approval_signature(authorization.signing_payload(), key),
    )


def verify_policy_signature(
    policy: PromotionPolicy,
    key: str | None,
) -> PromotionPolicy:
    """Fail closed unless the policy carries the exact governance signature."""
    expected = _approval_signature(policy.signing_payload(), key)
    if not hmac.compare_digest(str(policy.signature or ""), expected):
        raise FittedCertificationError("PROMOTION_POLICY_SIGNATURE_INVALID")
    return policy


def verify_authorization_signature(
    authorization: PromotionAuthorization,
    key: str | None,
) -> PromotionAuthorization:
    """Fail closed unless the authorization carries the exact approval signature."""
    expected = _approval_signature(authorization.signing_payload(), key)
    if not hmac.compare_digest(str(authorization.signature or ""), expected):
        raise FittedCertificationError("PROMOTION_AUTHORIZATION_SIGNATURE_INVALID")
    return authorization


def _authorization_blockers(
    authorization: PromotionAuthorization,
    *,
    artifact: FittedArtifactEvidence,
    policy: PromotionPolicy,
    replay_sha256: str,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if authorization.decision != "APPROVE":
        blockers.append("PROMOTION_AUTHORIZATION_DECISION_NOT_APPROVE")
    if authorization.policy_id != policy.policy_id:
        blockers.append("PROMOTION_AUTHORIZATION_POLICY_MISMATCH")
    if authorization.evidence_sha256 != replay_sha256:
        blockers.append("PROMOTION_AUTHORIZATION_EVIDENCE_MISMATCH")
    if authorization.artifact_checksum != artifact.identity.artifact_checksum:
        blockers.append("PROMOTION_AUTHORIZATION_ARTIFACT_MISMATCH")
    if authorization.reviewer_id.strip().lower() == artifact.identity.artifact_author_id.strip().lower():
        blockers.append("PROMOTION_AUTHORIZATION_AUTHOR_NOT_INDEPENDENT")
    if str(authorization.reviewer_role or "").strip().upper() in {
        "",
        "AUTHOR",
        "MODEL_AUTHOR",
        "ENGINEER",
        "IMPLEMENTER",
    }:
        blockers.append("PROMOTION_AUTHORIZATION_REVIEWER_ROLE_NOT_INDEPENDENT")
    if policy.status == "RATIFIED" and _instant(
        authorization.decided_at, "PROMOTION_AUTHORIZATION_DECIDED_AT_INVALID"
    ) < _instant(policy.ratified_at, "PROMOTION_POLICY_RATIFIED_AT_INVALID"):
        blockers.append("PROMOTION_AUTHORIZATION_BEFORE_POLICY_RATIFICATION")
    return tuple(dict.fromkeys(blockers))


def _policy_gate_blockers(
    replay: Mapping[str, Any],
    *,
    artifact: FittedArtifactEvidence,
    policy: PromotionPolicy,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not policy.matches(artifact.identity.sport, artifact.identity.surface):
        blockers.append("PROMOTION_POLICY_SCOPE_MISMATCH")
    if policy.status != "RATIFIED":
        blockers.append("PROMOTION_POLICY_NOT_RATIFIED")

    metrics = replay.get("metrics") or {}
    partition_settled = replay.get("partition_settled_counts")
    if not isinstance(partition_settled, Mapping):
        blockers.append("PROMOTION_POLICY_SETTLED_ROW_COUNT_UNAVAILABLE")
        settled_n = None
        test_settled = 0
    else:
        settled_values = list(partition_settled.values())
        if any(isinstance(value, bool) or not isinstance(value, int) for value in settled_values):
            blockers.append("PROMOTION_POLICY_SETTLED_ROW_COUNT_UNAVAILABLE")
            settled_n = None
        else:
            settled_n = sum(settled_values)
        test_value = partition_settled.get("TEST", 0)
        test_settled = test_value if isinstance(test_value, int) and not isinstance(test_value, bool) else 0

    if settled_n is not None and settled_n < policy.min_settled_rows:
        blockers.append("PROMOTION_POLICY_GATE_FAILED:min_settled_rows")

    if test_settled < policy.min_test_rows:
        blockers.append("PROMOTION_POLICY_GATE_FAILED:min_test_rows")
    if isinstance(metrics.get("row_n"), int) and metrics.get("row_n") != test_settled:
        blockers.append("PROMOTION_POLICY_TEST_ROW_COUNT_MISMATCH")
    if policy.require_test_holdout and test_settled <= 0:
        blockers.append("PROMOTION_POLICY_GATE_FAILED:test_holdout_required")

    for metric, maximum in (
        ("brier", policy.max_brier),
        ("log_loss", policy.max_log_loss),
        ("ece", policy.max_ece),
    ):
        value = metrics.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            blockers.append(f"PROMOTION_POLICY_METRIC_UNAVAILABLE:{metric}")
        elif float(value) > float(maximum) + 1e-12:
            blockers.append(f"PROMOTION_POLICY_GATE_FAILED:{metric}")
    return tuple(dict.fromkeys(blockers))


def _apply_packet(artifact: FittedArtifactEvidence, replay: Mapping[str, Any], policy: PromotionPolicy, authorization: PromotionAuthorization) -> dict[str, Any]:
    target_lifecycle = (
        "PROSPECTIVE_CERTIFIED"
        if artifact.identity.surface == "PROP"
        else "CERTIFIED"
    )
    return {
        "operation": "GOVERNED_FITTED_MODEL_PROMOTION",
        "dry_run": True,
        "requires_privileged_application": True,
        "automatic_promotion": False,
        "artifact_id": artifact.identity.artifact_id,
        "artifact_checksum": artifact.identity.artifact_checksum,
        "sport": artifact.identity.sport,
        "league": artifact.identity.league,
        "surface": artifact.identity.surface,
        "stat_type": artifact.identity.stat_type,
        "model_family": artifact.identity.model_family,
        "model_artifact_version": artifact.identity.model_artifact_version,
        "expected_lifecycle_state": artifact.lifecycle_state,
        "target_registry_flags": {
            "lifecycle_state": target_lifecycle,
            "promoted": True,
            "active": True,
            "probability_publishable": False,
            "can_execute": False,
        },
        "publication_authorized": False,
        "publication_gate": "SEPARATE_GOVERNED_PUBLICATION_DECISION",
        "certification_receipt_sha256": replay.get("receipt_sha256"),
        "policy_sha256": policy.policy_sha256,
        "authorization_sha256": authorization.authorization_sha256,
        "can_execute": False,
    }


def _promotion_packet(
    *,
    status: str,
    artifact: FittedArtifactEvidence | None,
    replay: Mapping[str, Any],
    policy: PromotionPolicy | None,
    authorization: PromotionAuthorization | None,
    blockers: Iterable[str],
    apply_packet: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "packet_version": PROMOTION_PACKET_VERSION,
        "status": status,
        "artifact_identity": artifact.identity.as_dict() if artifact is not None else replay.get("identity"),
        "replay": {
            "status": replay.get("status"),
            "receipt_sha256": replay.get("receipt_sha256"),
            "corpus_sha256": replay.get("received_corpus_sha256"),
            "verified_stored_metrics": replay.get("verified_stored_metrics", []),
            "unverified_stored_metrics": replay.get("unverified_stored_metrics", []),
        },
        "policy": policy.as_dict() if policy is not None else None,
        "policy_sha256": policy.policy_sha256 if policy is not None else None,
        "authorization": authorization.as_dict() if authorization is not None else None,
        "authorization_sha256": authorization.authorization_sha256 if authorization is not None else None,
        "blockers": sorted(set(blockers)),
        "apply_packet": dict(apply_packet) if apply_packet is not None else None,
        "independent_review_required": True,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }
    payload["promotion_sha256"] = _sha(payload)
    return payload


def evaluate_fitted_model_promotion(
    artifact: FittedArtifactEvidence | Mapping[str, Any],
    *,
    policy: PromotionPolicy | None = None,
    authorization: PromotionAuthorization | None = None,
    required_metrics: Sequence[str] = DEFAULT_REQUIRED_METRICS,
) -> dict[str, Any]:
    """Fail-closed promotion decision. Never mutates a registry or approves itself."""
    try:
        parsed = artifact if isinstance(artifact, FittedArtifactEvidence) else parse_fitted_artifact(artifact)
    except FittedCertificationError as exc:
        replay = _empty_receipt(status=REPLAY_FAILED, identity=None, blockers=(exc.code,))
        return _promotion_packet(
            status=PROMOTION_BLOCKED,
            artifact=None,
            replay=replay,
            policy=policy,
            authorization=authorization,
            blockers=(exc.code,),
        )

    # Always replay the parsed artifact.  A caller-supplied receipt is never
    # trusted, even when its identity/corpus fields happen to match: its
    # metrics, blockers or status may be stale or tampered.
    replay = replay_fitted_artifact(parsed, required_metrics=required_metrics)
    if replay["status"] != REPLAY_PASS:
        return _promotion_packet(
            status=PROMOTION_BLOCKED,
            artifact=parsed,
            replay=replay,
            policy=policy,
            authorization=authorization,
            blockers=replay["blockers"],
        )

    if policy is None:
        already_promoted = _already_promoted(parsed)
        return _promotion_packet(
            status=PROMOTION_BLOCKED if already_promoted else PROMOTION_POLICY_REVIEW_REQUIRED,
            artifact=parsed,
            replay=replay,
            policy=None,
            authorization=authorization,
            blockers=(
                ("PROMOTED_STATE_WITHOUT_PROMOTION_AUTHORIZATION",)
                if already_promoted
                else ("PROMOTION_POLICY_REQUIRED",)
            ),
        )

    policy_blockers = _policy_gate_blockers(replay, artifact=parsed, policy=policy)
    if policy_blockers:
        review_required = any(
            code.startswith("PROMOTION_POLICY_NOT_RATIFIED")
            or code == "PROMOTION_POLICY_SCOPE_MISMATCH"
            for code in policy_blockers
        )
        if review_required:
            status = PROMOTION_POLICY_REVIEW_REQUIRED
        elif all(
            code.startswith("PROMOTION_POLICY_GATE_FAILED")
            or code.startswith("PROMOTION_POLICY_METRIC_UNAVAILABLE")
            or code == "PROMOTION_POLICY_SETTLED_ROW_COUNT_UNAVAILABLE"
            for code in policy_blockers
        ):
            status = REMAIN_SHADOW
        else:
            status = PROMOTION_BLOCKED
        if _already_promoted(parsed):
            status = PROMOTION_BLOCKED
        return _promotion_packet(
            status=status,
            artifact=parsed,
            replay=replay,
            policy=policy,
            authorization=authorization,
            blockers=policy_blockers,
        )

    if _already_promoted(parsed):
        if authorization is None:
            return _promotion_packet(
                status=PROMOTION_BLOCKED,
                artifact=parsed,
                replay=replay,
                policy=policy,
                authorization=None,
                blockers=("PROMOTED_STATE_WITHOUT_PROMOTION_AUTHORIZATION",),
            )
        auth_blockers = _authorization_blockers(
            authorization,
            artifact=parsed,
            policy=policy,
            replay_sha256=str(replay["receipt_sha256"]),
        )
        if auth_blockers:
            return _promotion_packet(
                status=PROMOTION_BLOCKED,
                artifact=parsed,
                replay=replay,
                policy=policy,
                authorization=authorization,
                blockers=auth_blockers,
            )
        return _promotion_packet(
            status=PROMOTION_ALREADY_APPLIED,
            artifact=parsed,
            replay=replay,
            policy=policy,
            authorization=authorization,
            blockers=(),
        )

    if parsed.lifecycle_state not in PROMOTION_CANDIDATE_STATES:
        return _promotion_packet(
            status=PROMOTION_BLOCKED,
            artifact=parsed,
            replay=replay,
            policy=policy,
            authorization=authorization,
            blockers=("PROMOTION_LIFECYCLE_STATE_NOT_PROMOTABLE",),
        )

    if parsed.probability_publishable:
        return _promotion_packet(
            status=PROMOTION_BLOCKED,
            artifact=parsed,
            replay=replay,
            policy=policy,
            authorization=authorization,
            blockers=("PROMOTION_PUBLICATION_FLAG_PREMATURE",),
        )

    if authorization is None:
        return _promotion_packet(
            status=PROMOTION_READY,
            artifact=parsed,
            replay=replay,
            policy=policy,
            authorization=None,
            blockers=(),
        )

    auth_blockers = _authorization_blockers(
        authorization,
        artifact=parsed,
        policy=policy,
        replay_sha256=str(replay["receipt_sha256"]),
    )
    if auth_blockers:
        return _promotion_packet(
            status=PROMOTION_AUTHORIZATION_INVALID,
            artifact=parsed,
            replay=replay,
            policy=policy,
            authorization=authorization,
            blockers=auth_blockers,
        )

    return _promotion_packet(
        status=PROMOTION_APPROVED,
        artifact=parsed,
        replay=replay,
        policy=policy,
        authorization=authorization,
        blockers=(),
        apply_packet=_apply_packet(parsed, replay, policy, authorization),
    )


def add_promotion_blockers(
    packet: Mapping[str, Any],
    blockers: Iterable[str],
    *,
    status: str,
) -> dict[str, Any]:
    """Return a re-digested packet after an audit-layer blocker is added."""
    updated = dict(packet)
    updated.pop("promotion_sha256", None)
    updated["status"] = status
    updated["blockers"] = sorted(
        set(updated.get("blockers") or ()) | set(blockers)
    )
    if status != PROMOTION_APPROVED:
        updated["apply_packet"] = None
    updated["promotion_sha256"] = _sha(updated)
    return updated


def parse_promotion_policy(payload: Mapping[str, Any]) -> PromotionPolicy:
    fields = (
        "policy_id",
        "status",
        "scope_sport",
        "scope_surface",
        "min_settled_rows",
        "min_test_rows",
        "max_brier",
        "max_log_loss",
        "max_ece",
        "require_test_holdout",		"ratified_by",
		"ratified_at",
		"notes",
        "signature",
	)

    if not isinstance(payload, Mapping):
        raise FittedCertificationError("PROMOTION_POLICY_INVALID")
    missing = [name for name in fields if name != "notes" and name not in payload]
    if missing:
        raise FittedCertificationError("PROMOTION_POLICY_FIELDS_REQUIRED")
    return PromotionPolicy(**{name: payload[name] for name in fields if name in payload})


def parse_promotion_authorization(payload: Mapping[str, Any]) -> PromotionAuthorization:
    fields = (
        "authorization_id",
        "policy_id",
        "evidence_sha256",
        "artifact_checksum",
        "reviewer_id",		"reviewer_role",
		"decision",
		"decided_at",
        "signature",
	)

    if not isinstance(payload, Mapping):
        raise FittedCertificationError("PROMOTION_AUTHORIZATION_INVALID")
    missing = [name for name in fields if name not in payload]
    if missing:
        raise FittedCertificationError("PROMOTION_AUTHORIZATION_FIELDS_REQUIRED")
    return PromotionAuthorization(**{name: payload[name] for name in fields})


def _select_policy(
    sport: str,
    surface: str,
    policies: Sequence[PromotionPolicy],
) -> tuple[PromotionPolicy | None, tuple[str, ...]]:
    matches = [policy for policy in policies if policy.matches(sport, surface)]
    if not matches:
        return None, ()
    specificity = max(policy.specificity for policy in matches)
    finalists = sorted(
        (policy for policy in matches if policy.specificity == specificity),
        key=lambda policy: policy.policy_id,
    )
    if len(finalists) != 1:
        return None, ("PROMOTION_POLICY_AMBIGUOUS",)
    return finalists[0], ()


_STATUS_RANK = {
    PROMOTION_APPROVED: 0,
    PROMOTION_ALREADY_APPLIED: 1,
    PROMOTION_READY: 2,
    PROMOTION_AUTHORIZATION_INVALID: 3,
    REMAIN_SHADOW: 4,
    PROMOTION_POLICY_REVIEW_REQUIRED: 5,
    PROMOTION_BLOCKED: 6,
}


def _best_result(results: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    return min(results, key=lambda row: (_STATUS_RANK.get(str(row.get("status")), 99), str(row.get("promotion_sha256", ""))))


def _compact_lane(result: Mapping[str, Any]) -> dict[str, Any]:
    identity = result.get("artifact_identity") or {}
    return {
        "artifact_id": identity.get("artifact_id"),
        "sport": identity.get("sport"),
        "league": identity.get("league"),
        "surface": identity.get("surface"),
        "stat_type": identity.get("stat_type"),
        "model_family": identity.get("model_family"),
        "model_artifact_version": identity.get("model_artifact_version"),
        "artifact_checksum": identity.get("artifact_checksum"),
        "status": result.get("status"),
        "blockers": list(result.get("blockers") or []),
        "replay_status": (result.get("replay") or {}).get("status"),
        "promotion_sha256": result.get("promotion_sha256"),
        "probability_publishable": False,
        "can_execute": False,
    }


def build_cross_sport_matrix(
    artifacts: Sequence[FittedArtifactEvidence | Mapping[str, Any]],
    *,
    policies: Sequence[PromotionPolicy] = (),
    authorizations: Sequence[PromotionAuthorization] = (),
    required_metrics: Sequence[str] = DEFAULT_REQUIRED_METRICS,
) -> dict[str, Any]:
    """Evaluate every required sport x surface lane without omitting any sport."""
    parsed_artifacts: list[FittedArtifactEvidence] = []
    parse_failures: list[dict[str, Any]] = []
    for raw in artifacts:
        try:
            parsed_artifacts.append(
                raw if isinstance(raw, FittedArtifactEvidence) else parse_fitted_artifact(raw)
            )
        except FittedCertificationError as exc:
            parse_failures.append({"code": exc.code})

    grouped: dict[tuple[str, str], list[FittedArtifactEvidence]] = {}
    for artifact in parsed_artifacts:
        grouped.setdefault(
            (artifact.identity.sport, artifact.identity.surface), []
        ).append(artifact)

    lane_rows: list[dict[str, Any]] = []
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        for surface in SURFACES:
            candidates = sorted(
                grouped.get((sport, surface), []),
                key=lambda artifact: (
                    artifact.identity.artifact_id,
                    artifact.identity.artifact_checksum,
                ),
            )
            if not candidates:
                lane_rows.append(_empty_lane_row(sport, surface))
                continue
            policy, policy_conflict = _select_policy(sport, surface, policies)
            results: list[dict[str, Any]] = []
            for artifact in candidates:
                base = evaluate_fitted_model_promotion(
                    artifact,
                    policy=policy,
                    authorization=None,
                    required_metrics=required_metrics,
                )
                if policy_conflict:
                    base = add_promotion_blockers(
                        base,
                        policy_conflict,
                        status=str(base.get("status")),
                    )
                matching = [
                    authorization
                    for authorization in authorizations
                    if policy is not None
                    and authorization.policy_id == policy.policy_id
                    and authorization.artifact_checksum == artifact.identity.artifact_checksum
                ]
                if policy is not None and matching:
                    with_auth = [
                        evaluate_fitted_model_promotion(
                            artifact,
                            policy=policy,
                            authorization=authorization,
                            required_metrics=required_metrics,
                        )
                        for authorization in sorted(matching, key=lambda item: item.authorization_id)
                    ]
                    # When a matching authorization exists, it must determine the
                    # lane result.  Falling back to the no-authorization READY
                    # packet would hide a rejection from the matrix.
                    base = _best_result(with_auth)
                results.append(base)
            best = _best_result(results)
            blockers = sorted(
                {
                    blocker
                    for result in results
                    for blocker in (result.get("blockers") or ())
                }
                | set(policy_conflict)
            )
            lane_rows.append(
                {
                    "sport": sport,
                    "surface": surface,
                    "status": best.get("status"),
                    "blockers": blockers,
                    "artifact_lane_count": len(results),
                    "replay_pass_lane_count": sum(
                        1
                        for result in results
                        if (result.get("replay") or {}).get("status") == REPLAY_PASS
                    ),
                    "promotion_ready_lane_count": sum(
                        1 for result in results if result.get("status") == PROMOTION_READY
                    ),
                    "promotion_approved_lane_count": sum(
                        1
                        for result in results
                        if result.get("status") in {PROMOTION_APPROVED, PROMOTION_ALREADY_APPLIED}
                    ),
                    "blocked_lane_count": sum(
                        1
                        for result in results
                        if result.get("status") not in {
                            PROMOTION_APPROVED,
                            PROMOTION_ALREADY_APPLIED,
                            PROMOTION_READY,
                        }
                    ),
                    "lanes": [_compact_lane(result) for result in results],
                    "probability_publishable": False,
                    "automatic_certification": False,
                    "automatic_promotion": False,
                    "can_execute": False,
                }
            )

    sports: list[dict[str, Any]] = []
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        rows = [row for row in lane_rows if row["sport"] == sport]
        sports.append(
            {
                "sport": sport,
                "lanes": {row["surface"]: row["status"] for row in rows},
                "blockers": sorted(
                    {blocker for row in rows for blocker in row["blockers"]}
                ),
                "promotion_approved_lane_count": sum(
                    row["promotion_approved_lane_count"] for row in rows
                ),
                "can_execute": False,
            }
        )

    payload: dict[str, Any] = {
        "matrix_version": MATRIX_VERSION,
        "status": "FITTED_MODEL_CERTIFICATION_MATRIX_COMPLETE",
        "lanes": lane_rows,
        "sports": sports,
        "parse_failures": parse_failures,
        "summary": {
            "required_sports": len(EXPECTED_TEAM_EVENT_SPORTS),
            "required_lanes": len(EXPECTED_TEAM_EVENT_SPORTS) * len(SURFACES),
            "cataloged_lanes": len(lane_rows),
            "lanes_with_artifact": sum(1 for row in lane_rows if row["artifact_lane_count"] > 0),
            "replay_pass_lanes": sum(1 for row in lane_rows if row["replay_pass_lane_count"] > 0),
            "promotion_ready_lanes": sum(1 for row in lane_rows if row["promotion_ready_lane_count"] > 0),
            "promotion_approved_lanes": sum(1 for row in lane_rows if row["promotion_approved_lane_count"] > 0),
            "blocked_lanes": sum(1 for row in lane_rows if row["blocked_lane_count"] > 0),
            "candidate_evidence_missing_lanes": sum(
                1 for row in lane_rows if row["status"] == "CANDIDATE_EVIDENCE_MISSING"
            ),
            "build_required_lanes": sum(
                1 for row in lane_rows if row["status"] == "MODEL_BUILD_REQUIRED"
            ),
        },
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }
    payload["matrix_sha256"] = _sha(payload)
    return payload


def _empty_lane_row(sport: str, surface: str) -> dict[str, Any]:
    if surface == "TEAM_EVENT":
        lane = development_lane(sport)
        build_required = lane is not None and lane.status == "BUILD_REQUIRED"
        status = "MODEL_BUILD_REQUIRED" if build_required else "CANDIDATE_EVIDENCE_MISSING"
        blockers = [
            "FITTED_MODEL_BUILD_REQUIRED" if build_required else "FITTED_CANDIDATE_EVIDENCE_MISSING"
        ]
    else:
        declared = any(key[0] == sport for key in DECLARED_PROP_LANES)
        status = "CANDIDATE_EVIDENCE_MISSING" if declared else "MODEL_UNAVAILABLE"
        blockers = [
            "FITTED_CANDIDATE_EVIDENCE_MISSING" if declared else "GOVERNED_MODEL_CAPABILITY_UNAVAILABLE"
        ]
    return {
        "sport": sport,
        "surface": surface,
        "status": status,
        "blockers": blockers,
        "artifact_lane_count": 0,
        "replay_pass_lane_count": 0,
        "promotion_ready_lane_count": 0,
        "promotion_approved_lane_count": 0,
        "blocked_lane_count": 0,
        "lanes": [],
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


__all__ = [
    "APPROVAL_KEY_ENV",
    "APPROVAL_SIGNATURE_PREFIX",
    "AUTHORIZATION_SIGNATURE_VERSION",
    "AUTHORIZED_REVIEWER_ROLES",
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "DEFAULT_REQUIRED_METRICS",
    "FittedArtifactEvidence",
    "FittedArtifactIdentity",
    "FittedCertificationError",
    "FittedReplayRow",
    "GLOBAL_TERMINAL_REDUCER",
    "MATRIX_VERSION",
    "PARTITIONS",
    "PROMOTION_ALREADY_APPLIED",
    "PROMOTION_APPROVED",
    "PROMOTION_AUTHORIZATION_INVALID",
    "PROMOTION_BLOCKED",
    "PROMOTION_PACKET_VERSION",
    "PROMOTION_POLICY_REVIEW_REQUIRED",
    "PROMOTION_READY",
    "POLICY_SIGNATURE_VERSION",
    "PROBABILITY_PUBLISHABLE",
    "REMAIN_SHADOW",
    "PromotionAuthorization",
    "PromotionPolicy",
    "REPLAY_FAILED",
    "REPLAY_PASS",
    "SCHEMA_VERSION",
    "SETTLEMENT_STATES",
    "SURFACES",
    "VERIFIER_VERSION",
    "add_promotion_blockers",
    "build_cross_sport_matrix",
    "certification_sha256",
    "compute_replay_metrics",
    "corpus_sha256",
    "evaluate_fitted_model_promotion",
    "parse_artifact_identity",
    "parse_fitted_artifact",
    "parse_promotion_authorization",
    "parse_promotion_policy",
    "parse_replay_row",
    "replay_fitted_artifact",
    "sign_authorization",
    "sign_policy",
    "verify_authorization_signature",
    "verify_policy_signature",
]
