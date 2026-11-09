"""Authenticated NFL terminal-receipt admission client (no direct row INSERT).

This adapter forwards only a persisted specialist score UUID to the server-owned
admission RPC. The database owns source, fitted model, certified calibrator,
V17_TERMINAL_REDUCER, trusted reducer-origin identity, and immutability. No caller probability,
side, model artifact, price, or self-asserted PASS enters the admission request.
Unsupported sports have no implicit fallback into the NFL route.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from uuid import UUID

CAN_EXECUTE = False
ADMIT_RPC = "wow_v17_admit_nfl_event_receipt"
VERIFY_RPC = "wow_v17_verify_multisport_event_receipt"
_UNAVAILABLE = "LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE"
_MISMATCH = "LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"
_MISSING_ID = "LLP_EVENT_PREDICTION_ID_UNVERIFIED"
_NOT_PERFORMED = "LLP_EVENT_PREDICTION_LOOKUP_NOT_PERFORMED"
_ALLOWED_HOLDS = frozenset({
    _UNAVAILABLE, _MISMATCH, _MISSING_ID, _NOT_PERFORMED,
    "LLP_EVENT_PREDICTION_NOT_EXACTLY_ONE",
    "LLP_REDUCER_ORIGIN_RECEIPT_UNAVAILABLE",
})
_DIGEST = re.compile("^[0-9a-f]{64}$")


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None
    return canonical if value.lower() == canonical else None


def _single(result: Any) -> Mapping[str, Any] | None:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    return data if isinstance(data, Mapping) else None


def _hold(code: str) -> dict[str, Any]:
    return {"status": "HELD", "code": code, "can_execute": False,
            "probability_publishable": False, "rank_eligible": False}


def admit_verified_nfl_score(db: Any, *, score_snapshot_id: str) -> dict[str, Any]:
    """Only a database-authenticated NFL fitted score may mint a receipt.

    Both calls require the separately approved server-held service role and
    independently deployed SQL functions. Unit-test RPC mocks are not proof.
    """
    score_id = _uuid(score_snapshot_id)
    if score_id is None:
        return _hold(_MISSING_ID)
    if db is None:
        return _hold(_NOT_PERFORMED)
    try:
        admission = _single(db.rpc(
            ADMIT_RPC, {"p_score_snapshot_id": score_id}
        ).execute())
    except Exception:
        return _hold(_UNAVAILABLE)
    if admission is None:
        return _hold(_UNAVAILABLE)
    if admission.get("status") == "HOLD":
        code = admission.get("code")
        return _hold(code if isinstance(code, str) and code in _ALLOWED_HOLDS
                     else _MISMATCH)
    if admission.get("status") not in {"PERSISTED", "ALREADY_PERSISTED"}:
        return _hold(_MISMATCH)
    receipt_id = _uuid(admission.get("event_prediction_id"))
    reducer_id = _uuid(admission.get("reducer_receipt_id"))
    reducer_sha = admission.get("reducer_receipt_sha256")
    if (receipt_id is None or reducer_id is None
            or _uuid(admission.get("score_snapshot_id")) != score_id
            or not isinstance(reducer_sha, str)
            or not _DIGEST.fullmatch(reducer_sha)
            or admission.get("can_execute") is not False):
        return _hold(_MISMATCH)

    # The issuer's ACK alone cannot promote a selection. A separately owned
    # readback reducer must independently bind this exact immutable receipt.
    try:
        confirmation = _single(db.rpc(
            VERIFY_RPC, {"p_event_prediction_id": receipt_id}
        ).execute())
    except Exception:
        return _hold(_UNAVAILABLE)
    if confirmation is None:
        return _hold(_UNAVAILABLE)
    if any((
        confirmation.get("status") != "PASS",
        confirmation.get("terminal_label") != "FINAL_APPROVED",
        confirmation.get("terminal_authority") != "V17_TERMINAL_REDUCER",
        confirmation.get("can_execute") is not False,
        confirmation.get("rank_eligible") is not True,
        confirmation.get("probability_publishable") is not True,
        _uuid(confirmation.get("event_prediction_id")) != receipt_id,
        _uuid(confirmation.get("score_snapshot_id")) != score_id,
        _uuid(confirmation.get("reducer_receipt_id")) != reducer_id,
        confirmation.get("reducer_receipt_sha256") != reducer_sha,
    )):
        return _hold(_MISMATCH)
    # Return identifiers only. The model's selected side/probabilities must
    # remain those of the independently persisted fitted specialist, and later
    # LLP readback repeats the exact numeric/event checks.
    return {
        "status": "RECEIPT_VERIFIED", "source_receipt_id": receipt_id,
        "score_snapshot_id": score_id, "reducer_receipt_id": reducer_id,
        "reducer_receipt_sha256": reducer_sha,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }
