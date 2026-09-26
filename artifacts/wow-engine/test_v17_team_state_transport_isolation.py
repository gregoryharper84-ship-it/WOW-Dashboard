from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from v17 import first_six_open_data_maintenance as maintenance


def _result(scope: str) -> dict:
    return {
        "status": "COMPLETED_WITH_EVIDENCE",
        "program": "LLP_DYNAMIC_TEAM_STATE_CHALLENGER_V1",
        "scope": scope,
        "rows": [{"sport": scope, "status": "CANDIDATE_EVIDENCE_UPDATED", "can_execute": False}],
        "candidate_rows_updated": 1,
        "candidate_rows_blocked": 0,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_source_heavy_scope_fails_closed_to_runner_without_db_client(monkeypatch):
    monkeypatch.setattr(maintenance, "_sha", lambda: "a" * 40)
    db_calls = []

    result = maintenance._run_team_state_scope_singleflight(
        lambda: db_calls.append("db") or object(),
        "MLB",
    )

    assert db_calls == []
    assert result["status"] == "BLOCKED"
    assert result["code"] == "TEAM_STATE_SOURCE_HEAVY_RUNNER_REQUIRED"
    assert result["runner_isolation_required"] is True
    assert result["rows"][0]["status"] == "BLOCKED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_same_scope_retry_joins_single_inflight_computation(monkeypatch):
    monkeypatch.setattr(maintenance, "_sha", lambda: "b" * 40)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    trims = []

    def fake_run(db, scope):
        calls.append((db, scope))
        entered.set()
        assert release.wait(timeout=2)
        return _result(scope)

    monkeypatch.setattr(maintenance, "_run_team_state_scope", fake_run)
    monkeypatch.setattr(maintenance, "_release_process_memory", lambda: trims.append("trim"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(maintenance._run_team_state_scope_singleflight, lambda: "db-1", "NFL")
        assert entered.wait(timeout=1)
        second = pool.submit(maintenance._run_team_state_scope_singleflight, lambda: "db-2", "NFL")
        time.sleep(0.05)
        assert len(calls) == 1
        release.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert first_result == second_result
    assert calls == [("db-1", "NFL")]
    assert trims == ["trim"]
    assert maintenance._TEAM_STATE_INFLIGHT == {}


def test_different_lightweight_scopes_are_serialized(monkeypatch):
    monkeypatch.setattr(maintenance, "_sha", lambda: "c" * 40)
    active = 0
    max_active = 0
    state_lock = threading.Lock()

    def fake_run(db, scope):
        nonlocal active, max_active
        with state_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        return _result(scope)

    monkeypatch.setattr(maintenance, "_run_team_state_scope", fake_run)
    monkeypatch.setattr(maintenance, "_release_process_memory", lambda: None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        nfl = pool.submit(maintenance._run_team_state_scope_singleflight, lambda: "db-nfl", "NFL")
        nba = pool.submit(maintenance._run_team_state_scope_singleflight, lambda: "db-nba", "NBA")
        assert nfl.result(timeout=2)["scope"] == "NFL"
        assert nba.result(timeout=2)["scope"] == "NBA"

    assert max_active == 1
    assert maintenance._TEAM_STATE_INFLIGHT == {}


def test_runner_only_scope_set_covers_all_source_heavy_team_state_lanes():
    assert maintenance._TEAM_STATE_RUNNER_ONLY_SCOPES == {
        "MLB",
        "NCAAB",
        "SOCCER_EPL",
        "SOCCER_BUNDESLIGA",
        "SOCCER_LALIGA",
        "SOCCER_SERIE_A",
        "SOCCER_LIGUE_1",
    }
