from __future__ import annotations

import threading
import time

import pytest

from v17 import daily_snapshot_runtime as daily_runtime
from v17 import memory_admission


@pytest.fixture(autouse=True)
def _reset_memory_admission_state():
    memory_admission._reset_for_tests()
    yield
    memory_admission._reset_for_tests()


def test_memory_pressure_hysteresis_requires_resume_headroom(monkeypatch):
    monkeypatch.setenv("WOW_V17_HEAVY_MEMORY_STOP_RATIO", "0.80")
    monkeypatch.setenv("WOW_V17_HEAVY_MEMORY_RESUME_RATIO", "0.68")
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (81, 100))
    assert memory_admission.admission_snapshot("TEST")["under_pressure"] is True
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (72, 100))
    assert memory_admission.admission_snapshot("TEST")["under_pressure"] is True
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (67, 100))
    assert memory_admission.admission_snapshot("TEST")["under_pressure"] is False


def test_memory_pressure_is_typed_nonterminal_and_does_not_strand_slot(monkeypatch):
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (85, 100))
    with pytest.raises(memory_admission.HeavyJobDeferred) as caught:
        memory_admission.try_acquire_heavy_job("SCOUT_HANDOFF")
    receipt = caught.value.receipt()
    assert receipt["code"] == "MEMORY_PRESSURE"
    assert receipt["status"] == "DEFERRED"
    assert receipt["terminal"] is False
    assert receipt["probability_publishable"] is False
    assert receipt["can_execute"] is False

    memory_admission._reset_for_tests()
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (50, 100))
    permit = memory_admission.try_acquire_heavy_job("SCOUT_HANDOFF")
    assert permit is not None
    permit.release()


def test_bounded_idle_reclaim_can_reopen_slot_only_after_measured_resume(monkeypatch):
    measured = {"current": 90}
    calls = []
    monkeypatch.setattr(
        memory_admission,
        "_read_cgroup_memory_bytes",
        lambda: (measured["current"], 100),
    )
    monkeypatch.setattr(memory_admission.time, "monotonic", lambda: 1_000.0)
    monkeypatch.setattr(
        memory_admission,
        "release_process_memory",
        lambda: (calls.append("gc_and_trim"), measured.update(current=67)),
    )
    permit = memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT")
    assert permit is not None
    assert permit.admission["memory_ratio"] == pytest.approx(0.67)
    assert permit.admission["under_pressure"] is False
    assert calls == ["gc_and_trim"]
    permit.release()


def test_failed_reclaim_preserves_pressure_and_is_rate_limited(monkeypatch):
    clock = {"now": 500.0}
    calls = []
    monkeypatch.setattr(memory_admission.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (94, 100))
    monkeypatch.setattr(memory_admission, "release_process_memory", lambda: calls.append("reclaim"))
    for _ in range(2):
        with pytest.raises(memory_admission.HeavyJobDeferred) as exc:
            memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT")
        assert exc.value.code == "MEMORY_PRESSURE"
        assert exc.value.receipt()["memory_ratio"] == pytest.approx(0.94)
        assert exc.value.receipt()["can_execute"] is False
    assert calls == ["reclaim"]
    clock["now"] = 561.0
    with pytest.raises(memory_admission.HeavyJobDeferred):
        memory_admission.acquire_heavy_job("DAILY_SNAPSHOT", wait_seconds=0.0)
    assert calls == ["reclaim", "reclaim"]


def test_reclaim_cannot_claim_recovery_when_followup_cgroup_sample_vanishes(monkeypatch):
    measured = {"valid": True}
    monkeypatch.setattr(
        memory_admission,
        "_read_cgroup_memory_bytes",
        lambda: (93, 100) if measured["valid"] else None,
    )
    monkeypatch.setattr(
        memory_admission,
        "release_process_memory",
        lambda: measured.update(valid=False),
    )
    with pytest.raises(memory_admission.HeavyJobDeferred) as exc:
        memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT")
    assert exc.value.receipt()["code"] == "MEMORY_PRESSURE"
    assert exc.value.receipt()["memory_ratio"] == pytest.approx(0.93)
    assert exc.value.receipt()["probability_publishable"] is False


def test_snapshot_exposes_process_rss_without_affecting_gate(monkeypatch):
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (50, 100))
    monkeypatch.setattr(memory_admission, "_read_process_rss_bytes", lambda: 12_345_678)
    snapshot = memory_admission.admission_snapshot("TEST")
    assert snapshot["process_rss_bytes"] == 12_345_678
    assert snapshot["memory_current_bytes"] == 50
    assert snapshot["memory_limit_bytes"] == 100
    assert snapshot["under_pressure"] is False


