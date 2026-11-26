"""Authenticated persistence/terminal readback for news-triggered rescored rows.

Scorer-echoed immutable prediction ids, calibration ids, or terminal objects are
not production persistence. This module is the fail-closed surface that must
confirm those ids from an authenticated store/reducer readback before a news
rescore may set ``model_rescore_verified=true``. Synthetic test doubles may
exercise shape only; they are not independent production acceptance.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PersistenceFn = Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]


def _unavailable(*, blockers: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "authenticated": False,
        "code": "NEWS_PERSISTENCE_READBACK_UNAVAILABLE",
        "blockers": list(blockers),
        "immutable_prediction_id": None,
        "prior_immutable_prediction_id": None,
        "prior_immutable_prediction_preserved": False,
        "calibration_receipt_id": None,
        "terminal_readback": None,
        "store_attestation": None,
        "prediction_authority": False,
        "can_execute": False,
    }


def missing_news_persistence_readback(
    _request: Mapping[str, Any],
    _response: Mapping[str, Any],
) -> dict[str, Any]:
    """Default production binding until a store-backed adapter is registered."""
    return _unavailable(blockers=["NEWS_PERSISTENCE_STORE_NOT_BOUND"])


def _text(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _finite_open_unit(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 < number < 1.0:
        return None
    return number


def _same_mapping(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return False
    return dict(left) == dict(right)


def validate_news_persistence_readback(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    persistence: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Return (normalized_readback, status_code, blockers).

    Prior identity is bound from the authoritative store record, not from an
    optional scorer assertion. Exact event/route/market/feature/model/
    calibration/probability linkage must match the trusted readback.
    """
    if not isinstance(persistence, Mapping):
        return None, "NEWS_RESCORE_PERSISTENCE_UNVERIFIED", ["NEWS_PERSISTENCE_READBACK_MISSING"]
    if persistence.get("ok") is not True or persistence.get("authenticated") is not True:
        return None, "NEWS_RESCORE_PERSISTENCE_UNVERIFIED", [
            str(persistence.get("code") or "NEWS_PERSISTENCE_UNAUTHENTICATED"),
            *list(persistence.get("blockers") or []),
        ]

    claimed_immutable = _text(response.get("immutable_prediction_id"))
    claimed_prior = _text(response.get("prior_immutable_prediction_id"))
    claimed_calibration = _text(response.get("calibration_receipt_id"))

    store_immutable = _text(persistence.get("immutable_prediction_id"))
    store_prior = _text(persistence.get("prior_immutable_prediction_id"))
    store_calibration = _text(persistence.get("calibration_receipt_id"))

    if not claimed_immutable or not store_immutable:
        return None, "NEWS_RESCORE_IMMUTABLE_PREDICTION_MISSING", ["IMMUTABLE_PREDICTION_ID_MISSING"]
    if store_immutable != claimed_immutable:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["IMMUTABLE_PREDICTION_ID_MISMATCH"]
    if not claimed_calibration or not store_calibration:
        return None, "NEWS_RESCORE_CALIBRATION_RECEIPT_MISSING", ["CALIBRATION_RECEIPT_ID_MISSING"]
    if store_calibration != claimed_calibration:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["CALIBRATION_RECEIPT_ID_MISMATCH"]

    # Authoritative prior comes from the original immutable store record (and/or
    # the request's known prior). Scorer omission is not allowed when a prior exists.
    request_prior = _text(request.get("prior_immutable_prediction_id"))
    authoritative_prior = store_prior or request_prior
    if authoritative_prior is not None:
        if store_prior is None:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED", ["PRIOR_IMMUTABLE_ID_NOT_IN_STORE"]
        if claimed_prior is None:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED", ["PRIOR_IMMUTABLE_ID_OMITTED_BY_SCORER"]
        if claimed_prior != store_prior:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED", ["PRIOR_IMMUTABLE_ID_MISMATCH"]
        if request_prior is not None and request_prior != store_prior:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED", ["REQUEST_PRIOR_IMMUTABLE_ID_MISMATCH"]
        if persistence.get("prior_immutable_prediction_preserved") is not True:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED", ["PRIOR_IMMUTABLE_NOT_PRESERVED"]
        if store_prior == store_immutable:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_COLLISION", ["PRIOR_IMMUTABLE_ID_REUSED"]

    req_event = _text(request.get("official_event_id"))
    store_event = _text(persistence.get("official_event_id"))
    if not req_event:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["REQUEST_OFFICIAL_EVENT_ID_MISSING"]
    if not store_event:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["STORE_OFFICIAL_EVENT_ID_MISSING"]
    if store_event != req_event:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["OFFICIAL_EVENT_ID_MISMATCH"]

    req_route = _text(request.get("controlling_specialist_route"))
    store_route = _text(persistence.get("controlling_specialist_route"))
    if not req_route or not store_route or store_route != req_route:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["CONTROLLING_ROUTE_STORE_MISMATCH"]

    req_feature = _text(request.get("feature_snapshot_hash"))
    resp_feature = _text(response.get("feature_snapshot_hash"))
    store_feature = _text(persistence.get("feature_snapshot_hash"))
    if not req_feature or not store_feature or store_feature != req_feature:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["FEATURE_HASH_STORE_MISMATCH"]
    if resp_feature is not None and resp_feature != store_feature:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["FEATURE_HASH_RESPONSE_STORE_MISMATCH"]

    resp_model = _text(response.get("model_version"))
    store_model = _text(persistence.get("model_version") or persistence.get("fitted_model_version"))
    if not resp_model or not store_model or store_model != resp_model:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["MODEL_VERSION_STORE_MISMATCH"]

    resp_p = _finite_open_unit(response.get("calibrated_probability"))
    resp_lb = _finite_open_unit(response.get("calibrated_lower_bound"))
    store_p = _finite_open_unit(persistence.get("calibrated_probability"))
    store_lb = _finite_open_unit(persistence.get("calibrated_lower_bound"))
    if resp_p is None or store_p is None or store_p != resp_p:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["CALIBRATED_PROBABILITY_STORE_MISMATCH"]
    if resp_lb is None or store_lb is None or store_lb != resp_lb:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["CALIBRATED_LOWER_BOUND_STORE_MISMATCH"]
    if store_lb > store_p:
        return None, "NEWS_RESCORE_LOWER_BOUND_ORDER_INVALID", [
            "CALIBRATED_LOWER_BOUND_EXCEEDS_PROBABILITY"
        ]

    req_prop = request.get("prop_target")
    store_prop = persistence.get("prop_target")
    if req_prop is not None or store_prop is not None:
        if not _same_mapping(req_prop, store_prop):
            return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["PROP_TARGET_STORE_MISMATCH"]

    terminal = persistence.get("terminal_readback")
    if not isinstance(terminal, Mapping):
        return None, "NEWS_RESCORE_TERMINAL_READBACK_MISSING", ["TERMINAL_READBACK_MISSING"]
    if str(terminal.get("authority") or "").strip() != TERMINAL_AUTHORITY:
        return None, "NEWS_RESCORE_TERMINAL_READBACK_INVALID", ["TERMINAL_AUTHORITY_INVALID"]
    if not str(terminal.get("status") or "").strip():
        return None, "NEWS_RESCORE_TERMINAL_READBACK_MISSING", ["TERMINAL_STATUS_MISSING"]
    if terminal.get("can_execute") is not False:
        return None, "NEWS_RESCORE_TERMINAL_READBACK_INVALID", ["TERMINAL_CAN_EXECUTE_NOT_FALSE"]

    # Optional stronger linkage when the store names the prediction/calibration.
    terminal_pred = _text(terminal.get("immutable_prediction_id"))
    if terminal_pred is not None and terminal_pred != store_immutable:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["TERMINAL_PREDICTION_ID_MISMATCH"]
    terminal_cal = _text(terminal.get("calibration_receipt_id"))
    if terminal_cal is not None and terminal_cal != store_calibration:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["TERMINAL_CALIBRATION_ID_MISMATCH"]
    terminal_feature = _text(terminal.get("feature_snapshot_hash"))
    if terminal_feature is not None and terminal_feature != store_feature:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["TERMINAL_FEATURE_HASH_MISMATCH"]

    attestation = persistence.get("store_attestation")
    if not isinstance(attestation, Mapping) or not str(attestation.get("source") or "").strip():
        return None, "NEWS_RESCORE_PERSISTENCE_UNVERIFIED", ["STORE_ATTESTATION_MISSING"]

    normalized = {
        "ok": True,
        "authenticated": True,
        "immutable_prediction_id": store_immutable,
        # Prior is always store-authoritative when present.
        "prior_immutable_prediction_id": store_prior,
        "prior_immutable_prediction_preserved": store_prior is not None,
        "calibration_receipt_id": store_calibration,
        "terminal_readback": {
            "authority": TERMINAL_AUTHORITY,
            "status": str(terminal.get("status")).strip(),
            "can_execute": False,
            "immutable_prediction_id": store_immutable,
            "calibration_receipt_id": store_calibration,
            "feature_snapshot_hash": store_feature,
        },
        "store_attestation": {
            "source": str(attestation.get("source")).strip(),
            "read_at_utc": attestation.get("read_at_utc"),
        },
        "prediction_authority": False,
        "can_execute": False,
    }
    return normalized, None, []


__all__ = [
    "CAN_EXECUTE",
    "TERMINAL_AUTHORITY",
    "missing_news_persistence_readback",
    "validate_news_persistence_readback",
]
