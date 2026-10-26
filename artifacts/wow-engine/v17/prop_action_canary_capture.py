"""Persist proof from real canonical ``/score-pick-request`` invocations.

The production-registration audit already requires an immutable Action canary but
intentionally cannot create one. This module closes that operational loop by
wrapping the real HTTP boundary *after* the canonical scorer returns. It writes a
receipt only when:

* exact-once reconciliation passed;
* the prediction is actually present in ``wow_predictions``;
* the persisted prediction carries a complete governed probability package;
* an independently reviewed certification release already exists for the exact
  immutable artifact; and
* the release identity/evidence hash matches exactly.

No release -> no receipt. This module never creates certification, never promotes
an artifact, never changes a probability, and never grants execution authority.
"""
from __future__ import annotations

import json
import math
from hashlib import sha256
from typing import Any, Mapping, Optional

from fastapi import Header

from v17.detailed_evidence_install import DetailedPickRequestBatch
from v17.prop_production_registration import (
    CANARY_TABLE,
    REVIEWED_CERTIFICATION_RELEASES,
    _release_key,
)

CAN_EXECUTE = False
_OPERATION_ID = "scoreWowPickRequest"
_INSTALLED_ATTR = "_wow_v17_prop_action_canary_capture_installed"


def _hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


def _finite_probability(value: Any) -> float | None:
    # Python bool is int-like: float(True)==1.0 and float(False)==0.0.
    # Neither is valid numerical probability evidence or durable readback.
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


class _PredictionStoreUnavailable(RuntimeError):
    """wow_predictions read failed; distinct from an authentically missing row."""


def _persisted_prediction(db: Any, prediction_id: str) -> dict[str, Any] | None:
    try:
        rows = (
            db.table("wow_predictions")
            .select(
                "prediction_id,sport,stat_type,feature_schema_version,model_family,"
                "model_artifact_version,model_artifact_checksum,calibration_version,"
                "raw_model_probability,calibrated_probability,"
                "calibrated_probability_lower_bound"
            )
            .eq("prediction_id", prediction_id)
            .limit(1)
            .execute().data or []
        )
    except Exception as exc:
        # Never collapse a store outage into "prediction not found".
        raise _PredictionStoreUnavailable(type(exc).__name__) from exc
    return dict(rows[0]) if rows else None


def _release_matches_prediction(release: Any, prediction: Mapping[str, Any]) -> bool:
    return (
        str(release.sport).upper() == str(prediction.get("sport") or "").upper()
        and str(release.stat_type).upper() == str(prediction.get("stat_type") or "").upper()
        and release.feature_schema_version == str(prediction.get("feature_schema_version") or "")
        and release.model_family == str(prediction.get("model_family") or "")
        and release.model_artifact_version == str(prediction.get("model_artifact_version") or "")
        and release.artifact_checksum == str(prediction.get("model_artifact_checksum") or "")
        and release.calibrator_version == str(prediction.get("calibration_version") or "")
        and release.certification_review_status == "APPROVED"
        and release.deterministic_replay_ready is True
        and release.source_provenance_ready is True
        and release.can_execute is False
    )


def _candidate_prediction_id(outcome: Mapping[str, Any]) -> str | None:
    scored = outcome.get("result")
    if not isinstance(scored, Mapping):
        return None
    prediction = scored.get("prediction")
    if not isinstance(prediction, Mapping):
        return None
    value = str(prediction.get("prediction_id") or "").strip()
    return value or None


