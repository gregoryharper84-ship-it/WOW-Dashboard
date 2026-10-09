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
from dataclasses import dataclass
from datetime import timedelta

from v17.engineering_auditor import EngineeringAuditStore, parse_timestamp, utcnow
from v17.engineering_auditor_github import (
    GitHubAuditUnavailable, bootstrap_open_github_work, reconcile_github_updates,
    reconcile_sirt_watchdog,
)

_logger = logging.getLogger("wow.v17.engineering_auditor")
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def _db_client():
    """Build the worker's service-role Supabase client without a repo-root import.

    WOW historically uses both SUPABASE_SERVICE_ROLE_KEY and
    SUPABASE_SERVICE_KEY for the same server-side credential. Accept either,
    preferring the explicit role-named variable, while failing closed when the
    URL or credential is absent. No secret value is logged.
    """
    from supabase import create_client

    url = os.getenv("SUPABASE_URL", "").strip()
    key = (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    )
    if not url:
        raise RuntimeError("SUPABASE_URL unavailable")
    if not key:
        raise RuntimeError("SUPABASE service credential unavailable")
    return create_client(url, key)


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
    # Three unauthenticated public GitHub requests per pass (issues, runs,
    # independent SIRT runs); five minutes stays within 60/hour/IP.
    return min(max(value, 300), 1800)





@dataclass
class IndependentPollHealth:
    """Sticky, separately proved liveness across non-GitHub worker ticks."""

    github_error: str | None = "GITHUB_AUDIT_UNVERIFIED"
    watchdog_outcome: str = "SIRT_WATCHDOG_UNVERIFIED"

    @staticmethod
    def github_failure(exc: Exception) -> str:
        if isinstance(exc, GitHubAuditUnavailable):
            return str(exc)
        return f"GITHUB_AUDIT_{type(exc).__name__.upper()}"

    def report_github(self, error: str | None) -> None:
        self.github_error = error

    def report_watchdog(self, outcome: str) -> None:
        if outcome not in {"WATCHDOG_RUN_OBSERVED", "WATCHDOG_HEARTBEAT_MISSING"}:
            raise ValueError("SIRT_WATCHDOG_OUTCOME_INVALID")
        self.watchdog_outcome = outcome

    def watchdog_unavailable(self) -> None:
        self.watchdog_outcome = "SIRT_WATCHDOG_SOURCE_UNAVAILABLE"

    def runtime_state(self) -> tuple[str, str]:
        # A healthy generic GitHub API call does not clear a missing or
        # unverified SIRT watchdog. Only a fresh watchdog run does.
        if self.watchdog_outcome != "WATCHDOG_RUN_OBSERVED":
            return "DEGRADED", (
                "SIRT_WATCHDOG_HEARTBEAT_MISSING"
                if self.watchdog_outcome == "WATCHDOG_HEARTBEAT_MISSING"
                else self.watchdog_outcome
            )
        if self.github_error:
            return "DEGRADED", self.github_error
        return "RUNNING", "CLEAR"

def run_engineering_auditor_loop(stop_event: threading.Event = _STOP) -> None:
    """Reconcile on startup, then remain alive and enforce persisted deadlines."""
    instance_id = f"{socket.gethostname()}:{os.getpid()}"
    seen_workflow_runs: set[str] = set()
    last_github_sync = utcnow() - timedelta(minutes=10)
    poll_health = IndependentPollHealth()
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
        try:
            github_open_n = bootstrap_open_github_work(store)
            github_receipt = reconcile_github_updates(
                store,
                since=last_github_sync,
                seen_workflow_runs=seen_workflow_runs,
            )
            github_update_n = github_receipt["github_work_events"]
            github_health_n = github_receipt["code_health_events"]
            poll_health.report_github(None)
            last_github_sync = utcnow()
        except Exception as exc:
            poll_health.report_github(IndependentPollHealth.github_failure(exc))
            _logger.warning(
                "WOW_ENGINEERING_AUDITOR github_startup_reconcile=DEGRADED error_type=%s can_execute=false",
                type(exc).__name__,
            )
        # Independent from general issue/run reconciliation: one failing
        # GitHub endpoint cannot suppress the SIRT dead-man poll.
        try:
            poll_health.report_watchdog(reconcile_sirt_watchdog(store))
        except Exception as exc:
            poll_health.watchdog_unavailable()
            _logger.warning(
                "WOW_ENGINEERING_AUDITOR sirt_watchdog_startup=DEGRADED error_type=%s can_execute=false",
                type(exc).__name__,
            )
        runtime_status, runtime_error = poll_health.runtime_state()
        store.refresh_runtime_counts(now=utcnow())
        store.touch_runtime(
            instance_id=instance_id,
            status=runtime_status,
            last_heartbeat_at=utcnow(),
            last_reconcile_at=utcnow(),
            last_error_code=runtime_error,
        )
        _logger.warning(
            "WOW_ENGINEERING_AUDITOR status=%s startup_backlog=%s startup_findings=%s github_open=%s github_updates=%s code_health=%s terminal_authority=V17_TERMINAL_REDUCER can_execute=false",
            runtime_status,
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
        # Use latched independent evidence on every 30-second iteration.
        # A missing sentinel never reverts to CLEAR between 5-minute polls.
        status, error_code = poll_health.runtime_state()
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
                    poll_health.report_github(None)
                    last_github_sync = now
                except Exception as exc:
                    poll_health.report_github(IndependentPollHealth.github_failure(exc))
                    _logger.warning(
                        "WOW_ENGINEERING_AUDITOR github_reconcile=DEGRADED error_type=%s can_execute=false",
                        type(exc).__name__,
                    )
                # Poll the watchdog separately even when ordinary GitHub
                # issue/CI reconciliation failed.
                try:
                    poll_health.report_watchdog(reconcile_sirt_watchdog(store, now=now))
                except Exception as exc:
                    poll_health.watchdog_unavailable()
                    _logger.warning(
                        "WOW_ENGINEERING_AUDITOR sirt_watchdog_reconcile=DEGRADED error_type=%s can_execute=false",
                        type(exc).__name__,
                    )
                finally:
                    next_github_poll = time.monotonic() + _github_interval_seconds()
                status, error_code = poll_health.runtime_state()
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
