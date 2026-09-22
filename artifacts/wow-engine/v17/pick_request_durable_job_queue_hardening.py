"""Resilience hardening for the V17 durable pick-request queue.

Transient receipt-ledger outages must never cause a blind scorer retry or an
immediate permanent run closure. They move the durable job into bounded
RETRY_WAIT with no scoring. Ambiguous/conflicting receipts still fail closed.

The lease floor is also extended to one hour so a bounded canonical scoring
batch cannot be reclaimed by another worker merely because it outlives the
original 30-minute lease. can_execute remains false unconditionally.
"""
from __future__ import annotations

from typing import Any

from v17 import pick_request_durable_job_queue as queue

CAN_EXECUTE = False
LEASE_FLOOR_SECONDS = 3600
_TRANSIENT_RECEIPT_CODES = {
    "PREDICTION_RECEIPT_LOOKUP_FAILED",
    "PREDICTION_LEDGER_UNAVAILABLE",
    "RECEIPT_SERVICE_UNREACHABLE",
}
_INSTALLED = False
_ORIGINAL_TERMINAL_STOP = queue._terminal_stop


def _terminal_stop_with_receipt_backoff(
    db: Any,
    job: dict[str, Any],
    *,
    code: str,
    detail: dict[str, Any] | None = None,
) -> None:
    normalized = str(code or "").strip().upper()
    next_failure = int(job.get("consecutive_failures") or 0) + 1
    if normalized in _TRANSIENT_RECEIPT_CODES and next_failure < queue.MAX_CONSECUTIVE_FAILURES:
        error = dict(detail or {})
        error.update(
            {
                "code": normalized,
                "phase": "RECEIPT_PREFLIGHT",
                "scoring_attempted": False,
                "can_execute": False,
            }
        )
        queue._retry_or_stop(db, job, error)
        return
    _ORIGINAL_TERMINAL_STOP(db, job, code=code, detail=detail)


def install_durable_job_queue_hardening() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    queue.LEASE_SECONDS = max(int(queue.LEASE_SECONDS), LEASE_FLOOR_SECONDS)
    queue._terminal_stop = _terminal_stop_with_receipt_backoff
    _INSTALLED = True


__all__ = [
    "CAN_EXECUTE",
    "LEASE_FLOOR_SECONDS",
    "install_durable_job_queue_hardening",
]
