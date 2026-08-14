"""
gate_engine/mlb/pitcher_prefetch.py
=====================================
Bounded concurrent pitcher identity + Statcast fetch.

Replaces the serial-for-loop acquisition pattern in the MLB pitcher scan.
Each pitcher's pybaseball calls are submitted to a ThreadPoolExecutor so that
a 13-pitcher scan that would take 7+ minutes serially completes in ~40s with
the default 4 workers.

Key properties
--------------
- Worker count: configurable via WOW_PITCHER_FETCH_WORKERS env var (default 4,
  max 8). Conservative by design — pybaseball calls are network I/O, not CPU.
- In-flight deduplication: if two callers request the same pitcher
  simultaneously, only ONE fetch is submitted; both receive the same Future.
- Deterministic output ordering: prefetch_many() returns results in the same
  order as the input list regardless of completion order.
- Partial failure isolation: one pitcher's timeout / exception does not prevent
  others from completing.  The failed pitcher's result dict contains an "error"
  key.
- Prewarm: fire-and-forget background fetch to pre-populate the identity cache
  before the first synchronous request arrives.
- Gunicorn safety: the module-level executor is None until first use in each
  worker process.  The gunicorn post_fork hook sets _executor = None so each
  worker creates its own executor.

No mutable shared state is accessed by the fetch callables — they receive only
their own arguments and call module-level pure functions (identity_lookup_fn /
savant_fetch_fn).
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Callable, Dict, List, Optional, Tuple

# ── Configuration ──────────────────────────────────────────────────────────────

_DEFAULT_WORKERS = 4
_MAX_WORKERS_CAP = 8
_PER_PITCHER_TIMEOUT_S = 45


def _max_workers() -> int:
    try:
        w = int(os.environ.get("WOW_PITCHER_FETCH_WORKERS", str(_DEFAULT_WORKERS)))
        return max(1, min(w, _MAX_WORKERS_CAP))
    except (ValueError, TypeError):
        return _DEFAULT_WORKERS


# ── Module-level executor (per-worker, lazy) ───────────────────────────────────

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_inflight: Dict[str, Future] = {}
_inflight_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Return the worker-local executor, creating it on first use."""
    global _executor
    if _executor is None or _executor._shutdown:  # type: ignore[attr-defined]
        with _executor_lock:
            if _executor is None or _executor._shutdown:  # type: ignore[attr-defined]
                _executor = ThreadPoolExecutor(
                    max_workers=_max_workers(),
                    thread_name_prefix="wow-pitcher-fetch",
                )
    return _executor


def reset_for_new_worker() -> None:
    """Called by gunicorn post_fork to give each worker a fresh executor."""
    global _executor, _inflight
    with _executor_lock:
        _executor = None
    with _inflight_lock:
        _inflight = {}


# ── Key normalisation ──────────────────────────────────────────────────────────

def _dedup_key(first: str, last: str) -> str:
    return f"{last.strip().lower()}_{first.strip().lower()}"


# ── Core fetch helpers ────────────────────────────────────────────────────────

def _do_fetch(
    first: str,
    last: str,
    identity_fn: Callable[[str, str], Optional[int]],
    savant_fn: Callable[[str, str], dict],
) -> dict:
    """Execute identity lookup + Savant fetch for one pitcher.  Runs in a thread."""
    t0 = time.monotonic()
    try:
        mlbam_id = identity_fn(first, last)
        savant   = savant_fn(first, last)
        return {
            "first":   first,
            "last":    last,
            "mlbam_id": mlbam_id,
            "savant":  savant,
            "elapsed_s": round(time.monotonic() - t0, 2),
            "ok":      True,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "first":   first,
            "last":    last,
            "mlbam_id": None,
            "savant":  {"error": str(exc)[:200]},
            "elapsed_s": round(time.monotonic() - t0, 2),
            "ok":      False,
            "error":   str(exc)[:200],
        }


# ── Public API ────────────────────────────────────────────────────────────────

