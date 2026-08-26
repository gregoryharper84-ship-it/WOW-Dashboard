"""
calibrator_store.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.4

Persists fitted Phase B (Platt) / Phase C (isotonic) calibrator artifacts
to the wow_calibrators table (schema.sql) so `a`, `b`, the isotonic model,
fit cohort/version, training window, and fit metrics survive service
restarts. Returning a PlattFitOutcome/IsotonicFitOutcome from
calibration.py is not persistence by itself -- this module is what makes
it persistence.

The serialize/deserialize helpers are pure functions (no I/O) so the
artifact round-trip can be tested without a live Supabase instance; only
save_*/load_active_calibrator touch the network, via ledger.get_client().
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Optional
import base64
import pickle
import uuid

from calibration import (
    PlattCoefficients, PlattFitMetrics, CalibrationStatus,
    HistoricalCalibrationRow, PREDICTIVE_BOUNDS_METHOD_VERSION,
)
from ledger import get_client

_PHASE_BY_METHOD = {
    CalibrationStatus.PLATT_TIME_SPLIT_V1: "PHASE_B",
    CalibrationStatus.ISOTONIC_V1: "PHASE_C",
}


def _serialize_isotonic_model(model) -> str:
    # Trusted-artifact store only: this pickles/unpickles models WOW's own
    # fitting pipeline just produced and immediately persists, never
    # attacker-supplied bytes -- not an untrusted-deserialization path.
    return base64.b64encode(pickle.dumps(model)).decode("ascii")


def _deserialize_isotonic_model(artifact_b64: str):
    return pickle.loads(base64.b64decode(artifact_b64))


def platt_coefficients_from_record(record: dict) -> PlattCoefficients:
    return PlattCoefficients(a=record["platt_a"], b=record["platt_b"])


def isotonic_model_from_record(record: dict):
    return _deserialize_isotonic_model(record["isotonic_artifact_b64"])


def _deactivate_existing(client, parent_cohort: str, calibration_method: str) -> None:
    client.table("wow_calibrators").update({"active": False}).eq(
        "parent_cohort", parent_cohort
    ).eq("calibration_method", calibration_method).eq("active", True).execute()


def save_platt_calibrator(
    coefficients: PlattCoefficients,
    metrics: PlattFitMetrics,
    parent_cohort: str,
    calibration_version: str,
    training_n: int,
    fold_train_audit: Optional[dict] = None,
    activate: bool = True,
) -> dict:
    client = get_client()
    payload = {
        "calibrator_id": str(uuid.uuid4()),
        "phase": _PHASE_BY_METHOD[CalibrationStatus.PLATT_TIME_SPLIT_V1],
        "calibration_method": CalibrationStatus.PLATT_TIME_SPLIT_V1,
        "calibration_version": calibration_version,
        "parent_cohort": parent_cohort,
        "training_n": training_n,
        "platt_a": coefficients.a,
        "platt_b": coefficients.b,
        "fit_metrics_json": asdict(metrics),
        "fold_train_audit_json": fold_train_audit,
        "bounds_method_version": PREDICTIVE_BOUNDS_METHOD_VERSION,
        "promoted": True,
        "active": activate,
    }
    if activate:
        _deactivate_existing(client, parent_cohort, CalibrationStatus.PLATT_TIME_SPLIT_V1)
    result = client.table("wow_calibrators").insert(payload).execute()
    return result.data[0] if result.data else payload


def save_isotonic_calibrator(
    model,
    metrics: PlattFitMetrics,
    parent_cohort: str,
    calibration_version: str,
    training_n: int,
    activate: bool = True,
) -> dict:
    client = get_client()
    payload = {
        "calibrator_id": str(uuid.uuid4()),
        "phase": _PHASE_BY_METHOD[CalibrationStatus.ISOTONIC_V1],
        "calibration_method": CalibrationStatus.ISOTONIC_V1,
        "calibration_version": calibration_version,
        "parent_cohort": parent_cohort,
        "training_n": training_n,
        "isotonic_artifact_b64": _serialize_isotonic_model(model),
        "fit_metrics_json": asdict(metrics),
        "bounds_method_version": PREDICTIVE_BOUNDS_METHOD_VERSION,
        "promoted": True,
        "active": activate,
    }
    if activate:
        _deactivate_existing(client, parent_cohort, CalibrationStatus.ISOTONIC_V1)
    result = client.table("wow_calibrators").insert(payload).execute()
    return result.data[0] if result.data else payload


def load_active_calibrator(parent_cohort: str, calibration_method: str) -> Optional[dict]:
    """Returns the active wow_calibrators row for this (cohort, method), or
    None if no calibrator has been promoted for it yet -- callers must
    treat that as a data gap (fall back to an earlier phase / block), not
    silently substitute an untrained calibrator."""
    client = get_client()
    result = (
        client.table("wow_calibrators")
        .select("*")
        .eq("parent_cohort", parent_cohort)
        .eq("calibration_method", calibration_method)
        .eq("active", True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def load_historical_calibration_rows(
    parent_cohort: str, calibration_method: str
) -> list[HistoricalCalibrationRow]:
    """Loads verified settled (raw_probability, outcome, timestamp) rows
    for this cohort -- the historical calibration cohort input to
    calibration.compute_predictive_bounds() (PREDICTIVE_BOUNDS_V1).

    Joins wow_predictions to wow_outcomes with two plain queries and a
    Python-side merge, rather than a single Supabase embedded-resource
    query -- the embedded-select syntax can't be verified against a live
    project from this sandbox (see README), and a wrong join shape here
    would fail loudly (KeyError) rather than silently returning the wrong
    rows, but it's easy to get subtly wrong without a live test. A
    two-query fetch is easy to verify correct by inspection alone."""
    client = get_client()
    predictions = (
        client.table("wow_predictions")
        .select("prediction_id, raw_model_probability, event_start_time")
        .eq("calibration_parent_cohort", parent_cohort)
        .eq("calibration_method", calibration_method)
        .execute()
    ).data or []
    if not predictions:
        return []

    prediction_ids = [p["prediction_id"] for p in predictions]
    outcomes = (
        client.table("wow_outcomes")
        .select("prediction_id, hit")
        .in_("prediction_id", prediction_ids)
        .execute()
    ).data or []
    hit_by_prediction = {o["prediction_id"]: o["hit"] for o in outcomes if o["hit"] is not None}

    rows: list[HistoricalCalibrationRow] = []
    for p in predictions:
        hit = hit_by_prediction.get(p["prediction_id"])
        if hit is None or p["raw_model_probability"] is None:
            continue
        rows.append(HistoricalCalibrationRow(
            raw_probability=p["raw_model_probability"],
            outcome=int(bool(hit)),
            timestamp=p["event_start_time"],
        ))
    return rows
