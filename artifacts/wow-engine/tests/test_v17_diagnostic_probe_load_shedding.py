from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest
from fastapi import FastAPI, HTTPException

from v17.diagnostic_probe_load_shedding import (
    ProbeBusy,
    SingleFlightProbe,
    install_diagnostic_probe_load_shedding,
)


def _get_route(app: FastAPI, path: str):
    matches = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == path
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    assert len(matches) == 1
    return matches[0]


def test_singleflight_coalesces_overlapping_calls_but_never_reuses_sequential_state():
    probe = SingleFlightProbe(wait_timeout_seconds=1.0)
    workers = 20
    barrier = Barrier(workers)
    count_lock = Lock()
    calls = 0

    def loader():
        nonlocal calls
        with count_lock:
            calls += 1
        time.sleep(0.08)
        return {"status": "ok", "can_execute": False}

    def invoke():
        barrier.wait(timeout=2.0)
        return probe.run(loader)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [future.result(timeout=3.0) for future in [pool.submit(invoke) for _ in range(workers)]]

    assert calls == 1
    assert all(result == {"status": "ok", "can_execute": False} for result in results)

    # No TTL/stale-success window: a later sequential readiness/governance call
    # performs a new authoritative probe.
    assert probe.run(loader) == {"status": "ok", "can_execute": False}
    assert calls == 2


def test_singleflight_waiters_fail_closed_when_leader_exceeds_bound():
    probe = SingleFlightProbe(wait_timeout_seconds=0.03)
    entered = Event()
    release = Event()

    def loader():
        entered.set()
        assert release.wait(timeout=2.0)
        return {"status": "ok", "can_execute": False}

    with ThreadPoolExecutor(max_workers=1) as pool:
        leader = pool.submit(probe.run, loader)
        assert entered.wait(timeout=1.0)
        with pytest.raises(ProbeBusy):
            probe.run(loader)
        release.set()
        assert leader.result(timeout=2.0)["can_execute"] is False


def test_singleflight_preserves_original_http_failure_for_all_waiters():
    probe = SingleFlightProbe(wait_timeout_seconds=1.0)
    workers = 8
    barrier = Barrier(workers)
    count_lock = Lock()
    calls = 0

    def loader():
        nonlocal calls
        with count_lock:
            calls += 1
        time.sleep(0.05)
        raise HTTPException(status_code=503, detail={"code": "DEPENDENCY_DOWN", "can_execute": False})

    def invoke():
        barrier.wait(timeout=2.0)
        try:
            probe.run(loader)
        except HTTPException as exc:
            return exc.status_code, exc.detail
        raise AssertionError("expected fail-closed HTTPException")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [future.result(timeout=3.0) for future in [pool.submit(invoke) for _ in range(workers)]]

    assert calls == 1
    assert all(status == 503 for status, _ in results)
    assert all(detail == {"code": "DEPENDENCY_DOWN", "can_execute": False} for _, detail in results)


def test_installer_preserves_contract_and_coalesces_ready_and_governance():
    app = FastAPI()
    state_lock = Lock()
    ready_calls = 0
    governance_calls = 0

    @app.get("/health/live")
    def original_live():
        return {"status": "ok", "can_execute": False}

    @app.get("/health/ready")
    def original_ready():
        nonlocal ready_calls
        with state_lock:
            ready_calls += 1
        time.sleep(0.08)
        return {
            "status": "ok",
            "database": "ok",
            "queue": "ok",
            "worker_registry": "ok",
            "can_execute": False,
        }

    @app.get("/governance", operation_id="getWowV17Governance")
    def original_governance():
        nonlocal governance_calls
        with state_lock:
            governance_calls += 1
        time.sleep(0.08)
        return {
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "probability_publishable": None,
            "can_execute": False,
        }

    assert install_diagnostic_probe_load_shedding(app) is True
    assert install_diagnostic_probe_load_shedding(app) is True

    live_route = _get_route(app, "/health/live")
    ready_route = _get_route(app, "/health/ready")
    governance_route = _get_route(app, "/governance")
    assert governance_route.operation_id == "getWowV17Governance"
    assert asyncio.run(live_route.endpoint()) == {"status": "ok", "can_execute": False}

    workers = 12
    ready_barrier = Barrier(workers)

    def ready_call():
        ready_barrier.wait(timeout=2.0)
        return ready_route.endpoint()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ready_results = [future.result(timeout=3.0) for future in [pool.submit(ready_call) for _ in range(workers)]]

    assert ready_calls == 1
    assert all(result["status"] == "ok" for result in ready_results)
    assert all(result["can_execute"] is False for result in ready_results)

    governance_barrier = Barrier(workers)

    def governance_call():
        governance_barrier.wait(timeout=2.0)
        return governance_route.endpoint()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        governance_results = [
            future.result(timeout=3.0)
            for future in [pool.submit(governance_call) for _ in range(workers)]
        ]

    assert governance_calls == 1
    assert all(result["global_terminal_authority"] == "V17_TERMINAL_REDUCER" for result in governance_results)
    assert all(result["probability_publishable"] is None for result in governance_results)
    assert all(result["can_execute"] is False for result in governance_results)

    # Sequential calls remain fresh rather than relying on cached optimistic state.
    ready_route.endpoint()
    governance_route.endpoint()
    assert ready_calls == 2
    assert governance_calls == 2
