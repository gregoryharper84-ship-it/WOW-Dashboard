"""Maintenance routes must run under shared heavyweight memory admission.

Regression for the Oct 3-9 2026 production OOM loop: GitHub-scheduled
model-maintenance sweeps (basketball, NCAAF, NCAAB, soccer, team-state)
ran in-process outside the heavy slot, stacked on Daily/Scout work, and the
512 MiB web service was OOM-killed ~15x/day.
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from v17 import basketball_model_maintenance as basketball
from v17 import first_six_open_data_maintenance as first_six
from v17 import memory_admission
from v17 import nhl_model_maintenance as nhl


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    memory_admission._reset_for_tests()
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (50, 100))
    yield
    memory_admission._reset_for_tests()


def _basketball_client(monkeypatch, calls):
    monkeypatch.setattr(basketball, "scout_route_auth_dependency", lambda dep: dep)
    monkeypatch.setattr(
        basketball,
        "run_basketball_model_maintenance",
        lambda db: calls.append(db) or {"status": "OK", "probability_publishable": False, "can_execute": False},
    )
    app = FastAPI()
    basketball.install_basketball_model_maintenance_route(
        app, auth_dependency=Depends(lambda: None), db_client_fn=lambda: "db"
    )
    return TestClient(app)


def _assert_deferral(response, code):
    assert response.status_code == 503
    assert int(response.headers["Retry-After"]) >= 1
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["status"] == "DEFERRED"
    assert detail["terminal"] is False
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_admitted_maintenance_runs_unchanged_and_releases_slot(monkeypatch):
    calls: list = []
    response = _basketball_client(monkeypatch, calls).post("/internal/v17/basketball-model-maintenance")
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "probability_publishable": False, "can_execute": False}
    assert calls == ["db"]
    assert memory_admission.admission_snapshot("PROBE")["heavy_slot_busy"] is False


def test_busy_heavy_slot_defers_without_running_job(monkeypatch):
    calls: list = []
    client = _basketball_client(monkeypatch, calls)
    held = memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT")
    assert held is not None
    try:
        _assert_deferral(client.post("/internal/v17/basketball-model-maintenance"), "HEAVY_JOB_BUSY")
    finally:
        held.release()
    assert calls == []


def test_memory_pressure_defers_without_running_job(monkeypatch):
    calls: list = []
    client = _basketball_client(monkeypatch, calls)
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (90, 100))
    _assert_deferral(client.post("/internal/v17/basketball-model-maintenance"), "MEMORY_PRESSURE")
    assert calls == []
    assert memory_admission.admission_snapshot("PROBE")["heavy_slot_busy"] is False


def test_job_exception_still_releases_slot(monkeypatch):
    monkeypatch.setattr(nhl, "scout_route_auth_dependency", lambda dep: dep)

    def boom(db):
        raise RuntimeError("fit failed")

    monkeypatch.setattr(nhl, "run_nhl_model_maintenance", boom)
    app = FastAPI()
    nhl.install_nhl_model_maintenance_route(app, auth_dependency=Depends(lambda: None), db_client_fn=lambda: "db")
    response = TestClient(app, raise_server_exceptions=False).post("/internal/v17/nhl-model-maintenance")
    assert response.status_code == 500
    assert memory_admission.admission_snapshot("PROBE")["heavy_slot_busy"] is False


def test_first_six_training_routes_are_admitted(monkeypatch):
    monkeypatch.setattr(first_six, "scout_route_auth_dependency", lambda dep: dep)
    app = FastAPI()
    first_six.install_first_six_open_data_maintenance_routes(
        app, auth_dependency=Depends(lambda: None), db_client_fn=lambda: "db"
    )
    client = TestClient(app)
    held = memory_admission.try_acquire_heavy_job("SCOUT_HANDOFF")
    assert held is not None
    try:
        for path in (
            "/internal/v17/ncaab-model-maintenance",
            "/internal/v17/soccer-model-maintenance",
            "/internal/v17/tennis-model-maintenance",
            "/internal/v17/team-state-challenger-maintenance",
            "/internal/v17/team-state-challenger-maintenance/NBA",
        ):
            _assert_deferral(client.post(path), "HEAVY_JOB_BUSY")
    finally:
        held.release()
    # A deferred scope must not leave a stale single-flight entry behind.
    assert first_six._TEAM_STATE_INFLIGHT == {}
