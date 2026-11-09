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

* the split is fixed by this contract, not by a caller: the last quarter of the
  distinct settlement days is the holdout and the earlier days are the fit
  cohort. There is no knob to move the split until a fit passes;
* the fit reuses the ratified Phase B walk-forward implementation, which
  verifies its own fold chronology from the settlement timestamps;
* duplicate settled identities are a conflict, so a row can never be counted
  twice or appear in both cohorts;
* ``calibrator_checksum``, ``calibration_cohort_sha256`` and
  ``holdout_evidence_sha256`` are digests of the committed coefficients and of
  the ordered rows actually used — none is accepted from a caller;
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

from calibration import (
    PHASE_B_MIN_N,
    PREDICTIVE_BOUNDS_METHOD_VERSION,
    CalibrationStatus,
    phase_b_platt,
)

from v17.mlb_pregame_evidence_ingest import canonical_sha256

TABLE = "wow_mlb_event_calibrator_evidence"
SPORT = "MLB"
CALIBRATION_METHOD = CalibrationStatus.PLATT_TIME_SPLIT_V1
BOUNDS_METHOD_VERSION = PREDICTIVE_BOUNDS_METHOD_VERSION
CERTIFIED_LIFECYCLES = ("PROSPECTIVE_CERTIFIED", "CHAMPION")
# Fixed contract, not a parameter: the holdout is the final quarter of settlement
# days. A caller-tunable split would let the same cohort be re-split until it fit.
HOLDOUT_DAY_SHARE = 0.25
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
        "model_artifact_version": row.get("model_artifact_version"),
        "feature_schema_version": row.get("feature_schema_version"),
        "calibrator_checksum": row.get("calibrator_checksum"),
        "calibration_cohort_sha256": row.get("calibration_cohort_sha256"),
        "holdout_evidence_sha256": row.get("holdout_evidence_sha256"),
        "calibration_training_n": row.get("calibration_training_n"),
        "holdout_overlap_rows": row.get("holdout_overlap_rows"),
        "blockers": list(blockers),
        "probability_written": False,
        "verification_flag_written": False,
        "can_execute": CAN_EXECUTE,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def _artifact_identity(artifact: Mapping[str, Any]) -> tuple[dict[str, str] | None, tuple[str, ...]]:
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
    if identity["model_artifact_checksum"] and not _sha256(identity["model_artifact_checksum"]):
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
    settled_rows: Iterable[Mapping[str, Any]], *, decision_at: datetime
) -> tuple[list[dict[str, Any]] | None, tuple[str, ...]]:
    """Validate and order settled observations; refuse anything ambiguous."""
    blockers: list[str] = []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
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
        if probability is None or not 0.0 < probability < 1.0:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_RAW_PROBABILITY_INVALID:{event_id}")
            continue
        if outcome is not True and outcome is not False and outcome not in (0, 1):
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_OUTCOME_INVALID:{event_id}")
            continue
        if settled_at is None:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_SETTLEMENT_TIMESTAMP_INVALID:{event_id}")
            continue
        if settled_at > decision_at:
            blockers.append(f"MLB_CALIBRATOR_EVIDENCE_NOT_SETTLED_AT_DECISION:{event_id}")
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
            }
        )
    if blockers:
        return None, tuple(dict.fromkeys(blockers))
    rows.sort(key=lambda row: (row["settled_at"], row["event_id"]))
    return rows, ()


def partition_settled_rows(
    rows: list[dict[str, Any]]
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


def _holdout_metrics(
    raw_probabilities: list[float], outcomes: list[int], *, a: float, b: float
) -> tuple[float, float, float] | None:
    probs: list[float] = []
    for probability in raw_probabilities:
        logit = math.log(probability / (1.0 - probability))
        value = a + b * logit
        calibrated = 1.0 / (1.0 + math.exp(-value)) if value >= 0 else math.exp(value) / (
            1.0 + math.exp(value)
        )
        probs.append(min(max(calibrated, 1e-9), 1.0 - 1e-9))
    n = len(probs)
    brier = sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n
    log_loss = -sum(
        y * math.log(p) + (1 - y) * math.log(1.0 - p)
        for p, y in zip(probs, outcomes)
    ) / n
    bias = sum(probs) / n - sum(outcomes) / n
    if not all(math.isfinite(value) for value in (brier, log_loss, bias)):
        return None
    return brier, log_loss, bias


def _cohort_digest(rows: list[dict[str, Any]]) -> str:
    return canonical_sha256(
        [
            {
                "event_id": row["event_id"],
                "raw_probability": row["raw_probability"],
                "outcome": row["outcome"],
                "settled_at": row["settled_at"].isoformat(),
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

    rows, row_blockers = normalize_settled_rows(settled_rows, decision_at=decision_at)
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

    a = float(outcome.coefficients.a)
    b = float(outcome.coefficients.b)
    metrics = _holdout_metrics(
        [row["raw_probability"] for row in holdout],
        [row["outcome"] for row in holdout],
        a=a,
        b=b,
    )
    if metrics is None:
        return None, ("MLB_CALIBRATOR_EVIDENCE_HOLDOUT_METRICS_INVALID",)
    brier, log_loss, bias = metrics

    cohort_sha = _cohort_digest(fit)
    holdout_sha = _cohort_digest(holdout)
    calibrator_checksum = canonical_sha256(
        {
            "calibration_method": CALIBRATION_METHOD,
            "bounds_method_version": BOUNDS_METHOD_VERSION,
            "platt_a": a,
            "platt_b": b,
            "calibration_training_n": len(fit),
            "calibration_cohort_sha256": cohort_sha,
            "model_artifact_version": identity["model_artifact_version"],
            "feature_schema_version": identity["feature_schema_version"],
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
        "calibration_cohort_sha256": cohort_sha,
        "holdout_evidence_sha256": holdout_sha,
        "calibration_training_n": len(fit),
        "holdout_overlap_rows": 0,
        "brier": brier,
        "log_loss": log_loss,
        "calibration_bias": bias,
        "calibration_fit_end": fit_end.isoformat(),
    }
    return row, ()


def persist_calibrator_evidence(db: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """Insert one evidence row, replaying idempotently on its own checksum."""
    if db is None:
        return _receipt(
            REFUSED, row=row, blockers=("MLB_CALIBRATOR_EVIDENCE_DB_UNAVAILABLE",)
        )
    try:
        existing = (
            db.table(TABLE)
            .select("*")
            .eq("calibrator_checksum", row["calibrator_checksum"])
            .limit(1)
            .execute()
        )
        if list(getattr(existing, "data", None) or []):
            return _receipt(ALREADY_CURRENT, row=row)
    except Exception as exc:  # noqa: BLE001 - an unreadable store is not writable
        return _receipt(
            REFUSED,
            row=row,
            blockers=(f"MLB_CALIBRATOR_EVIDENCE_READ_FAILED:{type(exc).__name__}",),
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
    "BOUNDS_METHOD_VERSION",
    "CALIBRATION_METHOD",
    "CAN_EXECUTE",
    "HOLDOUT_DAY_SHARE",
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
