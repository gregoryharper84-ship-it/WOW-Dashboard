"""WOW V17 candidate-only recalibration from *frozen* forecast/settlement pairs.

This is not a serving calibrator, an auto-promotion path, or a live DB writer.
It consumes a prior ChallengerProposal and a separately hydrated, audited
immutable snapshot. Its time-split score is retrospective *diagnostic* evidence:
if proposal selection used any of the later outcomes, the split is not an
untouched confirmatory holdout. A later prospective comparison is required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from json import dumps
from math import exp, isfinite, log
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
AUTOMATIC_PROMOTION = False
SCHEMA_VERSION = "WOW17_RECALIBRATION_CANDIDATE_V1"


class RefitEvidenceError(ValueError):
    """Typed evidence rejection; never convert into MODEL_UNAVAILABLE."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _instant(value: Any, code: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RefitEvidenceError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RefitEvidenceError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RefitEvidenceError(code)
    return parsed.astimezone(timezone.utc)


def _prob(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RefitEvidenceError("REFIT_PROBABILITY_INVALID")
    result = float(value)
    if not isfinite(result) or not 0.0 < result < 1.0:
        raise RefitEvidenceError("REFIT_PROBABILITY_INVALID")
    return result


def _adjust(value: float, offset: float) -> float:
    logit = log(value / (1.0 - value)) + offset
    if logit >= 0:
        return 1.0 / (1.0 + exp(-logit))
    q = exp(logit)
    return q / (1.0 + q)


def _fit_intercept(rows: Sequence[dict[str, Any]]) -> float:
    """Deterministic one-parameter logit intercept MLE, regularized to be finite."""
    lo, hi = -8.0, 8.0
    for _ in range(80):
        mid = (lo + hi) / 2
        error = sum(_adjust(r["probability"], mid) - r["target"] for r in rows)
        error += 0.1 * mid
        if error > 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def _metrics(rows: Sequence[dict[str, Any]], offset: float) -> dict[str, float]:
    predictions = [_adjust(row["probability"], offset) for row in rows]
    y = [row["target"] for row in rows]
    n = len(rows)
    return {
        "brier": sum((p - t) ** 2 for p, t in zip(predictions, y)) / n,
        "log_loss": -sum(t * log(max(1e-12, min(1 - 1e-12, p)))
                         + (1 - t) * log(max(1e-12, min(1 - 1e-12, 1 - p)))
                         for p, t in zip(predictions, y)) / n,
        "mean_probability": sum(predictions) / n,
        "observed_rate": sum(y) / n,
        "calibration_bias": sum(y) / n - sum(predictions) / n,
    }


def build_recalibration_candidate(
    proposal: Mapping[str, Any],
    frozen_rows: Sequence[Mapping[str, Any]],
    *,
    min_fit_events: int = 50,
    min_diagnostic_events: int = 30,
) -> dict[str, Any]:
    """Create candidate-only JSON; no persistence, certification or serving changes.

    Required row fields are pregame-frozen prediction ID, exact cohort, official
    event ID, model time, scheduled event start, official settlement time,
    calibrated probability, and settlement target (0/1). The caller must
    produce the join from immutable sources; do not reconstruct forecasts.
    """
    if proposal.get("proposal_type") != "RECALIBRATION_CHALLENGER":
        raise RefitEvidenceError("REFIT_PROPOSAL_TYPE_UNSUPPORTED")
    if proposal.get("automatic_promotion") is not False or proposal.get("can_execute") is not False:
        raise RefitEvidenceError("REFIT_PROPOSAL_SAFETY_INVALID")
    pid = str(proposal.get("proposal_id") or "").strip()
    target_key = str(proposal.get("target_key") or "").strip()
    if not pid or not target_key:
        raise RefitEvidenceError("REFIT_PROPOSAL_IDENTITY_REQUIRED")
    if min_fit_events < 50 or min_diagnostic_events < 20:
        raise RefitEvidenceError("REFIT_EVIDENCE_FLOOR_INVALID")

    by_id: dict[str, dict[str, Any]] = {}
    event_start_by_id: dict[str, datetime] = {}
    for raw in frozen_rows:
        pred_id = str(raw.get("source_prediction_id") or "").strip()
        eid = str(raw.get("official_event_id") or "").strip()
        if not pred_id or not eid:
            raise RefitEvidenceError("REFIT_EVENT_OR_PREDICTION_ID_MISSING")
        if pred_id in by_id:
            raise RefitEvidenceError("REFIT_DUPLICATE_PREDICTION")
        if raw.get("cohort_key") != target_key:
            raise RefitEvidenceError("REFIT_COHORT_MISMATCH")
        if raw.get("probability_source") != "calibrated_probability":
            raise RefitEvidenceError("REFIT_NOT_CALIBRATED_FORECAST")
        if raw.get("outcome_target") not in (0, 1) or isinstance(raw.get("outcome_target"), bool):
            raise RefitEvidenceError("REFIT_BINARY_SETTLEMENT_REQUIRED")
        model_at = _instant(raw.get("model_timestamp"), "REFIT_MODEL_TIME_REQUIRED")
        event_at = _instant(raw.get("event_start_time"), "REFIT_EVENT_TIME_REQUIRED")
        settled_at = _instant(raw.get("settlement_timestamp"), "REFIT_SETTLEMENT_TIME_REQUIRED")
        if not model_at < event_at <= settled_at:
            raise RefitEvidenceError("REFIT_TEMPORAL_IDENTITY_INVALID")
        if eid in event_start_by_id and event_start_by_id[eid] != event_at:
            raise RefitEvidenceError("REFIT_EVENT_TIME_CONFLICT")
        event_start_by_id[eid] = event_at
        by_id[pred_id] = {
            "prediction_id": pred_id,
            "event_id": eid,
            "event_start": event_at,
            "settled_at": settled_at,
            "model_at": model_at,
            "probability": _prob(raw.get("probability")),
            "target": raw["outcome_target"],
        }

    events = sorted(event_start_by_id, key=lambda eid: (event_start_by_id[eid], eid))
    if len(events) < min_fit_events + min_diagnostic_events:
        raise RefitEvidenceError("REFIT_EVENT_SAMPLE_INSUFFICIENT")
    # Split events, not individual props on the same event.
    diagnostic_count = max(min_diagnostic_events, len(events) // 4)
    split_at = event_start_by_id[events[-diagnostic_count]]
    fit = sorted((r for r in by_id.values()
                  if r["event_start"] < split_at and r["settled_at"] < split_at),
                 key=lambda r: (r["event_start"], r["prediction_id"]))
    diagnostic = sorted((r for r in by_id.values() if r["event_start"] >= split_at),
                        key=lambda r: (r["event_start"], r["prediction_id"]))
    fit_event_n = len({r["event_id"] for r in fit})
    diagnostic_event_n = len({r["event_id"] for r in diagnostic})
    if fit_event_n < min_fit_events or diagnostic_event_n < min_diagnostic_events:
        raise RefitEvidenceError("REFIT_MATURED_SAMPLE_INSUFFICIENT")

    intercept = _fit_intercept(fit)
    champion = _metrics(diagnostic, 0.0)
    challenger = _metrics(diagnostic, intercept)
    frozen = [{
        "prediction_id": r["prediction_id"],
        "event_id": r["event_id"],
        "event_start_time": r["event_start"].isoformat(),
        "model_timestamp": r["model_at"].isoformat(),
        "settlement_timestamp": r["settled_at"].isoformat(),
        "probability": r["probability"],
        "target": r["target"],
    } for r in sorted(by_id.values(), key=lambda r: r["prediction_id"])]
    dataset_sha = sha256(dumps(frozen, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    candidate_id = sha256(f"{SCHEMA_VERSION}|{pid}|{target_key}|{dataset_sha}".encode()).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "RETROSPECTIVE_DIAGNOSTIC_ONLY",
        "candidate_id": candidate_id,
        "proposal_id": pid,
        "target_key": target_key,
        "source_snapshot_sha256": dataset_sha,
        "method": "LOGIT_INTERCEPT_ONLY_L2_REGULARIZED_V1",
        "fitted_intercept": intercept,
        "fit_prediction_n": len(fit),
        "fit_event_n": fit_event_n,
        "diagnostic_prediction_n": len(diagnostic),
        "diagnostic_event_n": diagnostic_event_n,
        "excluded_unmatured_training_n": len(by_id) - len(fit) - len(diagnostic),
        "diagnostic_begin_utc": split_at.isoformat(),
        "incumbent_diagnostic": champion,
        "candidate_diagnostic": challenger,
        "delta_brier": champion["brier"] - challenger["brier"],
        "delta_log_loss": champion["log_loss"] - challenger["log_loss"],
        "selection_bias_warning": "Proposal was chosen using retrospective outcomes; diagnostic split is not an untouched promotion holdout.",
        "prospective_holdout_required": True,
        "lower_bound_reliability_status": "NOT_EVALUATED",
        "certification_replay_pass": False,
        "eligible_for_governed_review": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "model_parameters_changed": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "authority": "ADVISORY_ONLY",
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "AUTOMATIC_PROMOTION", "SCHEMA_VERSION", "RefitEvidenceError", "build_recalibration_candidate"]