def capture_action_canary_receipts(
    *,
    db: Any,
    batch: DetailedPickRequestBatch,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist zero or more exact-artifact canary receipts without altering result."""
    if result.get("can_execute") is not False or result.get("reconciliation_pass") is not True:
        return {
            "status": "NOT_ELIGIBLE_RECONCILIATION",
            "eligible_rows": 0,
            "persisted_rows": 0,
            "can_execute": False,
        }

    if not REVIEWED_CERTIFICATION_RELEASES:
        return {
            "status": "NO_REVIEWED_CERTIFICATION_RELEASES",
            "eligible_rows": 0,
            "persisted_rows": 0,
            "can_execute": False,
        }

    request_payload = batch.model_dump(mode="json", exclude_none=True)
    request_fingerprint = _hash({
        "operation_id": _OPERATION_ID,
        "request": request_payload,
    })
    eligible = 0
    persisted = 0
    blockers: list[str] = []

    for outcome in result.get("rows") or []:
        if not isinstance(outcome, Mapping) or outcome.get("terminal_status") != "COMPLETED":
            continue
        prediction_id = _candidate_prediction_id(outcome)
        if not prediction_id:
            blockers.append("CANARY_PREDICTION_ID_NOT_RETURNED")
            continue
        try:
            prediction = _persisted_prediction(db, prediction_id)
        except _PredictionStoreUnavailable as exc:
            blockers.append(f"CANARY_PREDICTION_STORE_UNAVAILABLE:{exc}")
            continue
        if prediction is None:
            blockers.append("CANARY_PERSISTED_PREDICTION_NOT_FOUND")
            continue
        key = _release_key(
            str(prediction.get("sport") or ""),
            str(prediction.get("stat_type") or ""),
            str(prediction.get("model_artifact_version") or ""),
            str(prediction.get("model_artifact_checksum") or ""),
        )
        release = REVIEWED_CERTIFICATION_RELEASES.get(key)
        if release is None:
            continue
        eligible += 1
        if not _release_matches_prediction(release, prediction):
            blockers.append("CANARY_REVIEWED_RELEASE_IDENTITY_MISMATCH")
            continue

        raw = _finite_probability(prediction.get("raw_model_probability"))
        calibrated = _finite_probability(prediction.get("calibrated_probability"))
        lower = _finite_probability(prediction.get("calibrated_probability_lower_bound"))
        if raw is None or calibrated is None or lower is None or lower > calibrated:
            blockers.append("CANARY_GOVERNED_PROBABILITY_PACKAGE_INVALID")
            continue

        receipt = {
            "action_operation_id": _OPERATION_ID,
            "action_request_fingerprint": request_fingerprint,
            "prediction_id": prediction_id,
            "sport": str(prediction.get("sport") or "").upper(),
            "stat_type": str(prediction.get("stat_type") or "").upper(),
            "feature_schema_version": str(prediction.get("feature_schema_version") or ""),
            "model_family": str(prediction.get("model_family") or ""),
            "model_artifact_version": str(prediction.get("model_artifact_version") or ""),
            "artifact_checksum": str(prediction.get("model_artifact_checksum") or ""),
            "calibrator_version": str(prediction.get("calibration_version") or ""),
            "certification_id": release.certification_id,
            "calibration_evidence_hash": release.calibration_evidence_hash,
            "raw_model_probability": raw,
            "calibrated_probability": calibrated,
            "calibrated_lower_bound": lower,
            "reconciliation_status": "PASS",
            "can_execute": False,
        }
        receipt["immutable_receipt_hash"] = _hash(receipt)
        try:
            db.table(CANARY_TABLE).upsert(
                receipt,
                on_conflict="immutable_receipt_hash",
            ).execute()
        except Exception as exc:
            blockers.append(f"CANARY_RECEIPT_PERSISTENCE_UNAVAILABLE:{type(exc).__name__}")
            continue

        # A successful upsert response alone is not proof that the exact receipt
        # can be fetched durably. Never count a receipt without an exact readback.
        try:
            stored_rows = (
                db.table(CANARY_TABLE)
                .select("*")
                .eq("immutable_receipt_hash", receipt["immutable_receipt_hash"])
                .limit(1)
                .execute().data or []
            )
        except Exception as exc:
            blockers.append(f"CANARY_RECEIPT_READBACK_UNAVAILABLE:{type(exc).__name__}")
            continue
        if not stored_rows:
            blockers.append("CANARY_RECEIPT_READBACK_MISSING")
            continue
        stored = stored_rows[0]
        if not isinstance(stored, Mapping) or any(
            stored.get(field) != value
            for field, value in receipt.items()
            if field not in ("raw_model_probability", "calibrated_probability", "calibrated_lower_bound")
        ) or any(
            _finite_probability(stored.get(field)) != receipt[field]
            for field in ("raw_model_probability", "calibrated_probability", "calibrated_lower_bound")
        ):
            blockers.append("CANARY_RECEIPT_READBACK_MISMATCH")
            continue
        persisted += 1

    return {
        # Empty eligible cohorts are NOT successful Action-canary acceptance.
        "status": (
            "COMPLETED_WITH_BLOCKERS" if blockers
            else "NO_ELIGIBLE_CANARY_ROWS" if eligible == 0
            else "PASS"
        ),
        "eligible_rows": eligible,
        "persisted_rows": persisted,
        "blockers": list(dict.fromkeys(blockers)),
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def install_prop_action_canary_capture(
    app: Any,
    *,
    db_client_fn: Any,
) -> bool:
    """Wrap the canonical endpoint when present; stay inert on partial test apps.

    ``install_prop_forward_cohort_route`` is also unit-tested against intentionally
    minimal FastAPI apps that do not compose the canonical scorer. Absence there
    is not a production capability claim, so the wrapper returns ``False`` rather
    than making unrelated scheduler tests fail. On the production app the route
    is already present because detailed-evidence composition runs first.
    """
    if getattr(app.state, _INSTALLED_ATTR, False):
        return True
    original_route = next(
        (
            route for route in app.router.routes
            if getattr(route, "path", None) == "/score-pick-request"
            and "POST" in (getattr(route, "methods", set()) or set())
        ),
        None,
    )
    if original_route is None:
        return False

    original_endpoint = original_route.endpoint
    dependencies = list(getattr(original_route, "dependencies", None) or [])
    app.router.routes[:] = [route for route in app.router.routes if route is not original_route]

    @app.post(
        "/score-pick-request",
        dependencies=dependencies,
        operation_id=_OPERATION_ID,
    )
    def score_pick_request_with_canary_capture(
        batch: DetailedPickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        result = original_endpoint(batch, x_wow_model_identity=x_wow_model_identity)
        if isinstance(result, dict):
            # Advisory capture must never turn a canonical scoring response into
            # an unrelated HTTP 500. Failure remains explicit, never a canary PASS.
            try:
                audit = capture_action_canary_receipts(
                    db=db_client_fn(),
                    batch=batch,
                    result=result,
                )
            except Exception as exc:
                audit = {
                    "status": "BLOCKED_CAPTURE_UNAVAILABLE",
                    "eligible_rows": 0,
                    "persisted_rows": 0,
                    "blockers": [f"CANARY_CAPTURE_UNAVAILABLE:{type(exc).__name__}"],
                    "automatic_certification": False,
                    "automatic_promotion": False,
                    "can_execute": False,
                }
            result["production_action_canary_capture"] = audit
            result["can_execute"] = False
        return result

    setattr(app.state, _INSTALLED_ATTR, True)
    return True


__all__ = [
    "CAN_EXECUTE",
    "capture_action_canary_receipts",
    "install_prop_action_canary_capture",
]
