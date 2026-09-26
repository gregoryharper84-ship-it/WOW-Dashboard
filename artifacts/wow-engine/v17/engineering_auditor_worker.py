"""Always-on deadline/reconciliation loop for the WOW Engineering Auditor.

This process is driven by the lifetime of the existing Render background worker.
It is intentionally not a cron job and does not register a Celery beat schedule.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from datetime import timedelta

from v17.engineering_auditor import EngineeringAuditStore, parse_timestamp, utcnow
from v17.engineering_auditor_github import bootstrap_open_github_work, reconcile_github_updates

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


def _github_interval_seconds() -> int:
    try:
        value = int(os.getenv("WOW_ENGINEERING_AUDITOR_GITHUB_INTERVAL_SECONDS", "300"))
    except ValueError:
        value = 300
    # Two unauthenticated public GitHub requests per pass. Five minutes keeps
    # normal use well below the public 60-request/hour/IP budget.
    return min(max(value, 180), 1800)


def run_engineering_auditor_loop(stop_event: threading.Event = _STOP) -> None:
    """Reconcile on startup, then remain alive and enforce persisted deadlines."""
    instance_id = f"{socket.gethostname()}:{os.getpid()}"
    seen_workflow_runs: set[str] = set()
    last_github_sync = utcnow() - timedelta(minutes=10)
    try:
        store = EngineeringAuditStore(_db_client())
        prior_health = store.health()
        prior_event_at = prior_health.get("last_event_processed_at")
        if prior_event_at:
            try:
                last_github_sync = parse_timestamp(str(prior_event_at))
            except (TypeError, ValueError):
                pass
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
        github_open_n = 0
        github_update_n = 0
        github_health_n = 0
        github_error: str | None = None
        try:
            github_open_n = bootstrap_open_github_work(store)
            github_receipt = reconcile_github_updates(
                store,
                since=last_github_sync,
                seen_workflow_runs=seen_workflow_runs,
            )
            github_update_n = github_receipt["github_work_events"]
            github_health_n = github_receipt["code_health_events"]
            last_github_sync = utcnow()
        except Exception as exc:
            github_error = f"GITHUB_AUDIT_{type(exc).__name__.upper()}"
            _logger.warning(
                "WOW_ENGINEERING_AUDITOR github_startup_reconcile=DEGRADED error_type=%s can_execute=false",
                type(exc).__name__,
            )
        store.refresh_runtime_counts(now=utcnow())
        store.touch_runtime(
            instance_id=instance_id,
            status="DEGRADED" if github_error else "RUNNING",
            last_heartbeat_at=utcnow(),
            last_reconcile_at=utcnow(),
            last_error_code=github_error or "CLEAR",
        )
        _logger.warning(
            "WOW_ENGINEERING_AUDITOR status=%s startup_backlog=%s startup_findings=%s github_open=%s github_updates=%s code_health=%s terminal_authority=V17_TERMINAL_REDUCER can_execute=false",
            "DEGRADED" if github_error else "RUNNING",
            backlog_n,
            len(opened),
            github_open_n,
            github_update_n,
            github_health_n,
        )
    except Exception as exc:
        _logger.exception(
            "WOW_ENGINEERING_AUDITOR startup failed error_type=%s can_execute=false",
            type(exc).__name__,
        )
        return

    next_github_poll = time.monotonic() + _github_interval_seconds()
    while not stop_event.wait(_interval_seconds()):
        now = utcnow()
        error_code = "CLEAR"
        status = "RUNNING"
        try:
            # The process is continuously resident. This maximum sleep merely
            # bounds detection latency for persisted deadlines; no external
            # scheduler or Celery beat participates.
            store.reconcile_backlog(now=now)
            store.reconcile_due(now=now)
            if time.monotonic() >= next_github_poll:
                try:
                    reconcile_github_updates(
                        store,
                        since=last_github_sync,
                        seen_workflow_runs=seen_workflow_runs,
                    )
                    last_github_sync = now
                except Exception as exc:
                    status = "DEGRADED"
                    error_code = f"GITHUB_AUDIT_{type(exc).__name__.upper()}"
                    _logger.warning(
                        "WOW_ENGINEERING_AUDITOR github_reconcile=DEGRADED error_type=%s can_execute=false",
                        type(exc).__name__,
                    )
                finally:
                    next_github_poll = time.monotonic() + _github_interval_seconds()
            store.refresh_runtime_counts(now=now)
            store.touch_runtime(
                instance_id=instance_id,
                status=status,
                last_heartbeat_at=now,
                last_reconcile_at=now,
                last_error_code=error_code,
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
