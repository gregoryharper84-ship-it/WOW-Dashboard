"""Always-on deadline/reconciliation loop for the WOW Engineering Auditor.

This process is driven by the lifetime of the existing Render background worker.
It is intentionally not a cron job and does not register a Celery beat schedule.
"""
from __future__ import annotations

import logging
import os
import socket
import threading

from v17.engineering_auditor import EngineeringAuditStore, utcnow

_logger = logging.getLogger("wow.v17.engineering_auditor")
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def _db_client():
    from ledger import get_client
    return get_client()


def _enabled() -> bool:
    return os.getenv("WOW_ENGINEERING_AUDITOR_ENABLED", "0") == "1"


def _interval_seconds() -> int:
    try:
        value = int(os.getenv("WOW_ENGINEERING_AUDITOR_MAX_SLEEP_SECONDS", "30"))
    except ValueError:
        value = 30
    return min(max(value, 5), 300)


def run_engineering_auditor_loop(stop_event: threading.Event = _STOP) -> None:
    """Reconcile on startup, then remain alive and enforce persisted deadlines."""
    instance_id = f"{socket.gethostname()}:{os.getpid()}"
    try:
        store = EngineeringAuditStore(_db_client())
        now = utcnow()
        store.touch_runtime(
            instance_id=instance_id,
            status="STARTING",
            started_at=now,
            last_heartbeat_at=now,
            last_error_code="CLEAR",
        )
        backlog_n = store.reconcile_backlog(now=now)
        opened = store.reconcile_due(now=now)
        store.refresh_runtime_counts(now=now)
        store.touch_runtime(
            instance_id=instance_id,
            status="RUNNING",
            last_heartbeat_at=now,
            last_reconcile_at=now,
            last_error_code="CLEAR",
        )
        _logger.warning(
            "WOW_ENGINEERING_AUDITOR status=RUNNING startup_reconciled=%s startup_findings=%s terminal_authority=V17_TERMINAL_REDUCER can_execute=false",
            backlog_n,
            len(opened),
        )
    except Exception as exc:
        _logger.exception(
            "WOW_ENGINEERING_AUDITOR startup failed error_type=%s can_execute=false",
            type(exc).__name__,
        )
        return

    while not stop_event.wait(_interval_seconds()):
        now = utcnow()
        try:
            # The process is continuously resident. This maximum sleep merely
            # bounds detection latency for deadlines inserted by event ingress;
            # no external scheduler or Celery beat participates.
            store.reconcile_backlog(now=now)
            store.reconcile_due(now=now)
            store.refresh_runtime_counts(now=now)
            store.touch_runtime(
                instance_id=instance_id,
                status="RUNNING",
                last_heartbeat_at=now,
                last_error_code="CLEAR",
            )
        except Exception as exc:
            _logger.exception(
                "WOW_ENGINEERING_AUDITOR reconciliation failed error_type=%s can_execute=false",
                type(exc).__name__,
            )
            try:
                store.touch_runtime(
                    instance_id=instance_id,
                    status="DEGRADED",
                    last_heartbeat_at=now,
                    last_error_code=f"AUDITOR_RECONCILIATION_{type(exc).__name__.upper()}",
                )
            except Exception:
                pass

    try:
        store.touch_runtime(
            instance_id=instance_id,
            status="STOPPED",
            last_heartbeat_at=utcnow(),
            last_error_code="CLEAR",
        )
    except Exception:
        pass


def start_engineering_auditor() -> bool:
    global _THREAD
    if not _enabled():
        _logger.warning("WOW_ENGINEERING_AUDITOR status=DISABLED can_execute=false")
        return False
    if _THREAD is not None and _THREAD.is_alive():
        return False
    _STOP.clear()
    _THREAD = threading.Thread(
        target=run_engineering_auditor_loop,
        name="wow-v17-engineering-auditor",
        daemon=True,
    )
    _THREAD.start()
    return True


def stop_engineering_auditor() -> None:
    _STOP.set()
    thread = _THREAD
    if thread and thread.is_alive():
        thread.join(timeout=5.0)


def install_celery_worker_hooks() -> None:
    """Attach auditor lifetime to the existing always-on Celery worker."""
    from celery.signals import worker_ready, worker_shutdown

    @worker_ready.connect(weak=False)
    def _start_on_worker_ready(**_: object) -> None:
        start_engineering_auditor()

    @worker_shutdown.connect(weak=False)
    def _stop_on_worker_shutdown(**_: object) -> None:
        stop_engineering_auditor()
