from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from time import sleep

import pytest

from v17.diagnostic_singleflight import BurstSingleFlight


def test_concurrent_burst_executes_probe_once_and_shares_exact_result():
    singleflight: BurstSingleFlight[dict[str, object]] = BurstSingleFlight()
    callers = 24
    barrier = Barrier(callers)
    count_lock = Lock()
    probe_calls = 0

    def probe() -> dict[str, object]:
        nonlocal probe_calls
        with count_lock:
            probe_calls += 1
        sleep(0.08)
        return {"status": "ok", "can_execute": False}

    def caller() -> dict[str, object]:
        barrier.wait()
        return singleflight.run(probe)

    with ThreadPoolExecutor(max_workers=callers) as pool:
        results = list(pool.map(lambda _: caller(), range(callers)))

    assert probe_calls == 1
    assert results == [{"status": "ok", "can_execute": False}] * callers


def test_later_non_overlapping_request_always_probes_fresh():
    singleflight: BurstSingleFlight[int] = BurstSingleFlight()
    probe_calls = 0

    def probe() -> int:
        nonlocal probe_calls
        probe_calls += 1
        return probe_calls

    assert singleflight.run(probe) == 1
    assert singleflight.run(probe) == 2
    assert probe_calls == 2


def test_concurrent_failure_is_shared_but_not_cached_for_later_request():
    singleflight: BurstSingleFlight[str] = BurstSingleFlight()
    callers = 16
    barrier = Barrier(callers)
    count_lock = Lock()
    probe_calls = 0

    def failing_probe() -> str:
        nonlocal probe_calls
        with count_lock:
            probe_calls += 1
        sleep(0.08)
        raise RuntimeError("dependency unavailable")

    def caller() -> str:
        barrier.wait()
        with pytest.raises(RuntimeError, match="dependency unavailable"):
            singleflight.run(failing_probe)
        return "failed_closed"

    with ThreadPoolExecutor(max_workers=callers) as pool:
        results = list(pool.map(lambda _: caller(), range(callers)))

    assert probe_calls == 1
    assert results == ["failed_closed"] * callers

    # A later request is never served an old failure (or old success).
    assert singleflight.run(lambda: "fresh") == "fresh"
