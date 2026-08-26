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

from calibration import PlattCoefficients, PlattFitMetrics, CalibrationStatus
from ledger import get_client


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
        "calibration_method": CalibrationStatus.PLATT_TIME_SPLIT_V1,
        "calibration_version": calibration_version,
        "parent_cohort": parent_cohort,
        "training_n": training_n,
        "platt_a": coefficients.a,
        "platt_b": coefficients.b,
        "fit_metrics_json": asdict(metrics),
        "fold_train_audit_json": fold_train_audit,
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
        "calibration_method": CalibrationStatus.ISOTONIC_V1,
        "calibration_version": calibration_version,
        "parent_cohort": parent_cohort,
        "training_n": training_n,
        "isotonic_artifact_b64": _serialize_isotonic_model(model),
        "fit_metrics_json": asdict(metrics),
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
