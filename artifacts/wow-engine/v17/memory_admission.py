"""Shared V17 memory admission and heavyweight-job serialization.

This module is runtime/orchestration only. It never creates or modifies a
sporting probability, publication decision, calibration value, or execution
authority. The single in-process heavyweight slot prevents overlapping Scout
specialist scoring and Daily full-slate work inside the same Render process.

Memory pressure is measured from cgroup v1/v2 counters when available. A
hysteresis band prevents rapid admit/defer oscillation near the threshold.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CAN_EXECUTE = False
DEFAULT_STOP_RATIO = 0.80
DEFAULT_RESUME_RATIO = 0.68
DEFAULT_SYNC_WAIT_SECONDS = 5.0
DEFAULT_PRESSURE_RETRY_SECONDS = 15.0
DEFAULT_PRESSURE_RECLAIM_INTERVAL_SECONDS = 60.0

LOGGER = logging.getLogger("wow.v17.memory_admission")
_HEAVY_JOB_LOCK = threading.Lock()
_PRESSURE_STATE_LOCK = threading.Lock()
_PRIORITY_STATE_LOCK = threading.Lock()
_RECLAIM_STATE_LOCK = threading.Lock()
_INTERACTIVE_WAITERS = 0
_PRESSURE_ACTIVE = False
_MISSING_SAMPLE_WARNED = False
_LAST_RECLAIM_MONOTONIC: float | None = None


@dataclass(frozen=True)
class MemorySample:
    current_bytes: int
    limit_bytes: int

    @property
    def ratio(self) -> float:
        return self.current_bytes / self.limit_bytes


class HeavyJobDeferred(RuntimeError):
    """Typed non-terminal deferral for bounded runtime admission."""

    def __init__(self, *, code: str, operation: str, detail: dict[str, Any]):
        super().__init__(code)
        self.code = code
        self.operation = operation
        self.detail = dict(detail)

    def receipt(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": "DEFERRED",
            "terminal": False,
            "operation": self.operation,
            **self.detail,
            "probability_publishable": False,
            "can_execute": False,
        }


class HeavyJobPermit:
    """Exactly-once release handle for the shared heavyweight slot."""

    def __init__(self, *, operation: str, admission: dict[str, Any]):
        self.operation = operation
        self.admission = dict(admission)
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        _HEAVY_JOB_LOCK.release()

    def __enter__(self) -> "HeavyJobPermit":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _thresholds() -> tuple[float, float]:
    stop = _float_env(
        "WOW_V17_HEAVY_MEMORY_STOP_RATIO",
        DEFAULT_STOP_RATIO,
        minimum=0.60,
        maximum=0.95,
    )
    resume = _float_env(
        "WOW_V17_HEAVY_MEMORY_RESUME_RATIO",
        DEFAULT_RESUME_RATIO,
        minimum=0.40,
        maximum=0.90,
    )
    # Hysteresis must be real even if environment values are misconfigured.
    resume = min(resume, stop - 0.02)
    return stop, resume


def _read_cgroup_memory_bytes() -> tuple[int, int] | None:
    candidates = (
        (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory.max")),
        (
            Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
            Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        ),
    )
    for current_path, limit_path in candidates:
        try:
            current_text = current_path.read_text(encoding="utf-8").strip()
            limit_text = limit_path.read_text(encoding="utf-8").strip()
            if not current_text or not limit_text or limit_text.lower() == "max":
                continue
            current = int(current_text)
            limit = int(limit_text)
        except (OSError, ValueError):
            continue
        if current >= 0 and limit > 0:
            return current, limit
    return None


def _read_process_rss_bytes() -> int | None:
    """Process RSS diagnostic, distinct from cgroup usage including cache."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) == 3 and parts[2] == "kB":
                    return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def memory_sample() -> MemorySample | None:
    raw = _read_cgroup_memory_bytes()
    if raw is None:
        return None
    return MemorySample(current_bytes=raw[0], limit_bytes=raw[1])


def _pressure_for_sample(sample: MemorySample | None) -> bool:
    global _PRESSURE_ACTIVE, _MISSING_SAMPLE_WARNED

    if sample is None:
        if not _MISSING_SAMPLE_WARNED:
            LOGGER.warning(
                "WOW_V17_MEMORY_ADMISSION measurement=UNAVAILABLE action=ALLOW_WITHOUT_CGROUP_SAMPLE can_execute=false"
            )
            _MISSING_SAMPLE_WARNED = True
        return False

    stop, resume = _thresholds()
    with _PRESSURE_STATE_LOCK:
        if _PRESSURE_ACTIVE:
            if sample.ratio < resume:
                _PRESSURE_ACTIVE = False
        elif sample.ratio >= stop:
            _PRESSURE_ACTIVE = True
        return _PRESSURE_ACTIVE


