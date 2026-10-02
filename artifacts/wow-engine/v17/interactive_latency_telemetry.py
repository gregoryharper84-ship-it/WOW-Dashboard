"""Non-secret latency telemetry for the governed interactive Action surface.

This module is observability-only. It must not inspect request bodies, alter
sporting probabilities, change terminal semantics, or enable execution.
"""
from __future__ import annotations

import logging
import math
import threading
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Dict, Iterator, List, Optional, Tuple


LOGGER = logging.getLogger("wow.v17.interactive_latency")
INTERACTIVE_PATHS = frozenset({
    "/score-prop",
    "/score-pick-request",
    "/score-team-event-request",
    "/score-team-event",
})
STAGE_NAMES = (
    "discovery",
    "hydration",
    "fitted_scoring",
    "calibration_bounds",
    "persistence",
    "final_refresh",
    "reconciliation",
)
MAX_SAMPLES_PER_KEY = 512
MAX_AGGREGATE_KEYS = 256

_CURRENT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("wow_interactive_latency", default=None)


class LatencyAggregator:
    """Bounded in-process p50/p95 store keyed by route/sport/row_count/batch_size."""

    def __init__(self, max_samples: int = MAX_SAMPLES_PER_KEY, max_keys: int = MAX_AGGREGATE_KEYS) -> None:
        self._max_samples = max_samples
        self._max_keys = max_keys
        self._lock = threading.Lock()
        self._samples: Dict[Tuple[Any, ...], deque] = {}

    def record(self, route: str, sport: str, row_count: int, batch_size: int, total_ms: float) -> None:
        key = (route, sport, int(row_count), int(batch_size))
        with self._lock:
            bucket = self._samples.get(key)
            if bucket is None:
                if len(self._samples) >= self._max_keys:
                    self._samples.pop(next(iter(self._samples)))
                bucket = self._samples[key] = deque(maxlen=self._max_samples)
            bucket.append(float(total_ms))

    @staticmethod
    def _percentile(ordered: List[float], pct: int) -> float:
        # nearest-rank percentile
        rank = max(1, math.ceil(pct * len(ordered) / 100))
        return ordered[min(rank, len(ordered)) - 1]

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = [(k, sorted(v)) for k, v in self._samples.items() if v]
        return [
            {
                "route": k[0],
                "sport": k[1],
                "row_count": k[2],
                "batch_size": k[3],
                "samples": len(v),
                "p50_ms": self._percentile(v, 50),
                "p95_ms": self._percentile(v, 95),
                "can_execute": False,
            }
            for k, v in items
        ]

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()


AGGREGATOR = LatencyAggregator()


def set_request_dimensions(
    *, sport: Optional[str] = None, row_count: Optional[int] = None, batch_size: Optional[int] = None
) -> None:
    """Attach non-secret dimensions to the current interactive request. Never raises."""
    try:
        state = _CURRENT.get()
        if state is None:
            return
        if sport is not None:
            state["sport"] = str(sport)[:32]
        if row_count is not None:
            state["row_count"] = int(row_count)
        if batch_size is not None:
            state["batch_size"] = int(batch_size)
    except Exception:  # observability must never alter the request path
        return


@contextmanager
def stage_timer(name: str) -> Iterator[None]:
    """Time one named stage on the current request. Side-effect free; never raises."""
    started = perf_counter()
    try:
        yield
    finally:
        try:
            state = _CURRENT.get()
            if state is not None:
                stages = state["stages"]
                stages[name] = stages.get(name, 0.0) + (perf_counter() - started) * 1000.0
        except Exception:
            pass


def install_interactive_latency_middleware(app: Any) -> None:
    """Install one idempotent total-wall-time probe for interactive V17 routes."""
    if getattr(app.state, "wow_interactive_latency_installed", False):
        return

    @app.middleware("http")
    async def _interactive_latency_probe(request: Any, call_next: Any):
        path = str(getattr(request.url, "path", ""))
        if path not in INTERACTIVE_PATHS:
            return await call_next(request)

        started = perf_counter()
        status_code = 500
        state: Dict[str, Any] = {"stages": {}, "sport": "unknown", "row_count": 0, "batch_size": 0}
        token = _CURRENT.set(state)
        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            return response
        finally:
            total_ms = (perf_counter() - started) * 1000.0
            _CURRENT.reset(token)
            stages_text = ",".join(f"{k}={v:.3f}" for k, v in state["stages"].items()) or "none"
            LOGGER.warning(
                "WOW_V17_INTERACTIVE_LATENCY route=%s method=%s status_code=%s total_ms=%.3f "
                "sport=%s row_count=%s batch_size=%s stages_ms=%s can_execute=false",
                path,
                str(getattr(request, "method", "UNKNOWN")),
                status_code,
                total_ms,
                state["sport"],
                state["row_count"],
                state["batch_size"],
                stages_text,
            )
            try:
                AGGREGATOR.record(path, state["sport"], state["row_count"], state["batch_size"], total_ms)
            except Exception:
                pass

    app.state.wow_interactive_latency_installed = True
