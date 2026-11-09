"""Governed producer for the exact-version MLB calibrator evidence row.

The MLB evidence lane reads ``wow_mlb_event_calibrator_evidence``, which demands
a calibrator checksum, the calibration cohort digest, a disjoint chronological
holdout digest and holdout overlap of zero. The generic ``wow_calibrators`` table
stores none of that, so an exact-version calibrator could never be bonded to the
served artifact.

This producer fits the already-ratified Platt ladder (``calibration.phase_b_platt``
walk-forward out-of-fold fit) on a chronological fit cohort, scores a strictly
later holdout that the fit never saw, and derives every digest from the frozen
input rows it used. It does not choose a new calibration method, does not fit on
market prices, and cannot make a probability publishable.

Governed properties:

* **pregame provenance is required, not assumed.** Every settled observation
  must carry the immutable forecast timestamp and the official event start it
  belongs to, and the forecast must have been created strictly before first
  pitch. Chronological settlement alone proves nothing: a forecast written
  after the fact settles on time while being hindsight, so such a row is
  refused instead of silently entering the cohort;
* **the observation must come from the served artifact.** Each row names the
  artifact checksum whose forecast it is; a row from any other artifact is
  refused rather than pooled into this calibrator's cohort;
* the split is fixed by this contract, not by a caller: the last quarter of the
  distinct settlement days is the holdout and the earlier days are the fit
  cohort. There is no knob to move the split until a fit passes;
* the fit reuses the ratified Phase B walk-forward implementation, which
  verifies its own fold chronology from the settlement timestamps;
* duplicate settled identities are a conflict, so a row can never be counted
  twice or appear in both cohorts;
* ``calibrator_checksum`` binds the exact identity (calibrator id, artifact
  family/version/schema and artifact checksum), the coefficients and both
  cohort digests — so a checksum can never be reused for a different artifact,
  a different id or refitted weights, and ``calibration_coefficients`` +
  ``calibration_coefficients_sha256`` are persisted beside it so the fitted
  numbers are auditable after the fact instead of living only in this process;
* replaying the same identity with different coefficients is a conflict: the
  producer refuses rather than overwriting an existing row;
* the holdout is scored against the **incumbent** (the uncalibrated
  probabilities) on the identical rows, with Brier, log loss, bias, per-row
  counterexamples and a lower-bound reliability margin. A calibration that
  materially worsens the holdout, or whose declared lower bound is optimistic
  beyond tolerance, is refused instead of dressed up as improvement;
* only rows already settled at decision time are eligible; a future settlement
  timestamp is refused rather than back-dated;
* ``chronological_replay_verified`` and ``calibrated_lower_bound_verified`` are
  not written by this producer. They are independent-verification bindings the
  reader takes from the CERTIFIED receipt, and a producer must not attest to its
  own work. No probability, bound, rank flag or certification is written and
  ``can_execute`` is always false.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

import numpy as np

from calibration import (
    PHASE_B_MIN_N,
    PREDICTIVE_BOUNDS_METHOD_VERSION,
    CalibrationStatus,
    PlattCoefficients,
    phase_b_platt,
)
# The same ratified 1-D Platt fitter that produced these coefficients, so the
# bound bootstrap resamples the real fitter instead of a second implementation.
from calibration import _fit_platt_1d as fit_platt_1d

from v17.mlb_pregame_evidence_ingest import canonical_sha256

TABLE = "wow_mlb_event_calibrator_evidence"
SPORT = "MLB"
CALIBRATION_METHOD = CalibrationStatus.PLATT_TIME_SPLIT_V1
BOUNDS_METHOD_VERSION = PREDICTIVE_BOUNDS_METHOD_VERSION
CERTIFIED_LIFECYCLES = ("PROSPECTIVE_CERTIFIED", "CHAMPION")
# Fixed contract, not a parameter: the holdout is the final quarter of settlement
# days. A caller-tunable split would let the same cohort be re-split until it fit.
HOLDOUT_DAY_SHARE = 0.25
# A holdout that calibration makes materially worse is not evidence of
# calibration. The tolerance mirrors the ratified Phase B guard, which allows a
# metric to drift by at most 2% before it counts as a regression.
INCUMBENT_REGRESSION_TOLERANCE = 0.02
# Declared 10th-percentile floor and upper 90th, matching PREDICTIVE_BOUNDS_V1.
BOUNDS_LOWER_QUANTILE = 0.10
BOUNDS_UPPER_QUANTILE = 0.90
BOUNDS_BOOTSTRAP_REALIZATIONS = 200
BOUNDS_BOOTSTRAP_MIN_SUCCESS_SHARE = 0.80
# An honest lower bound may not exceed the realized holdout rate by more than
# this much; more overconfidence than that is a failed reliability check.
BOUNDS_RELIABILITY_TOLERANCE = 0.05
BOUNDS_BOOTSTRAP_SEED = 20261109
CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

PERSISTED = "PERSISTED"
ALREADY_CURRENT = "ALREADY_CURRENT"
REFUSED = "REFUSED"
WRITE_FAILED = "WRITE_FAILED"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _when(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _sha256(value: Any) -> bool:
    token = _text(value).lower()
    return len(token) == 64 and all(ch in "0123456789abcdef" for ch in token)


def _receipt(
    status: str,
    *,
    blockers: Iterable[str] = (),
    row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row = row or {}
    return {
        "status": status,
        "sport": SPORT,
        "calibrator_id": row.get("calibrator_id"),
        "model_family": row.get("model_family"),
        "model_artifact_version": row.get("model_artifact_version"),
        "feature_schema_version": row.get("feature_schema_version"),
        "model_artifact_checksum": row.get("model_artifact_checksum"),
        "calibrator_checksum": row.get("calibrator_checksum"),
        "calibration_coefficients_sha256": row.get("calibration_coefficients_sha256"),
        "calibration_cohort_sha256": row.get("calibration_cohort_sha256"),
        "holdout_evidence_sha256": row.get("holdout_evidence_sha256"),
        "calibration_training_n": row.get("calibration_training_n"),
        "holdout_n": row.get("holdout_n"),
        "holdout_overlap_rows": row.get("holdout_overlap_rows"),
        "holdout_brier_delta": row.get("holdout_brier_delta"),
        "holdout_log_loss_delta": row.get("holdout_log_loss_delta"),
        "holdout_lower_bound_reliability_margin": row.get(
            "holdout_lower_bound_reliability_margin"
        ),
        "blockers": list(blockers),
        "probability_written": False,
        "verification_flag_written": False,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _artifact_identity(
    artifact: Mapping[str, Any],
) -> tuple[dict[str, str] | None, tuple[str, ...]]:
    blockers: list[str] = []
    identity = {
        "model_family": _text(artifact.get("model_family")),
        "model_artifact_version": _text(artifact.get("model_artifact_version")),
        "feature_schema_version": _text(artifact.get("feature_schema_version")),
        "model_artifact_checksum": _text(artifact.get("artifact_checksum")).lower(),
    }
    for field, value in identity.items():
        if not value:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_ARTIFACT_INCOMPLETE:{field}")
    if identity["model_artifact_checksum"] and not _sha256(
        identity["model_artifact_checksum"]
    ):
        blockers.append("MLB_CALIBRATOR_EVIDENCE_ARTIFACT_CHECKSUM_INVALID")
    if artifact.get("active") is not True or artifact.get("promoted") is not True:
        blockers.append("MLB_CALIBRATOR_EVIDENCE_ARTIFACT_NOT_SERVED")
    lifecycle = _text(artifact.get("lifecycle_state")).upper()
    if lifecycle not in CERTIFIED_LIFECYCLES:
        blockers.append(
            f"MLB_CALIBRATOR_EVIDENCE_ARTIFACT_NOT_CERTIFIED:{lifecycle or 'UNKNOWN'}"
        )
    if blockers:
        return None, tuple(dict.fromkeys(blockers))
    return identity, ()


def normalize_settled_rows(
    settled_rows: Iterable[Mapping[str, Any]],
    *,
    decision_at: datetime,
    artifact_checksum: str | None = None,
) -> tuple[list[dict[str, Any]] | None, tuple[str, ...]]:
    """Validate and order settled observations; refuse anything unproven.

    A settled row is only eligible when it names the immutable forecast that
    produced ``raw_probability``, that forecast was created strictly before the
    official start it belongs to, the event settled after that start, and the
    forecast came from the artifact this calibrator is being bound to.
    """
    blockers: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    expected_artifact = _text(artifact_checksum).lower()
    for index, raw in enumerate(settled_rows):
        if not isinstance(raw, Mapping):
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_ROW_NOT_OBJECT:{index}")
            continue
        event_id = _text(raw.get("event_id") or raw.get("official_event_id"))
        if not event_id:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_ROW_IDENTITY_MISSING:{index}")
            continue
        probability = _number(raw.get("raw_probability"))
        outcome = raw.get("outcome")
        settled_at = _when(raw.get("settled_at") or raw.get("timestamp"))
        prediction_at = _when(raw.get("prediction_at") or raw.get("forecast_at"))
        event_start = _when(raw.get("event_start_time") or raw.get("commence_time"))
        row_artifact = _text(raw.get("model_artifact_checksum")).lower()
        if probability is None or not 0.0 < probability < 1.0:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_RAW_PROBABILITY_INVALID:{event_id}")
            continue
        if outcome is not True and outcome is not False and outcome not in (0, 1):
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_OUTCOME_INVALID:{event_id}")
            continue
        if settled_at is None:
            blockers.append(
                f"MLB_CALIBRATOR_EVIDENCE_SETTLEMENT_TIMESTAMP_INVALID:{event_id}"
            )
            continue
        if settled_at > decision_at:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_NOT_SETTLED_AT_DECISION:{event_id}")
            continue
        if prediction_at is None:
            # Without the forecast timestamp there is no proof the probability
            # predates first pitch, so the row cannot calibrate anything.
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_FORECAST_TIMESTAMP_MISSING:{event_id}")
            continue
        if event_start is None:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_EVENT_START_MISSING:{event_id}")
            continue
        if prediction_at >= event_start:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_FORECAST_NOT_PREGAME:{event_id}")
            continue
        if prediction_at > decision_at:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_FORECAST_AFTER_DECISION:{event_id}")
            continue
        if settled_at < event_start:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_SETTLED_BEFORE_START:{event_id}")
            continue
        if expected_artifact and row_artifact != expected_artifact:
            blockers.append(
                f"MLB_CALIBRATOR_EVIDENCE_ARTIFACT_PROVENANCE_MISMATCH:{event_id}"
            )
            continue
        if event_id in seen:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_DUPLICATE_SETTLED_ROW:{event_id}")
            continue
        seen.add(event_id)
        rows.append(
            {
                "event_id": event_id,
                "raw_probability": probability,
                "outcome": int(bool(outcome)),
                "settled_at": settled_at,
                "prediction_at": prediction_at,
                "event_start_time": event_start,
                "model_artifact_checksum": row_artifact or expected_artifact,
            }
        )
    if blockers:
        return None, tuple(dict.fromkeys(blockers))
    rows.sort(key=lambda row: (row["settled_at"], row["event_id"]))
    return rows, ()


def partition_settled_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Chronological split on distinct settlement days, fixed at one quarter."""
    days = sorted({row["settled_at"].date() for row in rows})
    holdout_days = max(1, round(len(days) * HOLDOUT_DAY_SHARE))
    boundary: date = days[-holdout_days]
    fit = [row for row in rows if row["settled_at"].date() < boundary]
    holdout = [row for row in rows if row["settled_at"].date() >= boundary]
    return fit, holdout


