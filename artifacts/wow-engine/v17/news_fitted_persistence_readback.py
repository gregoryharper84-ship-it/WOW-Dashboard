"""Authenticated persistence/terminal readback for news-triggered rescored rows.

Scorer-echoed immutable prediction ids, calibration ids, or terminal objects are
not production persistence. This module is the fail-closed surface that must
confirm those ids from an authenticated store/reducer readback before a news
rescore may set ``model_rescore_verified=true``. Synthetic test doubles may
exercise shape only; they are not independent production acceptance.
"""
from __future__ import annotations

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


def validate_news_persistence_readback(
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    persistence: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    """Return (normalized_readback, status_code, blockers).

    ``status_code`` is None when the readback authenticates the scorer's claimed
    immutable/calibration ids and a non-executing sole-reducer decision.
    """
    if not isinstance(persistence, Mapping):
        return None, "NEWS_RESCORE_PERSISTENCE_UNVERIFIED", ["NEWS_PERSISTENCE_READBACK_MISSING"]
    if persistence.get("ok") is not True or persistence.get("authenticated") is not True:
        return None, "NEWS_RESCORE_PERSISTENCE_UNVERIFIED", [
            str(persistence.get("code") or "NEWS_PERSISTENCE_UNAUTHENTICATED"),
            *list(persistence.get("blockers") or []),
        ]

    claimed_immutable = str(response.get("immutable_prediction_id") or "").strip()
    claimed_prior = response.get("prior_immutable_prediction_id")
    claimed_prior_s = str(claimed_prior).strip() if claimed_prior is not None else None
    if claimed_prior_s == "":
        claimed_prior_s = None
    claimed_calibration = str(response.get("calibration_receipt_id") or "").strip()

    store_immutable = str(persistence.get("immutable_prediction_id") or "").strip()
    store_prior = persistence.get("prior_immutable_prediction_id")
    store_prior_s = str(store_prior).strip() if store_prior is not None else None
    if store_prior_s == "":
        store_prior_s = None
    store_calibration = str(persistence.get("calibration_receipt_id") or "").strip()

    if not claimed_immutable or not store_immutable:
        return None, "NEWS_RESCORE_IMMUTABLE_PREDICTION_MISSING", ["IMMUTABLE_PREDICTION_ID_MISSING"]
    if store_immutable != claimed_immutable:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["IMMUTABLE_PREDICTION_ID_MISMATCH"]
    if not claimed_calibration or not store_calibration:
        return None, "NEWS_RESCORE_CALIBRATION_RECEIPT_MISSING", ["CALIBRATION_RECEIPT_ID_MISSING"]
    if store_calibration != claimed_calibration:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["CALIBRATION_RECEIPT_ID_MISMATCH"]

    if claimed_prior_s is not None:
        if store_prior_s != claimed_prior_s:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED", ["PRIOR_IMMUTABLE_ID_NOT_IN_STORE"]
        if persistence.get("prior_immutable_prediction_preserved") is not True:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_NOT_PRESERVED", ["PRIOR_IMMUTABLE_NOT_PRESERVED"]
        if store_prior_s == store_immutable:
            return None, "NEWS_RESCORE_PRIOR_RECEIPT_COLLISION", ["PRIOR_IMMUTABLE_ID_REUSED"]

    terminal = persistence.get("terminal_readback")
    if not isinstance(terminal, Mapping):
        return None, "NEWS_RESCORE_TERMINAL_READBACK_MISSING", ["TERMINAL_READBACK_MISSING"]
    if str(terminal.get("authority") or "").strip() != TERMINAL_AUTHORITY:
        return None, "NEWS_RESCORE_TERMINAL_READBACK_INVALID", ["TERMINAL_AUTHORITY_INVALID"]
    if not str(terminal.get("status") or "").strip():
        return None, "NEWS_RESCORE_TERMINAL_READBACK_MISSING", ["TERMINAL_STATUS_MISSING"]
    if terminal.get("can_execute") is True:
        return None, "NEWS_RESCORE_AUTHORITY_REJECTED", ["TERMINAL_CLAIMED_EXECUTION"]

    attestation = persistence.get("store_attestation")
    if not isinstance(attestation, Mapping) or not str(attestation.get("source") or "").strip():
        return None, "NEWS_RESCORE_PERSISTENCE_UNVERIFIED", ["STORE_ATTESTATION_MISSING"]

    # Scorer-claimed sport/event must match the store row when provided.
    store_event = str(persistence.get("official_event_id") or "").strip()
    req_event = str(request.get("official_event_id") or "").strip()
    if store_event and req_event and store_event != req_event:
        return None, "NEWS_RESCORE_PERSISTENCE_MISMATCH", ["OFFICIAL_EVENT_ID_MISMATCH"]

    normalized = {
        "ok": True,
        "authenticated": True,
        "immutable_prediction_id": store_immutable,
        "prior_immutable_prediction_id": store_prior_s,
        "prior_immutable_prediction_preserved": (
            True if claimed_prior_s is None else True
        ),
        "calibration_receipt_id": store_calibration,
        "terminal_readback": {
            "authority": TERMINAL_AUTHORITY,
            "status": str(terminal.get("status")).strip(),
            "can_execute": False,
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
