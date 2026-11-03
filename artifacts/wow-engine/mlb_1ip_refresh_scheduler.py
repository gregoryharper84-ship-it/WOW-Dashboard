"""Opt-in in-process scheduler for MLB 1IP final refresh.

This is a deployment/runtime adapter around mlb_1ip_final_refresh_job.run_once.
It reuses the already-configured production Supabase client from the web
process so no service-role credential must be copied into a second service.

The scheduler is disabled unless WOW_MLB_1IP_FINAL_REFRESH_ENABLED=1.
It does not change probability publication or wager execution authority.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from mlb_1ip_final_refresh_job import run_once
from mlb_1ip_live_self_acceptance import run_live_self_acceptance
from v17.memory_admission import memory_sample

CAN_EXECUTE = False
DEFAULT_INTERVAL_SECONDS = 300


def _process_rss_bytes() -> int | None:
    """Read current process RSS without importing a profiling dependency."""
    try:
        fields = Path("/proc/self/statm").read_text(encoding="utf-8").split()
        if len(fields) < 2:
            return None
        return int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, ValueError, TypeError):
        return None


def _runtime_memory_snapshot() -> dict[str, int | float | None]:
    sample = memory_sample()
    return {
        "cgroup_current_bytes": sample.current_bytes if sample else None,
        "cgroup_limit_bytes": sample.limit_bytes if sample else None,
        "cgroup_ratio": sample.ratio if sample else None,
        "process_rss_bytes": _process_rss_bytes(),
    }


def _delta(after: int | None, before: int | None) -> int | None:
    if after is None or before is None:
        return None
    return after - before


def _log_memory_delta(
    logger: logging.Logger,
    *,
    status: str,
    before: dict[str, int | float | None],
    after: dict[str, int | float | None],
    elapsed_ms: float,
) -> None:
    logger.warning(
        "WOW_V17_BACKGROUND_MEMORY_DELTA operation=MLB_1IP_FINAL_REFRESH status=%s "
        "elapsed_ms=%.3f cgroup_before_bytes=%s cgroup_after_bytes=%s cgroup_delta_bytes=%s "
        "cgroup_limit_bytes=%s cgroup_after_ratio=%s process_rss_before_bytes=%s "
        "process_rss_after_bytes=%s process_rss_delta_bytes=%s can_execute=false",
        status,
        elapsed_ms,
        before.get("cgroup_current_bytes"),
        after.get("cgroup_current_bytes"),
        _delta(
            after.get("cgroup_current_bytes") if isinstance(after.get("cgroup_current_bytes"), int) else None,
            before.get("cgroup_current_bytes") if isinstance(before.get("cgroup_current_bytes"), int) else None,
        ),
        after.get("cgroup_limit_bytes") or before.get("cgroup_limit_bytes"),
        after.get("cgroup_ratio"),
        before.get("process_rss_bytes"),
        after.get("process_rss_bytes"),
        _delta(
            after.get("process_rss_bytes") if isinstance(after.get("process_rss_bytes"), int) else None,
            before.get("process_rss_bytes") if isinstance(before.get("process_rss_bytes"), int) else None,
        ),
    )


async def run_refresh_loop(
    *,
    db_client_fn: Callable[[], Any],
    logger: logging.Logger,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    initial_delay_seconds: int = 30,
) -> None:
    """Run governed MLB 1IP refresh passes until the task is cancelled."""
    interval = max(60, int(interval_seconds))
    initial_delay = max(0, min(int(initial_delay_seconds), 300))
    logger.warning(
        "WOW_MLB_1IP_FINAL_REFRESH status=STARTED interval_seconds=%s initial_delay_seconds=%s probability_publishable=false can_execute=false",
        interval,
        initial_delay,
    )
    if initial_delay:
        await asyncio.sleep(float(initial_delay))
    if os.getenv("WOW_MLB_1IP_LIVE_SELF_ACCEPTANCE", "0") == "1":
        await run_live_self_acceptance(logger)
    while True:
        before = _runtime_memory_snapshot()
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(run_once, client=db_client_fn())
            after = _runtime_memory_snapshot()
            _log_memory_delta(
                logger,
                status="PASS",
                before=before,
                after=after,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
            logger.warning(
                "WOW_MLB_1IP_FINAL_REFRESH status=PASS seen=%s waiting=%s rerun_completed=%s purged=%s "
                "expired=%s expired_stale=%s failed=%s retry_scheduled=%s dead_lettered=%s "
                "dead_letter_backlog=%s probability_publishable=false can_execute=false",
                result.get("seen", 0),
                result.get("waiting", 0),
                result.get("rerun_completed", 0),
                result.get("purged", 0),
                result.get("expired", 0),
                result.get("expired_stale", 0),
                result.get("failed", 0),
                result.get("retry_scheduled", 0),
                result.get("dead_lettered", 0),
                result.get("dead_letter_backlog", 0),
            )
        except asyncio.CancelledError:
            _log_memory_delta(
                logger,
                status="CANCELLED",
                before=before,
                after=_runtime_memory_snapshot(),
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
            raise
        except Exception as exc:
            _log_memory_delta(
                logger,
                status="FAILED",
                before=before,
                after=_runtime_memory_snapshot(),
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
            logger.error(
                "WOW_MLB_1IP_FINAL_REFRESH status=FAILED error_type=%s probability_publishable=false can_execute=false",
                type(exc).__name__,
            )
        await asyncio.sleep(interval)
