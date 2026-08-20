"""Detached, lease-backed executor for one canonical WOW Daily manifest."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
from datetime import datetime, timezone
from typing import Callable

from gate_engine.daily_run_lifecycle import RUNNER_HEARTBEAT_SECONDS, _now, _safe_terminalize
from storage.daily_manifest import (
    RUNNER_LEASE_SECONDS,
    get_run,
    heartbeat_run,
    mark_progress,
)

logger = logging.getLogger(__name__)


def _as_iso(value: object | None) -> str | None:
    """Normalize psycopg2 timestamp values before passing them to the scorer."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class _RunnerHeartbeat:
    """Renew a run lease while orchestration performs source/network work."""

    def __init__(
        self,
        *,
        run_id: str,
        execution_owner: str,
        on_ownership_lost: Callable[[], None],
    ) -> None:
        self._run_id = run_id
        self._execution_owner = execution_owner
        self._on_ownership_lost = on_ownership_lost
        self._stop = threading.Event()
        self.lost_ownership = False
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"wow-daily-heartbeat-{run_id[:8]}",
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=RUNNER_HEARTBEAT_SECONDS + 1)

    def _run(self) -> None:
        while not self._stop.wait(RUNNER_HEARTBEAT_SECONDS):
            try:
                if not heartbeat_run(
                    run_id=self._run_id,
                    execution_owner=self._execution_owner,
                    lease_seconds=RUNNER_LEASE_SECONDS,
                ):
                    self.lost_ownership = True
                    logger.warning(
                        "daily executor lost manifest ownership run=%s",
                        self._run_id,
                    )
                    self._on_ownership_lost()
                    return
            except Exception:
                # Do not silently lengthen a failed lease: the startup reaper
                # will terminalize it honestly when the recorded lease expires.
                self.lost_ownership = True
                logger.exception(
                    "daily executor heartbeat failed run=%s",
                    self._run_id,
                )
                self._on_ownership_lost()
                return


def _terminalize_interruption(
    *,
    run_id: str,
    reason: str,
    module: str = "daily_run_executor",
    execution_owner: str | None = None,
) -> None:
    _safe_terminalize(
        run_id=run_id,
        finished_at=_now(),
        run_status="DEGRADED",
        failure_reason=reason,
        failure_module=module,
        execution_owner=execution_owner,
    )


def execute_run(
    *,
    run_id: str,
    execution_owner: str,
    orchestrator: Callable[..., object] | None = None,
) -> int:
    """Execute only an owned, active manifest and return a process exit code."""
    run = get_run(run_id)
    if not run:
        logger.error("daily executor missing manifest run=%s", run_id)
        return 2
    if (
        run.get("run_status") != "IN_PROGRESS"
        or run.get("finished_at") is not None
        or run.get("execution_owner") != execution_owner
    ):
        logger.warning("daily executor does not own active manifest run=%s", run_id)
        return 3
    if not heartbeat_run(
        run_id=run_id,
        execution_owner=execution_owner,
        lease_seconds=RUNNER_LEASE_SECONDS,
    ):
        logger.warning("daily executor could not establish lease run=%s", run_id)
        return 4

    previous_handlers: dict[int, signal.Handlers] = {}
    shutdown_reason: list[str | None] = [None]
    deadline_timer: threading.Timer | None = None

    def _request_shutdown(reason: str) -> None:
        if shutdown_reason[0] is not None:
            return
        shutdown_reason[0] = reason
        os.kill(os.getpid(), signal.SIGTERM)

    def _handle_signal(signum: int, _frame: object) -> None:
        signal_name = signal.Signals(signum).name
        _terminalize_interruption(
            run_id=run_id,
            reason=shutdown_reason[0] or f"RUNNER_SIGNAL_{signal_name}",
            execution_owner=execution_owner,
        )
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[signum] = signal.signal(signum, _handle_signal)
        heartbeat = _RunnerHeartbeat(
            run_id=run_id,
            execution_owner=execution_owner,
            on_ownership_lost=lambda: _request_shutdown("RUNNER_OWNERSHIP_LOST"),
        )
        heartbeat.start()
        deadline_at = _as_iso(run.get("deadline_at"))
        if deadline_at:
            deadline = datetime.fromisoformat(deadline_at.replace("Z", "+00:00"))
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
            seconds_remaining = max(
                0.0,
                (deadline - datetime.now(timezone.utc)).total_seconds(),
            )
            deadline_timer = threading.Timer(
                seconds_remaining,
                lambda: _request_shutdown("WHOLE_RUN_DEADLINE_EXCEEDED"),
            )
            deadline_timer.daemon = True
            deadline_timer.start()
        if not mark_progress(
            run_id=run_id,
            stage="DISCOVERY",
            detail="Detached canonical source union started",
            execution_owner=execution_owner,
        ):
            return 4
        if orchestrator is None:
            from gate_engine.daily_orchestrator import run_daily_orchestration

            orchestrator = run_daily_orchestration
        orchestrator(
            run_id=run_id,
            run_date=_as_iso(run.get("run_date")),
            sports=run.get("requested_sports") or None,
            environment=run.get("environment") or "production",
            runtime_provenance=run.get("runtime_provenance"),
            session_id=run.get("session_id"),
            deadline_at=deadline_at,
            persist=True,
            execution_owner=execution_owner,
        )
        if heartbeat.lost_ownership:
            logger.warning(
                "daily executor completed after losing ownership run=%s",
                run_id,
            )
            return 5
        return 0
    except SystemExit:
        raise
    except BaseException as exc:
        logger.exception("detached canonical daily runner failed run=%s", run_id)
        _terminalize_interruption(
            run_id=run_id,
            reason=f"RUNNER_EXCEPTION_{type(exc).__name__}",
            execution_owner=execution_owner,
        )
        return 1
    finally:
        if deadline_timer is not None:
            deadline_timer.cancel()
        if "heartbeat" in locals():
            heartbeat.stop()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute one WOW Daily manifest")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--execution-owner", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return execute_run(
        run_id=args.run_id,
        execution_owner=args.execution_owner,
    )


if __name__ == "__main__":
    raise SystemExit(main())