def prefetch_one(
    first: str,
    last: str,
    identity_fn: Callable[[str, str], Optional[int]],
    savant_fn: Callable[[str, str], dict],
    timeout: float = _PER_PITCHER_TIMEOUT_S,
) -> dict:
    """Fetch identity + Savant for one pitcher, with in-flight deduplication.

    If a fetch for this pitcher is already in-flight, this call blocks on the
    existing Future rather than spawning a duplicate network request.
    """
    key = _dedup_key(first, last)
    fut: Optional[Future] = None

    with _inflight_lock:
        existing = _inflight.get(key)
        if existing is not None and not existing.done():
            fut = existing  # coalesce onto the in-flight request
        else:
            fut = _get_executor().submit(_do_fetch, first, last, identity_fn, savant_fn)
            _inflight[key] = fut

    try:
        result = fut.result(timeout=timeout)
    except FuturesTimeout:
        result = {
            "first":   first,
            "last":    last,
            "mlbam_id": None,
            "savant":  {"error": "FETCH_TIMEOUT"},
            "elapsed_s": timeout,
            "ok":      False,
            "error":   "FETCH_TIMEOUT",
        }
    except Exception as exc:  # noqa: BLE001
        result = {
            "first":   first,
            "last":    last,
            "mlbam_id": None,
            "savant":  {"error": str(exc)[:200]},
            "elapsed_s": 0.0,
            "ok":      False,
            "error":   str(exc)[:200],
        }
    finally:
        # Clean up completed futures to prevent unbounded growth
        with _inflight_lock:
            if _inflight.get(key) is fut:
                _inflight.pop(key, None)

    return result


def prefetch_many(
    pitchers: List[Tuple[str, str]],
    identity_fn: Callable[[str, str], Optional[int]],
    savant_fn: Callable[[str, str], dict],
    timeout: float = _PER_PITCHER_TIMEOUT_S,
) -> List[dict]:
    """Concurrently fetch all pitchers; return results in INPUT ORDER.

    Each pitcher's fetch is independent — one failure does not affect others.
    Futures are submitted before any blocking wait so the executor can run them
    in parallel up to max_workers.
    """
    if not pitchers:
        return []

    dedup_keys = [_dedup_key(f, l) for f, l in pitchers]
    executor   = _get_executor()

    # Phase 1: submit all (coalescing in-flight duplicates)
    futures: list[Future] = []
    with _inflight_lock:
        for (first, last), key in zip(pitchers, dedup_keys):
            existing = _inflight.get(key)
            if existing is not None and not existing.done():
                futures.append(existing)
            else:
                fut = executor.submit(_do_fetch, first, last, identity_fn, savant_fn)
                _inflight[key] = fut
                futures.append(fut)

    # Phase 2: collect in INPUT order (deterministic)
    results: list[dict] = []
    for (first, last), key, fut in zip(pitchers, dedup_keys, futures):
        try:
            r = fut.result(timeout=timeout)
        except FuturesTimeout:
            r = {
                "first":   first,
                "last":    last,
                "mlbam_id": None,
                "savant":  {"error": "FETCH_TIMEOUT"},
                "elapsed_s": timeout,
                "ok":      False,
                "error":   "FETCH_TIMEOUT",
            }
        except Exception as exc:  # noqa: BLE001
            r = {
                "first":   first,
                "last":    last,
                "mlbam_id": None,
                "savant":  {"error": str(exc)[:200]},
                "elapsed_s": 0.0,
                "ok":      False,
                "error":   str(exc)[:200],
            }
        finally:
            with _inflight_lock:
                if _inflight.get(key) is fut:
                    _inflight.pop(key, None)
        results.append(r)

    return results


def prewarm(
    pitchers: List[Tuple[str, str]],
    identity_fn: Callable[[str, str], Optional[int]],
    savant_fn: Callable[[str, str], dict],
) -> None:
    """Fire-and-forget background prewarm.

    Submits all pitchers to the executor without waiting for results.  The
    identity cache will be populated by the time the first synchronous request
    arrives.  Duplicate in-flight entries are coalesced as usual.
    """
    executor = _get_executor()
    with _inflight_lock:
        for first, last in pitchers:
            key = _dedup_key(first, last)
            existing = _inflight.get(key)
            if existing is None or existing.done():
                fut = executor.submit(_do_fetch, first, last, identity_fn, savant_fn)
                _inflight[key] = fut
