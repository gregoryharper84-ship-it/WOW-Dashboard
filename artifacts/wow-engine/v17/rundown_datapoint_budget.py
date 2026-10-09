"""Daily call reservation and data-point ceiling for every TheRundown request.

Rundown bills per returned price ("data point"). Before this guard, WOW had a
per-scan call cap but nothing bounding daily usage across callers, and the
monthly allowance was exhausted in September 2026.

Semantics (per UTC day):

- ``WOW_RUNDOWN_DAILY_CALL_BUDGET`` (default 300) is a **hard** bound. A call is
  reserved *before* any network I/O by one atomic database statement
  (``wow_v17_rundown_budget_reserve``), so concurrent workers, restarts and
  partial failures cannot exceed it. A reservation whose result is never
  settled (crash) stays counted, which errs on the conservative side.
- ``WOW_RUNDOWN_DAILY_DATAPOINT_BUDGET`` (default 6000) is a **soft admission
  ceiling**, not a hard cap: Rundown reports usage only after a response
  (``X-Datapoints``) and publishes no per-call maximum, so admission stops once
  settled usage reaches the ceiling and overshoot is bounded by the calls that
  were already in flight. Responses without a valid header are settled as
  ``unmetered`` and remain bounded by the call cap.
- ``OFF`` disables a limit.

Source of truth:

- ``DURABLE`` scope (a DB client is registered, as on the production app): the
  shared ``wow_v17_rundown_daily_budget`` row is authoritative. Any failure to
  read/reserve, or an unrecoverable failure to settle a previous call, fails
  closed with ``PAID_PROVIDER_BUDGET_STATE_UNAVAILABLE`` before network I/O.
  The ledger is never silently reset.
- ``PROCESS_LOCAL`` scope (no client registered, e.g. tests or a stand-alone
  worker): the same semantics apply within one process only. The scope is
  reported in every reservation/status so callers never mistake it for a
  shared bound.

Exhaustion fails closed with ``PAID_PROVIDER_BUDGET_EXHAUSTED``; no network call
is made. Cost control only: never creates, alters or substitutes a probability.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

CAN_EXECUTE = False
BLOCK_CODE = "PAID_PROVIDER_BUDGET_EXHAUSTED"
STATE_UNAVAILABLE_CODE = "PAID_PROVIDER_BUDGET_STATE_UNAVAILABLE"
BUDGET_TABLE = "wow_v17_rundown_daily_budget"
RESERVE_RPC = "wow_v17_rundown_budget_reserve"
SETTLE_RPC = "wow_v17_rundown_budget_settle"
SCOPE_DURABLE = "DURABLE"
SCOPE_LOCAL = "PROCESS_LOCAL"
LOGGER = logging.getLogger(__name__)

_lock = threading.Lock()
_local: dict[str, Any] = {}
_pending_settlements: list[dict[str, Any]] = []
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


def register_client(client_fn: Callable[[], Any] | None) -> None:
    """Make the shared database row the source of truth (called at startup)."""
    global _client_fn
    _client_fn = client_fn if callable(client_fn) else None


def scope() -> str:
    return SCOPE_DURABLE if _client_fn is not None else SCOPE_LOCAL


def _empty_totals() -> dict[str, int]:
    return {
        "calls_reserved": 0,
        "calls_settled": 0,
        "calls_in_flight": 0,
        "calls_unmetered": 0,
        "calls_failed": 0,
        "datapoints": 0,
    }


def _local_day(day: str) -> dict[str, int]:
    if _local.get("day") != day:
        _local.clear()
        _local.update(day=day, totals=_empty_totals())
    return _local["totals"]


def _rpc_payload(response: Any) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    if not isinstance(data, dict):
        raise ValueError("budget RPC returned no object")
    return data


def _settle_params(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "p_day": item["day"],
        "p_datapoints": item["datapoints"],
        "p_failed": bool(item["failed"]),
    }


def _flush_pending_locked() -> bool:
    """Retry settlements whose durable write failed. False if any still fail."""
    while _pending_settlements:
        item = _pending_settlements[0]
        try:
            _rpc_payload(_client_fn().rpc(SETTLE_RPC, _settle_params(item)).execute())
        except Exception:  # noqa: BLE001 - typed fail-closed below
            return False
        _pending_settlements.pop(0)
    return True


@dataclass
class Reservation:
    """One admitted (or denied) paid request; settle exactly once."""

    allowed: bool
    code: str | None
    day: str
    scope: str
    totals: dict[str, Any] = field(default_factory=dict)
    datapoints: int | None = None
    failed: bool = False
    settled: bool = False

    def observe(self, headers: Any, *, failed: bool = False) -> None:
        """Record what the provider reported; a known count is never erased."""
        reported = header_datapoints(headers)
        if reported is not None:
            self.datapoints = reported
        if failed:
            self.failed = True

    def settle(self) -> None:
        """Release the in-flight slot and add reported usage (idempotent)."""
        if not self.allowed or self.settled:
            return
        self.settled = True
        item = {"day": self.day, "datapoints": self.datapoints, "failed": self.failed}
        if self.datapoints is None:
            LOGGER.info("RUNDOWN_DATAPOINT_BUDGET=UNMETERED_CALL day=%s can_execute=false", self.day)
        with _lock:
            if self.scope == SCOPE_LOCAL:
                totals = _local_day(self.day)
                totals["calls_in_flight"] = max(0, totals["calls_in_flight"] - 1)
                totals["calls_settled"] += 1
                totals["datapoints"] += self.datapoints or 0
                totals["calls_unmetered"] += 1 if self.datapoints is None else 0
                totals["calls_failed"] += 1 if self.failed else 0
                return
            _pending_settlements.append(item)
            if _client_fn is None or not _flush_pending_locked():
                LOGGER.warning(
                    "RUNDOWN_DATAPOINT_BUDGET=SETTLE_DEFERRED day=%s pending=%s can_execute=false",
                    self.day, len(_pending_settlements),
                )

    def audit(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "day": self.day,
            "scope": self.scope,
            "totals": dict(self.totals),
            "call_limit": call_limit(),
            "call_limit_kind": "HARD_RESERVED",
            "datapoint_limit": datapoint_limit(),
            "datapoint_limit_kind": "SOFT_ADMISSION_CEILING",
            "paid_provider_network_attempted": False if not self.allowed else None,
            "can_execute": False,
        }


def reserve(now: datetime | None = None) -> Reservation:
    """Atomically reserve one Rundown call for today, or deny before network I/O."""
    day = _today(now)
    c_limit, dp_limit = call_limit(), datapoint_limit()
    with _lock:
        if _client_fn is None:
            totals = _local_day(day)
            if (c_limit is not None and totals["calls_reserved"] >= c_limit) or (
                dp_limit is not None and totals["datapoints"] >= dp_limit
            ):
                return Reservation(False, BLOCK_CODE, day, SCOPE_LOCAL, dict(totals))
            totals["calls_reserved"] += 1
            totals["calls_in_flight"] += 1
            return Reservation(True, None, day, SCOPE_LOCAL, dict(totals))

        if not _flush_pending_locked():
            LOGGER.warning("RUNDOWN_DATAPOINT_BUDGET=STATE_UNAVAILABLE reason=pending_settlement can_execute=false")
            return Reservation(False, STATE_UNAVAILABLE_CODE, day, SCOPE_DURABLE,
                               {"pending_settlements": len(_pending_settlements)})
        try:
            result = _rpc_payload(
                _client_fn().rpc(
                    RESERVE_RPC,
                    {"p_day": day, "p_call_limit": c_limit, "p_datapoint_limit": dp_limit},
                ).execute()
            )
            allowed = result.get("allowed")
            if not isinstance(allowed, bool):
                raise ValueError("budget RPC returned no boolean decision")
        except Exception:  # noqa: BLE001 - fail closed, never reset the ledger
            LOGGER.warning("RUNDOWN_DATAPOINT_BUDGET=STATE_UNAVAILABLE reason=reserve_failed can_execute=false")
            return Reservation(False, STATE_UNAVAILABLE_CODE, day, SCOPE_DURABLE, {})
    totals = {k: result.get(k) for k in _empty_totals()}
    return Reservation(allowed, None if allowed else BLOCK_CODE, day, SCOPE_DURABLE, totals)


def header_datapoints(headers: Any) -> int | None:
    """Non-negative integer ``X-Datapoints`` or None when missing/invalid."""
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for name in ("X-Datapoints", "x-datapoints"):
        value = getter(name)
        if value is not None:
            try:
                parsed = int(str(value).strip())
            except ValueError:
                return None
            return parsed if parsed >= 0 else None
    return None


def status(now: datetime | None = None) -> dict[str, Any]:
    day = _today(now)
    base = {
        "day": day,
        "scope": scope(),
        "call_limit": call_limit(),
        "call_limit_kind": "HARD_RESERVED",
        "datapoint_limit": datapoint_limit(),
        "datapoint_limit_kind": "SOFT_ADMISSION_CEILING",
        "can_execute": False,
    }
    with _lock:
        if _client_fn is None:
            return {**base, "state": "OK", **_local_day(day)}
        pending = len(_pending_settlements)
    try:
        response = (
            _client_fn().table(BUDGET_TABLE).select("*").eq("budget_day", day).limit(1).execute()
        )
        rows = getattr(response, "data", None) or []
        row = rows[0] if rows and isinstance(rows[0], dict) else {}
    except Exception:  # noqa: BLE001 - status is diagnostic; report, don't guess
        return {**base, "state": STATE_UNAVAILABLE_CODE, "pending_settlements": pending}
    totals = {k: int(row.get(k) or 0) for k in _empty_totals()}
    return {**base, "state": "OK", "pending_settlements": pending, **totals}


def _reset_for_tests() -> None:
    global _client_fn
    with _lock:
        _local.clear()
        _pending_settlements.clear()
        _client_fn = None


__all__ = [
    "BLOCK_CODE",
    "CAN_EXECUTE",
    "Reservation",
    "STATE_UNAVAILABLE_CODE",
    "call_limit",
    "datapoint_limit",
    "header_datapoints",
    "register_client",
    "reserve",
    "scope",
    "status",
]
