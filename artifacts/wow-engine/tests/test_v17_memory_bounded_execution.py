from __future__ import annotations

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


def test_shared_heavy_slot_serializes_jobs(monkeypatch):
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: (50, 100))
    first = memory_admission.try_acquire_heavy_job("SCOUT_HANDOFF")
    assert first is not None
    assert memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT") is None
    first.release()
    second = memory_admission.try_acquire_heavy_job("DAILY_SNAPSHOT")
    assert second is not None
    second.release()


def test_missing_cgroup_sample_is_visible_not_silent(monkeypatch):
    monkeypatch.setattr(memory_admission, "_read_cgroup_memory_bytes", lambda: None)
    snapshot = memory_admission.admission_snapshot("TEST")
    assert snapshot["measurement_available"] is False
    assert snapshot["under_pressure"] is False
    assert snapshot["memory_ratio"] is None
    assert snapshot["can_execute"] is False
