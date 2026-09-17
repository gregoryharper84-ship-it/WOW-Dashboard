"""Artifact-isolated prospective calibrator candidate evidence for V17 props.

This module closes the engineering gap between accumulating settled forward
predictions and independent certification review. It may *fit candidate-only*
Platt/isotonic calibrators and evaluate them on an untouched chronological
holdout, but it never registers, activates, certifies, promotes, or publishes a
calibrator. Candidate evidence is tied to one immutable model artifact and one
exact sport/stat route. Direction twins and refreshed snapshots count once.

Candidate generation is deliberately stricter than the Phase-B fitting minimum:
an untouched holdout is required in addition to >= PHASE_B_MIN_N training rows.
This is an evidence-generation rule, not a certification threshold.
``can_execute`` is always false.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from calibration import (
    CalibrationStatus,
    PHASE_B_MIN_N,
    PHASE_C_MIN_N,
    PHASE_C_MIN_PER_REGION,
    PlattFitMetrics,
    phase_b_platt,
    phase_c_fit_isotonic,
    phase_c_promote,
)
from v17.prop_capability_manifest import DECLARED_PROP_LANES, normalize_prop_sport
from v17.prop_certification_runtime import PROVIDER

CAN_EXECUTE = False
PAGE_SIZE = 1000
OUTCOME_CHUNK_SIZE = 150
MIN_HOLDOUT_ROWS = 30
MIN_TIME_FOLDS = 6
DEFAULT_HOLDOUT_FRACTION = 0.20
CANDIDATE_EVIDENCE_VERSION = "V17_PROP_CALIBRATOR_CANDIDATE_EVIDENCE_V1"


class PropCalibratorCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)
    include_inactive: bool = True
    holdout_fraction: float = Field(default=DEFAULT_HOLDOUT_FRACTION, ge=0.10, le=0.30)


@dataclass(frozen=True)
class RawForwardObservation:
    prediction_id: str
    event_id: str
    player: str
    sport: str
    stat_type: str
    line: float
    direction: str
    raw_model_probability: float
    outcome: int
    model_timestamp: str
    event_start_time: str
    feature_schema_version: str
    model_family: str
    model_artifact_version: str
    artifact_checksum: str
    can_execute: bool = CAN_EXECUTE


@dataclass(frozen=True)
class CalibratorCandidatePacket:
    evidence_version: str
    sport: str
    stat_type: str
    feature_schema_version: str
    model_family: str
    model_artifact_version: str
    artifact_checksum: str
    independent_settled_n: int
    training_n: int
    holdout_n: int
    training_event_n: int
    holdout_event_n: int
    selected_method: str | None
    candidate_parameters: Mapping[str, Any] | None
    raw_holdout_metrics: Mapping[str, float] | None
    calibrated_holdout_metrics: Mapping[str, float] | None
    fit_oof_metrics: Mapping[str, float] | None
    evidence_hash: str
    status: str
    blockers: tuple[str, ...]
    certification_review_packet_ready: bool
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _token(sport: Any, stat_type: Any) -> str:
    return f"{normalize_prop_sport(str(sport or ''))}:{_norm(stat_type)}"


def _requested_tokens(routes: Sequence[str]) -> list[str]:
    declared = {_token(*key) for key in DECLARED_PROP_LANES}
    if not routes:
        return sorted(declared)
    out: list[str] = []
    for value in routes:
        token = _norm(value)
        if token not in declared:
            raise ValueError(f"undeclared prop route: {value!r}")
        if token not in out:
            out.append(token)
    return out


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (getattr(result, "data", None) or [])]


def _paginate(build: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    start = 0
    while True:
        batch = _rows(build().range(start, start + PAGE_SIZE - 1).execute())
        output.extend(batch)
        if len(batch) < PAGE_SIZE:
            return output
        start += PAGE_SIZE


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _finite_probability(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 < number < 1.0 else None


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.utcoffset() is not None else None


def _artifact_rows(db: Any, *, include_inactive: bool) -> list[dict[str, Any]]:
    fields = (
        "artifact_id,provider_identity,model_family,model_artifact_version,sport,stat_type,"
        "feature_schema_version,artifact_checksum,promoted,active,can_execute"
    )
    query = db.table("wow_prop_fitted_model_artifacts").select(fields).eq("provider_identity", PROVIDER)
    if not include_inactive:
        query = query.eq("active", True)
    return _rows(query.execute())


def _prediction_rows(db: Any, artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        "prediction_id,event_id,event_start_time,model_timestamp,locked_at,source_snapshot_id,player,"
        "sport,stat_type,line,direction,model_family,model_artifact_version,model_artifact_checksum,"
        "feature_schema_version,raw_model_probability,model_provider_identity"
    )
    return _paginate(
        lambda: db.table("wow_predictions").select(fields)
        .eq("model_provider_identity", PROVIDER)
        .eq("sport", str(artifact.get("sport")))
        .eq("stat_type", str(artifact.get("stat_type")))
        .eq("model_artifact_version", str(artifact.get("model_artifact_version")))
        .eq("model_artifact_checksum", str(artifact.get("artifact_checksum")))
    )


def _outcome_map(db: Any, prediction_ids: list[str]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(prediction_ids, OUTCOME_CHUNK_SIZE):
        result = (
            db.table("wow_outcomes")
            .select("prediction_id,hit,push,void,settlement_timestamp")
            .in_("prediction_id", chunk).execute()
        )
        for row in _rows(result):
            if row.get("prediction_id"):
                output[str(row["prediction_id"])] = row
    return output


def build_independent_raw_observations(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[RawForwardObservation], dict[str, int]]:
    """Return one pregame settled observation per event/player/stat/line thesis."""
    grouped: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    settled_direction_rows = 0
    excluded_invalid_rows = 0
    for row in predictions:
        prediction_id = str(row.get("prediction_id") or "")
        outcome = outcomes.get(prediction_id)
        if not outcome or outcome.get("hit") is None or outcome.get("push") is True or outcome.get("void") is True:
            continue
        settled_direction_rows += 1
        if not row.get("source_snapshot_id") or not row.get("locked_at"):
            excluded_invalid_rows += 1
            continue
        model_ts = _aware(row.get("model_timestamp"))
        locked_ts = _aware(row.get("locked_at"))
        event_ts = _aware(row.get("event_start_time"))
        if model_ts is None or locked_ts is None or event_ts is None or not (model_ts < event_ts and locked_ts < event_ts):
            excluded_invalid_rows += 1
            continue
        raw = _finite_probability(row.get("raw_model_probability"))
        if raw is None:
            excluded_invalid_rows += 1
            continue
        key = (
            str(row.get("event_id") or ""),
            str(row.get("player") or "").strip().casefold(),
            _norm(row.get("stat_type")),
            str(row.get("line")),
        )
        if not all(key):
            excluded_invalid_rows += 1
            continue
        grouped.setdefault(key, []).append(row)

    observations: list[RawForwardObservation] = []
    duplicate_or_twin_rows = 0
    for members in grouped.values():
        duplicate_or_twin_rows += max(0, len(members) - 1)
        more = [row for row in members if _norm(row.get("direction")) == "MORE"]
        less = [row for row in members if _norm(row.get("direction")) == "LESS"]
        pool = more or less
        if not pool:
            excluded_invalid_rows += len(members)
            continue
        chosen = sorted(
            pool,
            key=lambda row: (str(row.get("model_timestamp") or ""), str(row.get("prediction_id") or "")),
        )[-1]
        prediction_id = str(chosen["prediction_id"])
        outcome = outcomes[prediction_id]
        observations.append(
            RawForwardObservation(
                prediction_id=prediction_id,
                event_id=str(chosen.get("event_id") or ""),
                player=str(chosen.get("player") or ""),
                sport=str(chosen.get("sport") or ""),
                stat_type=str(chosen.get("stat_type") or ""),
                line=float(chosen.get("line")),
                direction=_norm(chosen.get("direction")),
                raw_model_probability=float(chosen["raw_model_probability"]),
                outcome=1 if outcome.get("hit") is True else 0,
                model_timestamp=str(chosen.get("model_timestamp") or ""),
                event_start_time=str(chosen.get("event_start_time") or ""),
                feature_schema_version=str(chosen.get("feature_schema_version") or ""),
                model_family=str(chosen.get("model_family") or ""),
                model_artifact_version=str(chosen.get("model_artifact_version") or ""),
                artifact_checksum=str(chosen.get("model_artifact_checksum") or ""),
            )
        )
    observations.sort(key=lambda row: (_aware(row.event_start_time), row.event_id, row.player, row.line))
    return observations, {
        "settled_direction_row_n": settled_direction_rows,
        "independent_settled_thesis_n": len(observations),
        "duplicate_or_twin_row_n": duplicate_or_twin_rows,
        "excluded_invalid_row_n": excluded_invalid_rows,
    }


def _metrics(probabilities: Sequence[float], outcomes: Sequence[int], bins: int = 10) -> dict[str, float]:
    p = np.clip(np.asarray(probabilities, dtype=float), 1e-9, 1 - 1e-9)
    y = np.asarray(outcomes, dtype=float)
    if len(p) == 0:
        raise ValueError("metrics require at least one row")
    brier = float(np.mean((p - y) ** 2))
    log_loss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    bias = float(np.mean(p - y))
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (p >= lo) & (p < hi if hi < 1.0 else p <= hi)
        if np.any(mask):
            ece += (float(np.sum(mask)) / len(p)) * abs(float(np.mean(p[mask]) - np.mean(y[mask])))
    return {"brier": brier, "log_loss": log_loss, "ece": ece, "calibration_bias": bias}


def _chronological_holdout(
    rows: Sequence[RawForwardObservation], holdout_fraction: float
) -> tuple[list[RawForwardObservation], list[RawForwardObservation]]:
    starts = sorted({_aware(row.event_start_time) for row in rows})
    if None in starts:
        raise ValueError("invalid event timestamp in calibration cohort")
    if len(starts) < MIN_TIME_FOLDS + 1:
        raise ValueError("not enough distinct event times for chronological holdout")
    holdout_event_n = max(1, int(math.ceil(len(starts) * holdout_fraction)))
    while holdout_event_n > 1:
        holdout_starts = set(starts[-holdout_event_n:])
        holdout = [row for row in rows if _aware(row.event_start_time) in holdout_starts]
        training = [row for row in rows if _aware(row.event_start_time) not in holdout_starts]
        if len(training) >= PHASE_B_MIN_N and len(holdout) >= MIN_HOLDOUT_ROWS:
            return training, holdout
        holdout_event_n -= 1
    holdout_starts = {starts[-1]}
    holdout = [row for row in rows if _aware(row.event_start_time) in holdout_starts]
    training = [row for row in rows if _aware(row.event_start_time) not in holdout_starts]
    if len(training) < PHASE_B_MIN_N or len(holdout) < MIN_HOLDOUT_ROWS:
        raise ValueError("forward cohort is not large enough for >=200 training rows plus >=30 untouched holdout rows")
    return training, holdout


def _time_folds(rows: Sequence[RawForwardObservation], fold_count: int = MIN_TIME_FOLDS) -> list[int]:
    event_starts = sorted({_aware(row.event_start_time) for row in rows})
    if len(event_starts) < fold_count:
        raise ValueError(f"calibrator candidate requires at least {fold_count} distinct event times")
    event_to_index = {value: index for index, value in enumerate(event_starts)}
    count = len(event_starts)
    folds: list[int] = []
    for row in rows:
        index = event_to_index[_aware(row.event_start_time)]
        fold = min(fold_count - 1, int(index * fold_count / count))
        folds.append(fold)
    if sorted(set(folds)) != list(range(fold_count)):
        raise ValueError("unable to form contiguous chronological calibration folds")
    return folds


def _platt_metrics_dict(metrics: PlattFitMetrics) -> dict[str, float]:
    return {
        "brier": float(metrics.brier),
        "log_loss": float(metrics.log_loss),
        "ece": float(metrics.ece),
        "calibration_bias": float(metrics.calibration_bias),
    }


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def build_calibrator_candidate_packet(
    artifact: Mapping[str, Any],
    observations: Sequence[RawForwardObservation],
    *,
    holdout_fraction: float = DEFAULT_HOLDOUT_FRACTION,
) -> CalibratorCandidatePacket:
    identity = {
        "sport": normalize_prop_sport(str(artifact.get("sport") or "")),
        "stat_type": _norm(artifact.get("stat_type")),
        "feature_schema_version": str(artifact.get("feature_schema_version") or ""),
        "model_family": str(artifact.get("model_family") or ""),
        "model_artifact_version": str(artifact.get("model_artifact_version") or ""),
        "artifact_checksum": str(artifact.get("artifact_checksum") or ""),
    }
    blockers: list[str] = []
    if artifact.get("can_execute") is not False:
        blockers.append("ARTIFACT_CAN_EXECUTE_MUST_BE_FALSE")
    if len(observations) < PHASE_B_MIN_N + MIN_HOLDOUT_ROWS:
        blockers.append("CALIBRATOR_CANDIDATE_FORWARD_HOLDOUT_NOT_MATURE")
    if blockers:
        payload = {**identity, "n": len(observations), "blockers": blockers, "version": CANDIDATE_EVIDENCE_VERSION}
        return CalibratorCandidatePacket(
            evidence_version=CANDIDATE_EVIDENCE_VERSION,
            **identity,
            independent_settled_n=len(observations),
            training_n=0,
            holdout_n=0,
            training_event_n=0,
            holdout_event_n=0,
            selected_method=None,
            candidate_parameters=None,
            raw_holdout_metrics=None,
            calibrated_holdout_metrics=None,
            fit_oof_metrics=None,
            evidence_hash=_canonical_hash(payload),
            status="CALIBRATOR_CANDIDATE_EVIDENCE_REQUIRED",
            blockers=tuple(blockers),
            certification_review_packet_ready=False,
        )

    try:
        training, holdout = _chronological_holdout(observations, holdout_fraction)
        folds = _time_folds(training)
        raw_train = [row.raw_model_probability for row in training]
        y_train = [row.outcome for row in training]
        ts_train = [row.event_start_time for row in training]
        platt = phase_b_platt(raw_train, y_train, folds, ts_train)

        selected_method = CalibrationStatus.PLATT_TIME_SPLIT_V1
        selected_apply = platt.coefficients.apply
        selected_parameters: dict[str, Any] = {
            "a": float(platt.coefficients.a),
            "b": float(platt.coefficients.b),
        }
        selected_oof_metrics = _platt_metrics_dict(platt.metrics)

        if len(training) >= PHASE_C_MIN_N:
            # Isotonic's minimum-per-region guard is evaluated on ten raw-probability
            # regions before fitting. The actual method is selected only if it beats
            # Platt under the already-ratified Phase-C comparison contract.
            counts: list[int] = []
            for index in range(10):
                lo, hi = index / 10, (index + 1) / 10
                counts.append(sum(1 for p in raw_train if lo <= p < hi or (index == 9 and p == 1.0)))
            if all(count >= PHASE_C_MIN_PER_REGION for count in counts):
                isotonic = phase_c_fit_isotonic(raw_train, y_train, folds, ts_train)
                if phase_c_promote(isotonic.metrics, platt.metrics):
                    selected_method = CalibrationStatus.ISOTONIC_V1
                    selected_apply = lambda p: float(isotonic.model.predict([p])[0])
                    selected_parameters = {
                        "x_thresholds": [float(v) for v in isotonic.model.X_thresholds_],
                        "y_thresholds": [float(v) for v in isotonic.model.y_thresholds_],
                    }
                    selected_oof_metrics = _platt_metrics_dict(isotonic.metrics)

        raw_holdout = [row.raw_model_probability for row in holdout]
        y_holdout = [row.outcome for row in holdout]
        calibrated_holdout = [selected_apply(p) for p in raw_holdout]
        raw_metrics = _metrics(raw_holdout, y_holdout)
        calibrated_metrics = _metrics(calibrated_holdout, y_holdout)
        evidence_rows = [
            {
                "prediction_id": row.prediction_id,
                "event_id": row.event_id,
                "raw_model_probability": row.raw_model_probability,
                "outcome": row.outcome,
                "event_start_time": row.event_start_time,
            }
            for row in observations
        ]
        candidate_payload = {
            **identity,
            "evidence_version": CANDIDATE_EVIDENCE_VERSION,
            "selected_method": selected_method,
            "candidate_parameters": selected_parameters,
            "raw_holdout_metrics": raw_metrics,
            "calibrated_holdout_metrics": calibrated_metrics,
            "fit_oof_metrics": selected_oof_metrics,
            "training_prediction_ids": [row.prediction_id for row in training],
            "holdout_prediction_ids": [row.prediction_id for row in holdout],
            "evidence_rows": evidence_rows,
            "can_execute": False,
        }
        candidate_improves = (
            calibrated_metrics["brier"] < raw_metrics["brier"]
            and calibrated_metrics["log_loss"] <= raw_metrics["log_loss"]
            and calibrated_metrics["ece"] <= raw_metrics["ece"]
        )
        candidate_blockers: list[str] = []
        if not candidate_improves:
            candidate_blockers.append("UNTOUCHED_HOLDOUT_IMPROVEMENT_NOT_DEMONSTRATED")
        return CalibratorCandidatePacket(
            evidence_version=CANDIDATE_EVIDENCE_VERSION,
            **identity,
            independent_settled_n=len(observations),
            training_n=len(training),
            holdout_n=len(holdout),
            training_event_n=len({row.event_id for row in training}),
            holdout_event_n=len({row.event_id for row in holdout}),
            selected_method=selected_method,
            candidate_parameters=selected_parameters,
            raw_holdout_metrics=raw_metrics,
            calibrated_holdout_metrics=calibrated_metrics,
            fit_oof_metrics=selected_oof_metrics,
            evidence_hash=_canonical_hash(candidate_payload),
            status="CALIBRATOR_CANDIDATE_REVIEW_PACKET_READY" if candidate_improves else "CALIBRATOR_CANDIDATE_HOLDOUT_BLOCKED",
            blockers=tuple(candidate_blockers),
            certification_review_packet_ready=candidate_improves,
        )
    except Exception as exc:
        payload = {**identity, "n": len(observations), "error_type": type(exc).__name__, "version": CANDIDATE_EVIDENCE_VERSION}
        return CalibratorCandidatePacket(
            evidence_version=CANDIDATE_EVIDENCE_VERSION,
            **identity,
            independent_settled_n=len(observations),
            training_n=0,
            holdout_n=0,
            training_event_n=0,
            holdout_event_n=0,
            selected_method=None,
            candidate_parameters=None,
            raw_holdout_metrics=None,
            calibrated_holdout_metrics=None,
            fit_oof_metrics=None,
            evidence_hash=_canonical_hash(payload),
            status="CALIBRATOR_CANDIDATE_BUILD_BLOCKED",
            blockers=(f"CALIBRATOR_CANDIDATE_BUILD_FAILED:{type(exc).__name__}",),
            certification_review_packet_ready=False,
        )


def run_prop_calibrator_candidate_audit(req: PropCalibratorCandidateRequest, *, db: Any) -> dict[str, Any]:
    requested = set(_requested_tokens(req.routes))
    packets: list[dict[str, Any]] = []
    total_prediction_rows = 0
    total_independent = 0
    total_twins_or_refresh = 0
    for artifact in _artifact_rows(db, include_inactive=req.include_inactive):
        token = _token(artifact.get("sport"), artifact.get("stat_type"))
        if token not in requested:
            continue
        predictions = _prediction_rows(db, artifact)
        total_prediction_rows += len(predictions)
        outcomes = _outcome_map(db, [str(row.get("prediction_id")) for row in predictions if row.get("prediction_id")])
        observations, audit = build_independent_raw_observations(predictions, outcomes)
        total_independent += audit["independent_settled_thesis_n"]
        total_twins_or_refresh += audit["duplicate_or_twin_row_n"]
        packet = build_calibrator_candidate_packet(
            artifact,
            observations,
            holdout_fraction=req.holdout_fraction,
        ).as_dict()
        packet["cohort_audit"] = audit
        packets.append(packet)

    packets.sort(key=lambda row: (
        str(row.get("sport") or ""),
        str(row.get("stat_type") or ""),
        str(row.get("model_artifact_version") or ""),
        str(row.get("artifact_checksum") or ""),
    ))
    return {
        "terminal": True,
        "evidence_version": CANDIDATE_EVIDENCE_VERSION,
        "routes_requested": sorted(requested),
        "artifact_packets": packets,
        "artifact_packet_n": len(packets),
        "prediction_rows_reviewed": total_prediction_rows,
        "independent_settled_thesis_n": total_independent,
        "duplicate_or_twin_row_n": total_twins_or_refresh,
        "review_packet_ready_n": sum(1 for row in packets if row.get("certification_review_packet_ready") is True),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CANDIDATE_EVIDENCE_VERSION",
    "CAN_EXECUTE",
    "CalibratorCandidatePacket",
    "MIN_HOLDOUT_ROWS",
    "PropCalibratorCandidateRequest",
    "RawForwardObservation",
    "build_calibrator_candidate_packet",
    "build_independent_raw_observations",
    "run_prop_calibrator_candidate_audit",
]