def test_shared_heavy_slot_serializes_jobs(monkeypatch):
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (50, 100))
    first = memory_admission.try_acquire_heavy_job("SCOUT_HANDOFF")
    assert first is not None
    assert memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT") is None
    first.release()
    second = memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT")
    assert second is not None
    second.release()



def test_waiting_interactive_daily_prevents_scout_reacquisition(monkeypatch):
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (50, 100))
    scout = memory_admission.try_acquire_heavy_job("SCOUT_HANDOFF")
    assert scout is not None

    daily_acquired = threading.Event()
    release_daily = threading.Event()
    result = {}

    def acquire_daily():
        try:
            permit = memory_admission.acquire_heavy_job("DAILY_SNAPSHOT", wait_seconds=1.0)
            result["permit"] = permit
            daily_acquired.set()
            release_daily.wait(timeout=1.0)
            permit.release()
        except Exception as exc:
            result["error"] = exc
            daily_acquired.set()

    thread = threading.Thread(target=acquire_daily)
    thread.start()
    deadline = time.monotonic() + 0.5
    while memory_admission.admission_snapshot("TEST")["interactive_waiters"] < 1:
        assert time.monotonic() < deadline
        time.sleep(0.005)

    scout.release()
    assert memory_admission.try_acquire_heavy_job("SCOUT_HANDOFF") is None
    assert daily_acquired.wait(timeout=0.5)
    assert "error" not in result
    assert result.get("permit") is not None

    release_daily.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()


def test_missing_cgroup_sample_is_visible_not_silent(monkeypatch):
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: None)
    snapshot = memory_admission.admission_snapshot("TEST")
    assert snapshot["measurement_available"] is False
    assert snapshot["under_pressure"] is False
    assert snapshot["memory_ratio"] is None
    assert snapshot["can_execute"] is False


class _Permit:
    def __init__(self):
        self.release_count = 0

    def release(self):
        self.release_count += 1


def test_release_process_memory_runs_gc_and_best_effort_trim(monkeypatch):
    calls = []

    class _Trim:
        argtypes = None
        restype = None

        def __call__(self, value):
            calls.append(("trim", value))
            return 1

    class _Lib:
        malloc_trim = _Trim()

    monkeypatch.setattr(memory_admission.gc, "collect", lambda: calls.append(("gc", None)))
    monkeypatch.setattr(memory_admission.ctypes, "CDLL", lambda _name: _Lib())

    memory_admission.release_process_memory()

    assert calls[0] == ("gc", None)
    assert calls[1] == ("trim", 0)


def test_daily_snapshot_owned_permit_is_released(monkeypatch):
    permit = _Permit()
    monkeypatch.setattr(memory_admission, "acquire_heavy_job", lambda operation: permit)
    monkeypatch.setattr(
        daily_runtime,
        "_run_daily_snapshot_impl",
        lambda *args, **kwargs: {"run_id": "r", "can_execute": False},
    )
    monkeypatch.setattr(daily_runtime.source_policy, "begin_paid_budget_scope", lambda: "token")
    monkeypatch.setattr(daily_runtime.source_policy, "end_paid_budget_scope", lambda token: None)

    result = daily_runtime.run_daily_snapshot(
        daily_runtime.DailySnapshotRequest(
            requested_slate_date="2026-10-07",
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
            max_props=0,
            max_team_events=1,
        ),
        db=object(),
        market_api=object(),
        event_api=object(),
    )

    assert result["run_id"] == "r"
    assert permit.release_count == 1


def test_daily_snapshot_preacquired_permit_does_not_reacquire_or_release(monkeypatch):
    permit = _Permit()

    def unexpected_acquire(_operation):
        raise AssertionError("Daily attempted to acquire the heavy slot twice")

    monkeypatch.setattr(memory_admission, "acquire_heavy_job", unexpected_acquire)
    monkeypatch.setattr(
        daily_runtime,
        "_run_daily_snapshot_impl",
        lambda *args, **kwargs: {"run_id": "r", "can_execute": False},
    )
    monkeypatch.setattr(daily_runtime.source_policy, "begin_paid_budget_scope", lambda: "token")
    monkeypatch.setattr(daily_runtime.source_policy, "end_paid_budget_scope", lambda token: None)

    result = daily_runtime.run_daily_snapshot(
        daily_runtime.DailySnapshotRequest(
            requested_slate_date="2026-10-07",
            requested_timezone="America/Chicago",
            lanes=["MONEYLINE"],
            max_props=0,
            max_team_events=1,
        ),
        db=object(),
        market_api=object(),
        event_api=object(),
        _heavy_permit=permit,
    )

    assert result["run_id"] == "r"
    assert permit.release_count == 0
