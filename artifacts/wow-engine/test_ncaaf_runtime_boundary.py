import asyncio
import logging
from pathlib import Path
import time

import pytest
from fastapi import HTTPException

import api_ncaaf_acceptance as api


def test_readiness_reports_external_and_model_blockers(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    monkeypatch.setattr(api, "_safe_count", lambda table: 0)
    monkeypatch.setattr(api, "_artifact_state", lambda: {"ok": False, "code": "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"})
    monkeypatch.setattr(api, "_calibrator_state", lambda version: {"ok": False, "code": "NCAAF_MODEL_ARTIFACT_UNAVAILABLE"})

    result = api.ncaaf_readiness()
    assert result["ncaaf_controlling_model"] == "MODEL_UNAVAILABLE"
    assert result["ncaaf_trust_state"] == "NCAAF_TEST_ONLY"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert "CFBD_API_KEY_MISSING" in result["blockers"]
    assert "NCAAF_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND" in result["blockers"]


def test_hydration_missing_key_fails_closed(monkeypatch):
    monkeypatch.delenv("CFBD_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        api.hydrate_ncaaf_history(2025, 1, 1)
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "CFBD_API_KEY_MISSING"
    assert exc.value.detail["probability_publishable"] is False
    assert exc.value.detail["can_execute"] is False


def test_runtime_contract_is_service_role_only_and_non_executable():
    sql = Path("ncaaf_runtime_contract.sql").read_text()
    assert "enable row level security" in sql.lower()
    assert "revoke all on table public.wow_ncaaf_event_feature_snapshots" in sql.lower()
    assert "grant all on table public.wow_ncaaf_event_feature_snapshots" in sql.lower()
    assert "to service_role" in sql.lower()
    assert "security invoker" in sql.lower()
    assert "can_execute = false" in sql.lower()
    assert "feature_as_of < event_start_time" in sql
    assert "injury_evidence_timestamp < event_start_time" in sql


def test_readiness_and_hydration_routes_are_authenticated():
    source = Path("api_ncaaf_acceptance.py").read_text()
    assert '"/internal/ncaaf/readiness"' in source
    assert '"/internal/ncaaf/hydrate-history"' in source
    assert "dependencies=[_auth]" in source
    assert "probability_publishable\": False" in source
    assert "can_execute\": False" in source


def test_startup_handler_only_schedules_readiness_audit(monkeypatch):
    scheduled = []

    class _DummyTask:
        def add_done_callback(self, _callback):
            return None

        def __hash__(self):
            return id(self)

    def fake_create_task(coro):
        scheduled.append(coro)
        coro.close()
        return _DummyTask()

    monkeypatch.setattr(api.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(
        api,
        "ncaaf_readiness",
        lambda: (_ for _ in ()).throw(AssertionError("startup must not call readiness synchronously")),
    )
    api._background_tasks.clear()

    started = time.monotonic()
    asyncio.run(api.log_ncaaf_startup_readiness())
    elapsed = time.monotonic() - started

    assert len(scheduled) == 1
    assert elapsed < 0.1
    assert len(api._background_tasks) == 1
    api._background_tasks.clear()


def test_background_readiness_timeout_emits_degraded_receipt(monkeypatch, caplog):
    monkeypatch.setattr(api, "_NCAAF_STARTUP_READINESS_TIMEOUT_SECONDS", 0.01)

    def slow_readiness():
        time.sleep(0.05)
        return {"can_execute": False}

    monkeypatch.setattr(api, "ncaaf_readiness", slow_readiness)
    with caplog.at_level(logging.ERROR, logger="wow.ncaaf.readiness"):
        asyncio.run(api._run_ncaaf_startup_readiness_audit())

    messages = [record.getMessage() for record in caplog.records]
    assert any("assessment=DEGRADED" in message for message in messages)
    assert any("code=NCAAF_READINESS_TIMEOUT" in message for message in messages)
    assert any("probability_publishable=false" in message for message in messages)
    assert any("can_execute=false" in message for message in messages)


def test_background_readiness_preserves_responsive_payload_semantics(monkeypatch):
    state = {
        "cfbd_configured": True,
        "historical_source_snapshot_n": 1,
        "training_game_n": 2,
        "training_feature_n": 3,
        "evidence_provider_n": 4,
        "pregame_evidence_n": 5,
        "forward_shadow_n": 6,
        "artifact_status": "ARTIFACT_STATUS",
        "calibrator_status": "CALIBRATOR_STATUS",
        "ncaaf_controlling_model": "MODEL_UNAVAILABLE",
        "ncaaf_trust_state": "NCAAF_TEST_ONLY",
        "blockers": ["BLOCKER"],
        "probability_publishable": False,
        "can_execute": False,
    }
    emitted = []
    monkeypatch.setattr(api, "ncaaf_readiness", lambda: dict(state))
    monkeypatch.setattr(api, "_emit_ncaaf_readiness_state", lambda payload: emitted.append(payload))

    asyncio.run(api._run_ncaaf_startup_readiness_audit())

    assert emitted == [state]
    assert emitted[0]["probability_publishable"] is False
    assert emitted[0]["can_execute"] is False
