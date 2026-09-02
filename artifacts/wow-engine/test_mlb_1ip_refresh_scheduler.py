import asyncio
import logging

import pytest

import mlb_1ip_refresh_scheduler as scheduler


def test_refresh_loop_runs_governed_pass_and_preserves_nonexecution(monkeypatch, caplog):
    calls = []

    def fake_run_once(*, client):
        calls.append(client)
        return {"seen": 1, "waiting": 1, "rerun_completed": 0, "purged": 0, "expired": 0, "failed": 0}

    async def stop_after_first_sleep(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_after_first_sleep)
    logger = logging.getLogger("test.mlb.1ip.refresh")

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run_refresh_loop(db_client_fn=lambda: "db-client", logger=logger, interval_seconds=300)

    with caplog.at_level(logging.INFO):
        asyncio.run(exercise())

    assert calls == ["db-client"]
    assert "probability_publishable=false can_execute=false" in caplog.text
    assert scheduler.CAN_EXECUTE is False


def test_refresh_loop_failure_is_nonfatal_until_cancel(monkeypatch, caplog):
    calls = {"n": 0}

    def fake_run_once(*, client):
        calls["n"] += 1
        raise RuntimeError("transient")

    async def stop_after_failure(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_after_failure)
    logger = logging.getLogger("test.mlb.1ip.refresh.failure")

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run_refresh_loop(db_client_fn=lambda: "db-client", logger=logger)

    with caplog.at_level(logging.ERROR):
        asyncio.run(exercise())

    assert calls["n"] == 1
    assert "status=FAILED" in caplog.text
    assert "can_execute=false" in caplog.text