def _folds(rows: list[dict[str, Any]]) -> list[int]:
    """Contiguous fold ids in time order, one per settlement day.

    A day is never split across folds, so every validation fold starts strictly
    after the timestamp of its training data and the ratified fitter's own
    chronology assertion cannot be bypassed.
    """
    days = sorted({row["settled_at"].date() for row in rows})
    position = {day: index for index, day in enumerate(days)}
    return [position[row["settled_at"].date()] for row in rows]


def _losses(
    rows: list[dict[str, Any]], coefficients: PlattCoefficients
) -> tuple[float, float, float, list[tuple[float, float]]]:
    """Brier, log loss, bias and the per-row paired losses for one scoring rule."""
    probs = [
        min(max(coefficients.apply(row["raw_probability"]), 1e-9), 1.0 - 1e-9)
        for row in rows
    ]
    outcomes = [row["outcome"] for row in rows]
    n = len(probs)
    per_row = [
        (
            (probability - outcome) ** 2,
            -(
                outcome * math.log(probability)
                + (1 - outcome) * math.log(1.0 - probability)
            ),
        )
        for probability, outcome in zip(probs, outcomes)
    ]
    brier = sum(brier_row for brier_row, _ in per_row) / n
    log_loss = sum(log_row for _, log_row in per_row) / n
    bias = sum(probs) / n - sum(outcomes) / n
    return brier, log_loss, bias, per_row