def _interactive_waiter_count() -> int:
    with _PRIORITY_STATE_LOCK:
        return _INTERACTIVE_WAITERS


def admission_snapshot(operation: str) -> dict[str, Any]:
    sample = memory_sample()
    under_pressure = _pressure_for_sample(sample)
    stop, resume = _thresholds()
    return {
        "operation": operation,
        "measurement_available": sample is not None,
        "memory_current_bytes": sample.current_bytes if sample else None,
        "process_rss_bytes": _read_process_rss_bytes(),
        "memory_limit_bytes": sample.limit_bytes if sample else None,
        "memory_ratio": sample.ratio if sample else None,
        "stop_ratio": stop,
        "resume_ratio": resume,
        "under_pressure": under_pressure,
        "heavy_slot_busy": _HEAVY_JOB_LOCK.locked(),
        "interactive_waiters": _interactive_waiter_count(),
        "can_execute": False,
    }


def _raise_memory_pressure(operation: str, snapshot: dict[str, Any]) -> None:
    raise HeavyJobDeferred(
        code="MEMORY_PRESSURE",
        operation=operation,
        detail={
            "reason": "MEMORY_PRESSURE",
            "memory_current_bytes": snapshot.get("memory_current_bytes"),
            "process_rss_bytes": snapshot.get("process_rss_bytes"),
            "memory_limit_bytes": snapshot.get("memory_limit_bytes"),
            "memory_ratio": snapshot.get("memory_ratio"),
            "stop_ratio": snapshot.get("stop_ratio"),
            "resume_ratio": snapshot.get("resume_ratio"),
            "retry_after_seconds": DEFAULT_PRESSURE_RETRY_SECONDS,
        },
    )


def _monotonic_clock() -> float:
    """Small indirection keeps rate-limit tests from replacing the global clock."""
    return time.monotonic()


