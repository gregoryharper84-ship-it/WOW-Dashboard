"""Rundown consumption bookkeeping and transactional paid-call reservations.

Rundown bills per returned price ("data point"). Before this guard, WOW had a
per-scan call cap but nothing bounding daily data points across all callers,
and the monthly allowance was exhausted in September 2026.

- Limits (per UTC day): ``WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET`` (default 6000,
  ~180k/month, under the 200k free-plan cap) and ``WOW_RUNDOWN_DAILY_CALL_BUDGET``
  (default 300; backstop when a response lacks ``X-Datapoints``). ``OFF``
  disables a limit.
- Usage is read from the provider's ``X-Datapoints`` response header.
- Totals persist durably in ``wow_market_feed_sync_state`` (feed key
  ``RUNDOWN:DATAPOINT_BUDGET:<UTC date>``) when a DB client is registered, so
  restarts and other workers see them. Concurrent workers may overshoot by a
  few calls (read-modify-write); the call cap bounds that drift.
- When exhausted, requests fail closed with the existing typed
  ``PAID_PROVIDER_BUDGET_EXHAUSTED``; no network call is made.

Cost control only: never creates, alters or substitutes a probability.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

CAN_EXECUTE = False
BLOCK_CODE = "PAID_PROVIDER_BUDGET_EXHAUSTED"
SYNC_TABLE = "wow_market_feed_sync_state"
LOGGER = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict[str, Any] = {"day": None, "datapoints": 0, "calls": 0, "loaded": False}
_client_fn: Callable[[], Any] | None = None


def _limit(name: str, default: int) -> int | None:
    raw = os.environ.get(name, str(default)).strip()
    if raw.upper() == "OFF":
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def datapoint_limit() -> int | None:
    return _limit("WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET", 6000)


def call_limit() -> int | None:
    return _limit("WOW_RUNDOWN_DAILY_CALL_BUDGET", 300)


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


def feed_key(day: str) -> str:
    return f"RUNDOWN:DATAPOINT_BUDGET:{day}"


def register_client(client_fn: Callable[[], Any] | None) -> None:
    """Enable durable totals (called once at startup)."""
    global _client_fn
    _client_fn = client_fn if callable(client_fn) else None


def _read_persisted(day: str) -> tuple[int, int]:
    if _client_fn is None:
        return 0, 0
    try:
        response = (
            _client_fn().table(SYNC_TABLE).select("metadata").eq("feed_key", feed_key(day)).limit(1).execute()
        )
        rows = getattr(response, "data", None) or []
        meta = rows[0].get("metadata") if rows and isinstance(rows[0], dict) else {}
        meta = meta if isinstance(meta, dict) else {}
        return int(meta.get("datapoints") or 0), int(meta.get("calls") or 0)
    except Exception:  # noqa: BLE001 - budget bookkeeping must never raise
        return 0, 0


def _persist(day: str, datapoints: int, calls: int) -> None:
    if _client_fn is None:
        return
    try:
        _client_fn().table(SYNC_TABLE).upsert(
            {
                "feed_key": feed_key(day),
                "sport_key": "ALL",
                "slate_date": day,
                "market_ids": [],
                "affiliate_ids": [],
                "acquisition_mode": "BUDGET",
                "metadata": {
                    "datapoints": datapoints,
                    "calls": calls,
                    "datapoint_limit": datapoint_limit(),
                    "call_limit": call_limit(),
                },
                "can_execute": False,
            },
            on_conflict="feed_key",
        ).execute()
    except Exception:  # noqa: BLE001
        LOGGER.warning("RUNDOWN_DATAPOINT_BUDGET=PERSIST_FAIL day=%s can_execute=false", day)


def _roll(now: datetime | None = None) -> str:
    day = _today(now)
    if _state["day"] != day or not _state["loaded"]:
        datapoints, calls = _read_persisted(day)
        _state.update(day=day, datapoints=datapoints, calls=calls, loaded=True)
    return day


def check(now: datetime | None = None) -> tuple[bool, str | None]:
    """Whether one more Rundown request may be made today."""
    with _lock:
        _roll(now)
        dp_limit, c_limit = datapoint_limit(), call_limit()
        if dp_limit is not None and _state["datapoints"] >= dp_limit:
            return False, BLOCK_CODE
        if c_limit is not None and _state["calls"] >= c_limit:
            return False, BLOCK_CODE
        return True, None


def record(datapoints: Any, now: datetime | None = None) -> dict[str, Any]:
    """Record one completed request and its reported data points."""
    try:
        used = max(0, int(datapoints)) if datapoints is not None else 0
    except (TypeError, ValueError):
        used = 0
    with _lock:
        day = _roll(now)
        # Re-read the durable total so other workers' usage is included.
        persisted_dp, persisted_calls = _read_persisted(day) if _client_fn else (_state["datapoints"], _state["calls"])
        _state["datapoints"] = max(_state["datapoints"], persisted_dp) + used
        _state["calls"] = max(_state["calls"], persisted_calls) + 1
        snapshot = dict(_state)
        _persist(day, snapshot["datapoints"], snapshot["calls"])
    if datapoints is None:
        LOGGER.info("RUNDOWN_DATAPOINT_BUDGET=UNMETERED_CALL calls=%s can_execute=false", snapshot["calls"])
    return snapshot


def header_datapoints(headers: Any) -> int | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for name in ("X-Datapoints", "x-datapoints"):
        value = getter(name)
        if value is not None:
            try:
                return max(0, int(str(value).strip()))
            except ValueError:
                return None
    return None


def status(now: datetime | None = None) -> dict[str, Any]:
    with _lock:
        day = _roll(now)
        return {
            "day": day,
            "datapoints": _state["datapoints"],
            "calls": _state["calls"],
            "datapoint_limit": datapoint_limit(),
            "call_limit": call_limit(),
            "can_execute": False,
        }


def _reset_for_tests() -> None:
    global _client_fn
    with _lock:
        _state.update(day=None, datapoints=0, calls=0, loaded=False)
        _client_fn = None

# Every production metered HTTP attempt MUST use these two RPC-backed methods
# instead of legacy check()/record(): the old counters are informative only.
# Cross-worker correctness comes solely from SQL FOR UPDATE in migration
# 20261009_v17_rundown_atomic_budget.sql. Without that migration, fail closed.
STATE_UNAVAILABLE = "PAID_PROVIDER_BUDGET_STATE_UNAVAILABLE"
FINISH_FAILED = "PAID_PROVIDER_BUDGET_RECONCILIATION_FAILED"


def _rpc_payload(result: Any) -> dict[str, Any]:
    payload = getattr(result, "data", None)
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    if not isinstance(payload, dict):
        raise ValueError("RUNDOWN_BUDGET_RPC_RESPONSE_INVALID")
    return payload


def reserve_call(now: datetime | None = None) -> tuple[bool, str, str | None]:
    """Atomically count a single future paid request, globally across workers.

    One in-flight request at a time; an unknown charge or lost finish record
    requires reconciliation before ANY further paid request. This is a hard
    *call* budget but an observed, post-response data-point ceiling; per-call
    data points are not known upfront, so never claim a hard DP ceiling.
    """
    if _client_fn is None:
        return False, STATE_UNAVAILABLE, None
    request_id = uuid4().hex
    day = _today(now)
    try:
        payload = _rpc_payload(_client_fn().rpc("wow_rundown_reserve_call", {
            "p_request_id": request_id,
            "p_utc_day": day,
            "p_call_limit": call_limit() if call_limit() is not None else -1,
            "p_point_limit": datapoint_limit() if datapoint_limit() is not None else -1,
        }).execute())
    except Exception:  # No DB, no migration, wrong role, malformed result: no paid call.
        LOGGER.exception("RUNDOWN_BUDGET_RESERVE_FAILED can_execute=false")
        return False, STATE_UNAVAILABLE, None
    if payload.get("allowed") is True and payload.get("code") == "PAID_PROVIDER_CALL_RESERVED":
        return True, "PAID_PROVIDER_CALL_RESERVED", request_id
    return False, str(payload.get("code") or STATE_UNAVAILABLE), None


def finish_call(request_id: str | None, datapoints: Any) -> tuple[bool, str]:
    """Settle a reserved request exactly once. Missing usage never becomes zero."""
    if not request_id or _client_fn is None:
        return False, FINISH_FAILED
    points = None
    if datapoints is not None:
        try:
            parsed = int(datapoints)
            if not isinstance(datapoints, bool) and parsed >= 0:
                points = parsed
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        payload = _rpc_payload(_client_fn().rpc("wow_rundown_finish_call", {
            "p_request_id": request_id, "p_datapoints": points,
        }).execute())
    except Exception:
        LOGGER.exception("RUNDOWN_BUDGET_FINISH_FAILED can_execute=false")
        return False, FINISH_FAILED
    if payload.get("ok") is not True:
        return False, str(payload.get("code") or FINISH_FAILED)
    # Successful settlement with unknown charge still blocks the next
    # reservation, by the DB's unknown_usage fail-closed gate.
    return True, str(payload.get("code") or "PAID_PROVIDER_CALL_SETTLED")


__all__ = [
    "BLOCK_CODE",
    "STATE_UNAVAILABLE",
    "FINISH_FAILED",
    "reserve_call",
    "finish_call",
    "CAN_EXECUTE",
    "call_limit",
    "check",
    "datapoint_limit",
    "header_datapoints",
    "record",
    "register_client",
    "status",
]