def _bootstrap_bounds(
    fit: list[dict[str, Any]], holdout: list[dict[str, Any]]
) -> tuple[list[float] | None, list[float] | None, tuple[str, ...]]:
    """Declared lower/upper bounds per holdout row, from the ratified fitter.

    The fit cohort is resampled and refitted with the same 1-D Platt fitter used
    to produce the coefficients; the 10th/90th percentiles of those refits at
    each holdout probability are the bounds. This resamples coefficient
    uncertainty, not outcomes, so it cannot borrow the holdout's own answers.
    """
    raw = np.array([row["raw_probability"] for row in fit], dtype=float)
    outcomes = np.array([row["outcome"] for row in fit], dtype=float)
    targets = np.array([row["raw_probability"] for row in holdout], dtype=float)
    clipped = np.clip(targets, 1e-9, 1.0 - 1e-9)
    logit_targets = np.log(clipped / (1.0 - clipped))
    rng = np.random.default_rng(BOUNDS_BOOTSTRAP_SEED)
    draws: list[np.ndarray] = []
    for _ in range(BOUNDS_BOOTSTRAP_REALIZATIONS):
        index = rng.integers(0, len(raw), size=len(raw))
        try:
            a, b = fit_platt_1d(raw[index], outcomes[index])
        except (ValueError, TypeError, ArithmeticError, np.linalg.LinAlgError):
            continue
        values = a + b * logit_targets
        draws.append(1.0 / (1.0 + np.exp(-values)))
    if not draws:
        return None, None, ("MLB_CALIBRATOR_EVIDENCE_BOUNDS_BOOTSTRAP_EMPTY",)
    if len(draws) < BOUNDS_BOOTSTRAP_REALIZATIONS * BOUNDS_BOOTSTRAP_MIN_SUCCESS_SHARE:
        return None, None, (
            f"MLB_CALIBRATOR_EVIDENCE_BOUNDS_BOOTSTRAP_INSUFFICIENT:"
            f"{len(draws)}<{int(BOUNDS_BOOTSTRAP_REALIZATIONS * BOUNDS_BOOTSTRAP_MIN_SUCCESS_SHARE)}",
        )
    matrix = np.vstack(draws)
    lower = np.percentile(matrix, BOUNDS_LOWER_QUANTILE * 100, axis=0)
    upper = np.percentile(matrix, BOUNDS_UPPER_QUANTILE * 100, axis=0)
    if not (np.all(np.isfinite(lower)) and np.all(np.isfinite(upper))):
        return None, None, ("MLB_CALIBRATOR_EVIDENCE_BOUNDS_NON_FINITE",)
    return [float(value) for value in lower], [float(value) for value in upper], ()


def _comparison_blockers(
    brier: float, incumbent_brier: float, lower_bound_margin: float
) -> tuple[str, ...]:
    """Refuse a calibrator the holdout does not support."""
    blockers: list[str] = []
    if brier > incumbent_brier * (1.0 + INCUMBENT_REGRESSION_TOLERANCE):
        blockers.append(
            "MLB_CALIBRATOR_EVIDENCE_HOLDOUT_BRIER_REGRESSION_VS_INCUMBENT:"
            f"{brier:.6f}>{incumbent_brier:.6f}"
        )
    if lower_bound_margin < -BOUNDS_RELIABILITY_TOLERANCE:
        blockers.append(
            "MLB_CALIBRATOR_EVIDENCE_HOLDOUT_LOWER_BOUND_UNRELIABLE:"
            f"{lower_bound_margin:.6f}"
        )
    return tuple(blockers)


def _cohort_digest(rows: list[dict[str, Any]]) -> str:
    return canonical_sha256(
        [
            {
                "event_id": row["event_id"],
                "raw_probability": row["raw_probability"],
                "outcome": row["outcome"],
                "settled_at": row["settled_at"].isoformat(),
                "prediction_at": row["prediction_at"].isoformat(),
                "event_start_time": row["event_start_time"].isoformat(),
                "model_artifact_checksum": row["model_artifact_checksum"],
            }
            for row in rows
        ]
    )


