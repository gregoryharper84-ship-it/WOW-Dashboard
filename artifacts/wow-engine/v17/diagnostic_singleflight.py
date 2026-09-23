"""Burst single-flight for read-only V17 diagnostic probes.

Concurrent health/governance requests may otherwise multiply the same remote
Supabase/Redis reads on a small production instance. This primitive coalesces
only requests that overlap in time. It does not TTL-cache a successful result,
so a later request always performs a fresh fail-closed probe.

It has no sporting-probability, ranking, calibration, or execution authority.
"""
from __future__ import annotations

from threading import Lock
from typing import Callable, Generic, TypeVar, cast


T = TypeVar("T")
_UNSET = object()


class BurstSingleFlight(Generic[T]):
    """Run one probe for a concurrent request burst and share its exact outcome.

    Callers that begin while a probe is in flight observe its generation and,
    after waiting for the probe lock, reuse that completed outcome. A caller
    that begins after completion observes the new generation and performs a new
    probe. Therefore there is no time-based optimistic cache window.
    """

    def __init__(self) -> None:
        self._state_lock = Lock()
        self._probe_lock = Lock()
        self._generation = 0
        self._outcome: object = _UNSET

    def run(self, probe: Callable[[], T]) -> T:
        with self._state_lock:
            observed_generation = self._generation

        with self._probe_lock:
            with self._state_lock:
                if self._generation != observed_generation and self._outcome is not _UNSET:
                    succeeded, value = cast(tuple[bool, object], self._outcome)
                    if succeeded:
                        return cast(T, value)
                    raise cast(Exception, value)

            try:
                result = probe()
            except Exception as exc:
                with self._state_lock:
                    self._generation += 1
                    self._outcome = (False, exc)
                raise

            with self._state_lock:
                self._generation += 1
                self._outcome = (True, result)
            return result


__all__ = ["BurstSingleFlight"]