def _reclaim_and_resample_under_pressure(operation: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Bounded reclamation never bypasses the existing cgroup hysteresis gate."""
    global _LAST_RECLAIM_MONOTONIC
    if not snapshot["under_pressure"]:
        return snapshot
    now = _monotonic_clock()
    with _RECLAIM_STATE_LOCK:
        if (
            _LAST_RECLAIM_MONOTONIC is not None
            and 0 <= now - _LAST_RECLAIM_MONOTONIC < DEFAULT_PRESSURE_RECLAIM_INTERVAL_SECONDS
        ):
            return snapshot
        _LAST_RECLAIM_MONOTONIC = now
    try:
        release_process_memory()
    except Exception as exc:
        LOGGER.warning(
            "WOW_V17_MEMORY_RECLAIM_FAILED operation=%s error_type=%s can_execute=false",
            operation,
            type(exc).__name__,
        )
        return snapshot
    updated = admission_snapshot(operation)
    if not updated["measurement_available"]:
        # A vanished cgroup sample must not relax a measured prior hold.
        updated = snapshot
    LOGGER.warning(
        "WOW_V17_MEMORY_RECLAIM operation=%s before_cgroup_bytes=%s after_cgroup_bytes=%s "
        "before_process_rss_bytes=%s after_process_rss_bytes=%s before_ratio=%s "
        "after_ratio=%s admission_recovered=%s can_execute=false",
        operation,
        snapshot.get("memory_current_bytes"),
        updated.get("memory_current_bytes"),
        snapshot.get("process_rss_bytes"),
        updated.get("process_rss_bytes"),
        snapshot.get("memory_ratio"),
        updated.get("memory_ratio"),
        not updated["under_pressure"],
    )
    return updated


def try_acquire_heavy_job(operation: str) -> HeavyJobPermit | None:
    """Acquire the one heavyweight slot without blocking.

    Returns None when another heavy job owns the slot. Memory pressure is a
    typed non-terminal deferral and never claims downstream durable work.
    """
    # Give a bounded synchronous user-facing request priority once it is
    # waiting so background Scout/async work cannot repeatedly reacquire this
    # non-fair lock and starve the interactive run.
    with _PRIORITY_STATE_LOCK:
        if _INTERACTIVE_WAITERS > 0:
            return None
        acquired = _HEAVY_JOB_LOCK.acquire(blocking=False)
    if not acquired:
        return None

    try:
        snapshot = _reclaim_and_resample_under_pressure(
            operation, admission_snapshot(operation)
        )
    except Exception as exc:
        # A failed cgroup/RSS/reclamation measurement must never strand the
        # heavyweight lock or accidentally allow unmeasured scoring.
        _HEAVY_JOB_LOCK.release()
        raise HeavyJobDeferred(
            code="MEMORY_ADMISSION_MEASUREMENT_FAILED",
            operation=operation,
            detail={
                "reason": "MEMORY_ADMISSION_MEASUREMENT_FAILED",
                "error_type": type(exc).__name__,
                "retry_after_seconds": DEFAULT_PRESSURE_RETRY_SECONDS,
            },
        ) from exc
    if snapshot["under_pressure"]:
        _HEAVY_JOB_LOCK.release()
        _raise_memory_pressure(operation, snapshot)

    snapshot["heavy_slot_busy"] = True
    return HeavyJobPermit(operation=operation, admission=snapshot)


def acquire_heavy_job(operation: str, *, wait_seconds: float | None = None) -> HeavyJobPermit:
    """Acquire the shared heavy slot for a synchronous request.

    Interactive callers wait only a bounded interval. Background workers should
    prefer try_acquire_heavy_job so cancellation cannot strand a lock in a
    worker thread.
    """
    if wait_seconds is None:
        wait_seconds = _float_env(
            "WOW_V17_HEAVY_JOB_SYNC_WAIT_SECONDS",
            DEFAULT_SYNC_WAIT_SECONDS,
            minimum=0.0,
            maximum=60.0,
        )
    global _INTERACTIVE_WAITERS
    with _PRIORITY_STATE_LOCK:
        _INTERACTIVE_WAITERS += 1
    try:
        acquired = _HEAVY_JOB_LOCK.acquire(timeout=max(0.0, float(wait_seconds)))
    finally:
        with _PRIORITY_STATE_LOCK:
            _INTERACTIVE_WAITERS = max(0, _INTERACTIVE_WAITERS - 1)
    if not acquired:
        raise HeavyJobDeferred(
            code="HEAVY_JOB_BUSY",
            operation=operation,
            detail={
                "reason": "SERIALIZATION_BUSY",
                "retry_after_seconds": max(1.0, min(float(wait_seconds) or 1.0, 15.0)),
            },
        )

    try:
        snapshot = _reclaim_and_resample_under_pressure(
            operation, admission_snapshot(operation)
        )
    except Exception as exc:
        # A failed cgroup/RSS/reclamation measurement must never strand the
        # heavyweight lock or accidentally allow unmeasured scoring.
        _HEAVY_JOB_LOCK.release()
        raise HeavyJobDeferred(
            code="MEMORY_ADMISSION_MEASUREMENT_FAILED",
            operation=operation,
            detail={
                "reason": "MEMORY_ADMISSION_MEASUREMENT_FAILED",
                "error_type": type(exc).__name__,
                "retry_after_seconds": DEFAULT_PRESSURE_RETRY_SECONDS,
            },
        ) from exc
    if snapshot["under_pressure"]:
        _HEAVY_JOB_LOCK.release()
        _raise_memory_pressure(operation, snapshot)

    snapshot["heavy_slot_busy"] = True
    return HeavyJobPermit(operation=operation, admission=snapshot)


def release_process_memory() -> None:
    """Best-effort reclamation after bounded in-process heavyweight work.

    GC drops unreachable request/corpus objects and malloc_trim returns free
    glibc arenas to the OS when available. Reclamation is runtime-only and
    never changes probability, calibration, routing, ranking, or execution.
    """
    gc.collect()
    try:
        malloc_trim = getattr(ctypes.CDLL(None), "malloc_trim", None)
        if malloc_trim is not None:
            malloc_trim.argtypes = [ctypes.c_size_t]
            malloc_trim.restype = ctypes.c_int
            malloc_trim(0)
    except Exception:
        pass


def pressure_retry_seconds() -> float:
    return _float_env(
        "WOW_V17_MEMORY_PRESSURE_RETRY_SECONDS",
        DEFAULT_PRESSURE_RETRY_SECONDS,
        minimum=5.0,
        maximum=300.0,
    )


def _reset_for_tests() -> None:
    global _PRESSURE_ACTIVE, _MISSING_SAMPLE_WARNED, _INTERACTIVE_WAITERS, _LAST_RECLAIM_MONOTONIC
    with _PRESSURE_STATE_LOCK:
        _PRESSURE_ACTIVE = False
    with _PRIORITY_STATE_LOCK:
        _INTERACTIVE_WAITERS = 0
    _MISSING_SAMPLE_WARNED = False
    with _RECLAIM_STATE_LOCK:
        _LAST_RECLAIM_MONOTONIC = None
    if _HEAVY_JOB_LOCK.locked():
        _HEAVY_JOB_LOCK.release()


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_PRESSURE_RETRY_SECONDS",
    "DEFAULT_RESUME_RATIO",
    "DEFAULT_STOP_RATIO",
    "HeavyJobDeferred",
    "HeavyJobPermit",
    "MemorySample",
    "acquire_heavy_job",
    "admission_snapshot",
    "memory_sample",
    "pressure_retry_seconds",
    "release_process_memory",
    "try_acquire_heavy_job",
]
