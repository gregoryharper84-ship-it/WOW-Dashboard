from __future__ import annotations

from types import SimpleNamespace
import threading
import time

import pytest
from fastapi import HTTPException

import v17.interactive_team_event_latency as latency
import v17.team_event_request_runtime as team_runtime


_CANONICAL_BARRIER = team_runtime._run_mandatory_scout_research


def _req():
    return SimpleNamespace(
        requester_host_identity="WOW_CUSTOM_GPT",
        candidate_family="TEAM_EVENT",
        research_run_id="latency-test",
        event_key="WNBA:test-event",
        sport="WNBA",
        league="WNBA",
        official_event_id="test-event",
        market_family="OUTRIGHT_WINNER",
        event_start_time_utc="2099-09-21T20:00:00+00:00",
        sport_specific_evidence={"status_freshness_hours": 0.25},
    )


def _out(worker_id: str, *, status: str = "SUCCEEDED", blockers=None):
    return SimpleNamespace(
        status=status,
        blockers=list(blockers or []),
        output={"worker_id": worker_id, "research_status": "PASS"},
    )


def _isolate_installer(monkeypatch, workers):
    # The production installer intentionally mutates the canonical barrier once
    # per process. Register that global with pytest's monkeypatch before calling
    # the installer so every test restores the original runtime afterward.
    monkeypatch.setattr(
        team_runtime,
        "_run_mandatory_scout_research",
        _CANONICAL_BARRIER,
    )
    monkeypatch.setattr(team_runtime, latency._STATE_KEY, False, raising=False)
    # Keep the concurrency assertion deterministic even if a CI environment
    # overrides the production worker-count environment variable.
    monkeypatch.setattr(latency, "_worker_count", lambda: len(workers))


def test_team_event_research_workers_overlap_and_reconcile_in_canonical_order(monkeypatch):
    workers = ("research-1", "research-2", "research-3")
    _isolate_installer(monkeypatch, workers)
    monkeypatch.setattr(team_runtime, "RESEARCH_WORKERS", workers)
    monkeypatch.setattr(team_runtime, "RESEARCH_RECONCILER", "reconciler")
    monkeypatch.setattr(team_runtime, "scout_lane", lambda _candidate: "TEAM_EVENT")
    monkeypatch.setattr(
        team_runtime,
        "_scout_research_envelope",
        lambda _run_id, _candidate_id, worker_id, payload: SimpleNamespace(
            worker_id=worker_id, payload=payload
        ),
    )

    lock = threading.Lock()
    all_research_started = threading.Event()
    active = 0
    max_active = 0
    starts = []
    reconciler_report_order = []

    def execute(env):
        nonlocal active, max_active
        starts.append(env.worker_id)
        if env.worker_id in workers:
            with lock:
                active += 1
                max_active = max(max_active, active)
                if active == len(workers):
                    all_research_started.set()
            assert all_research_started.wait(0.75), "research workers executed serially"
            time.sleep(0.02)
            with lock:
                active -= 1
        if env.worker_id == "reconciler":
            reconciler_report_order.extend(
                report["worker_id"] for report in env.payload["research_reports"]
            )
        return _out(env.worker_id)

    monkeypatch.setattr(team_runtime, "execute_envelope", execute)
    assert latency.install_interactive_team_event_latency() is True

    result = team_runtime._run_mandatory_scout_research(_req())

    assert result["status"] == "SUCCEEDED"
    assert max_active == len(workers)
    assert starts[0:2] == ["wow.global-scout-coordinator", "wow.ml-event-scout-router"]
    assert starts[-1] == "reconciler"
    assert reconciler_report_order == list(workers)
    assert [stage["worker_id"] for stage in result["stages"]] == [
        "wow.global-scout-coordinator",
        "wow.ml-event-scout-router",
        *workers,
        "reconciler",
    ]


def test_team_event_parallel_barrier_preserves_reconciler_blocker(monkeypatch):
    workers = ("research-1", "research-2")
    _isolate_installer(monkeypatch, workers)
    monkeypatch.setattr(team_runtime, "RESEARCH_WORKERS", workers)
    monkeypatch.setattr(team_runtime, "RESEARCH_RECONCILER", "reconciler")
    monkeypatch.setattr(team_runtime, "scout_lane", lambda _candidate: "TEAM_EVENT")
    monkeypatch.setattr(
        team_runtime,
        "_scout_research_envelope",
        lambda _run_id, _candidate_id, worker_id, payload: SimpleNamespace(
            worker_id=worker_id, payload=payload
        ),
    )

    def execute(env):
        if env.worker_id == "reconciler":
            return _out("reconciler", status="BLOCKED", blockers=["RESEARCH_CONTRADICTION"])
        return _out(env.worker_id)

    monkeypatch.setattr(team_runtime, "execute_envelope", execute)
    assert latency.install_interactive_team_event_latency() is True

    with pytest.raises(HTTPException) as exc_info:
        team_runtime._run_mandatory_scout_research(_req())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "SCOUT_RESEARCH_BARRIER_BLOCKED"
    assert exc_info.value.detail["stage"] == "reconciler"
    assert "RESEARCH_CONTRADICTION" in exc_info.value.detail["blockers"]
    assert exc_info.value.detail["can_execute"] is False