def build_calibrator_row(
    *,
    artifact: Mapping[str, Any],
    settled_rows: Iterable[Mapping[str, Any]],
    calibrator_id: Any,
    now: Any,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Fit and assemble one evidence row, or return the typed reasons it cannot exist."""
    decision_at = _when(now)
    if decision_at is None:
        return None, ("MLB_CALIBRATOR_EVIDENCE_DECISION_TIMESTAMP_INVALID",)
    identity, blockers = _artifact_identity(artifact)
    if identity is None:
        return None, blockers
    calibrator = _text(calibrator_id)
    if not calibrator:
        return None, ("MLB_CALIBRATOR_EVIDENCE_IDENTITY_INCOMPLETE:calibrator_id",)

    rows, row_blockers = normalize_settled_rows(
        settled_rows,
        decision_at=decision_at,
        artifact_checksum=identity["model_artifact_checksum"],
    )
    if rows is None:
        return None, row_blockers

    fit, holdout = partition_settled_rows(rows)
    if len(fit) < PHASE_B_MIN_N:
        return None, (
            f"MLB_CALIBRATOR_EVIDENCE_FIT_COHORT_INSUFFICIENT:{len(fit)}<{PHASE_B_MIN_N}",
        )
    if not holdout:
        return None, ("MLB_CALIBRATOR_EVIDENCE_HOLDOUT_EMPTY",)

    fit_ids = {row["event_id"] for row in fit}
    if fit_ids & {row["event_id"] for row in holdout}:
        # Structurally unreachable through the day split; kept so a future change
        # to the split cannot silently leak the holdout into the fit.
        return None, ("MLB_CALIBRATOR_EVIDENCE_HOLDOUT_OVERLAP",)

    try:
        outcome = phase_b_platt(
            [row["raw_probability"] for row in fit],
            [row["outcome"] for row in fit],
            _folds(fit),
            [row["settled_at"].isoformat() for row in fit],
        )
    except (ValueError, TypeError, ArithmeticError) as exc:
        return None, (f"MLB_CALIBRATOR_EVIDENCE_FIT_FAILED:{type(exc).__name__}",)

    coefficients = PlattCoefficients(
        a=float(outcome.coefficients.a), b=float(outcome.coefficients.b)
    )
    if not (math.isfinite(coefficients.a) and math.isfinite(coefficients.b)):
        return None, ("MLB_CALIBRATOR_EVIDENCE_COEFFICIENTS_NON_FINITE",)
    coefficients_payload = {"a": coefficients.a, "b": coefficients.b}
    coefficients_sha = canonical_sha256(coefficients_payload)

    brier, log_loss, bias, per_row = _losses(holdout, coefficients)
    incumbent_brier, incumbent_log_loss, _, incumbent_rows = _losses(
        holdout, PlattCoefficients(a=0.0, b=1.0)
    )
    if not all(
        math.isfinite(value)
        for value in (brier, log_loss, bias, incumbent_brier, incumbent_log_loss)
    ):
        return None, ("MLB_CALIBRATOR_EVIDENCE_HOLDOUT_METRICS_INVALID",)

    lower, upper, bound_blockers = _bootstrap_bounds(fit, holdout)
    if bound_blockers:
        return None, bound_blockers
    assert lower is not None and upper is not None
    realized_rate = sum(row["outcome"] for row in holdout) / len(holdout)
    lower_bound_margin = realized_rate - (sum(lower) / len(lower))
    comparison = _comparison_blockers(brier, incumbent_brier, lower_bound_margin)
    if comparison:
        return None, comparison

    worse_n = sum(
        1
        for (_, calibrated_log), (_, incumbent_log) in zip(per_row, incumbent_rows)
        if calibrated_log > incumbent_log
    )
    cohort_sha = _cohort_digest(fit)
    holdout_sha = _cohort_digest(holdout)
    calibrator_checksum = canonical_sha256(
        {
            "calibrator_id": calibrator,
            "sport": SPORT,
            "model_family": identity["model_family"],
            "model_artifact_version": identity["model_artifact_version"],
            "feature_schema_version": identity["feature_schema_version"],
            "model_artifact_checksum": identity["model_artifact_checksum"],
            "calibration_method": CALIBRATION_METHOD,
            "bounds_method_version": BOUNDS_METHOD_VERSION,
            "calibration_coefficients_sha256": coefficients_sha,
            "calibration_training_n": len(fit),
            "holdout_n": len(holdout),
            "calibration_cohort_sha256": cohort_sha,
            "holdout_evidence_sha256": holdout_sha,
        }
    )
    fit_end = max(row["settled_at"] for row in fit)
    row = {
        "sport": SPORT,
        "model_family": identity["model_family"],
        "model_artifact_version": identity["model_artifact_version"],
        "feature_schema_version": identity["feature_schema_version"],
        "model_artifact_checksum": identity["model_artifact_checksum"],
        "calibrator_id": calibrator,
        "calibrator_checksum": calibrator_checksum,
        "calibration_method": CALIBRATION_METHOD,
        "bounds_method_version": BOUNDS_METHOD_VERSION,
        "calibration_coefficients": coefficients_payload,
        "calibration_coefficients_sha256": coefficients_sha,
        "calibration_cohort_sha256": cohort_sha,
        "holdout_evidence_sha256": holdout_sha,
        "calibration_training_n": len(fit),
        "holdout_n": len(holdout),
        "holdout_overlap_rows": 0,
        "brier": brier,
        "log_loss": log_loss,
        "calibration_bias": bias,
        "holdout_incumbent_brier": incumbent_brier,
        "holdout_incumbent_log_loss": incumbent_log_loss,
        "holdout_brier_delta": brier - incumbent_brier,
        "holdout_log_loss_delta": log_loss - incumbent_log_loss,
        "holdout_calibrated_worse_n": worse_n,
        "holdout_bounds_lower_mean": sum(lower) / len(lower),
        "holdout_bounds_upper_mean": sum(upper) / len(upper),
        "holdout_lower_bound_reliability_margin": lower_bound_margin,
        "calibration_fit_end": fit_end.isoformat(),
    }
    return row, ()


def persist_calibrator_evidence(db: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """Insert one evidence row; an existing identity is never overwritten."""
    if db is None:
        return _receipt(
            REFUSED, row=row, blockers=("MLB_CALIBRATOR_EVIDENCE_DB_UNAVAILABLE",)
        )
    try:
        existing = (
            db.table(TABLE)
            .select("*")
            .eq("calibrator_id", row["calibrator_id"])
            .limit(1)
            .execute()
        )
        stored = list(getattr(existing, "data", None) or [])
    except Exception as exc:  # noqa: BLE001 - an unreadable store is not writable
        return _receipt(
            REFUSED,
            row=row,
            blockers=(f"MLB_CALIBRATOR_EVIDENCE_READ_FAILED:{type(exc).__name__}",),
        )
    if stored:
        current = stored[0]
        same_checksum = (
            _text(current.get("calibrator_checksum")).lower()
            == _text(row["calibrator_checksum"]).lower()
        )
        same_coefficients = (
            _text(current.get("calibration_coefficients_sha256")).lower()
            == _text(row["calibration_coefficients_sha256"]).lower()
        )
        if same_checksum and same_coefficients:
            return _receipt(ALREADY_CURRENT, row=row)
        # Same identity, different fitted evidence: overwriting would erase the
        # binding the checksum is supposed to prove.
        return _receipt(
            REFUSED,
            row=row,
            blockers=(f"MLB_CALIBRATOR_EVIDENCE_IDENTITY_CONFLICT:{row['calibrator_id']}",),
        )
    try:
        db.table(TABLE).insert(dict(row)).execute()
    except Exception as exc:  # noqa: BLE001 - a failed write is a typed receipt
        return _receipt(
            WRITE_FAILED,
            row=row,
            blockers=(f"MLB_CALIBRATOR_EVIDENCE_WRITE_FAILED:{type(exc).__name__}",),
        )
    return _receipt(PERSISTED, row=row)


def produce_calibrator_evidence(
    db: Any,
    *,
    artifact: Mapping[str, Any],
    settled_rows: Iterable[Mapping[str, Any]],
    calibrator_id: Any,
    now: Any,
) -> dict[str, Any]:
    """Fit, verify and persist one exact-version MLB calibrator evidence row."""
    row, blockers = build_calibrator_row(
        artifact=artifact,
        settled_rows=settled_rows,
        calibrator_id=calibrator_id,
        now=now,
    )
    if row is None:
        return _receipt(REFUSED, blockers=blockers)
    return persist_calibrator_evidence(db, row)


__all__ = [
    "ALREADY_CURRENT",
    "BOUNDS_BOOTSTRAP_REALIZATIONS",
    "BOUNDS_METHOD_VERSION",
    "BOUNDS_RELIABILITY_TOLERANCE",
    "CALIBRATION_METHOD",
    "CAN_EXECUTE",
    "HOLDOUT_DAY_SHARE",
    "INCUMBENT_REGRESSION_TOLERANCE",
    "PERSISTED",
    "REFUSED",
    "SPORT",
    "TABLE",
    "TERMINAL_AUTHORITY",
    "WRITE_FAILED",
    "build_calibrator_row",
    "normalize_settled_rows",
    "partition_settled_rows",
    "persist_calibrator_evidence",
    "produce_calibrator_evidence",
]
