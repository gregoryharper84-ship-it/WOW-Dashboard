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
            await scheduler.run_refresh_loop(db_client_fn=lambda: "db-client", logger=logger, interval_seconds=300, initial_delay_seconds=0)

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
            await scheduler.run_refresh_loop(db_client_fn=lambda: "db-client", logger=logger, initial_delay_seconds=0)

    with caplog.at_level(logging.ERROR):
        asyncio.run(exercise())

    assert calls["n"] == 1
    assert "status=FAILED" in caplog.text
    assert "can_execute=false" in caplog.text


def test_refresh_loop_staggers_first_database_access(monkeypatch):
    calls = []
    sleeps = []

    def fake_run_once(*, client):
        calls.append(client)
        return {}

    async def stop_on_initial_delay(seconds):
        sleeps.append(seconds)
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_on_initial_delay)
    logger = logging.getLogger("test.mlb.1ip.startup-delay")

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run_refresh_loop(
                db_client_fn=lambda: "db-client",
                logger=logger,
                interval_seconds=300,
                initial_delay_seconds=30,
            )

    asyncio.run(exercise())
    assert sleeps == [30.0]
    assert calls == []


def test_refresh_loop_emits_same_process_memory_delta(monkeypatch, caplog):
    snapshots = iter([
        {
            "cgroup_current_bytes": 275_000_000,
            "cgroup_limit_bytes": 536_870_900,
            "cgroup_ratio": 275_000_000 / 536_870_900,
            "process_rss_bytes": 240_000_000,
        },
        {
            "cgroup_current_bytes": 305_000_000,
            "cgroup_limit_bytes": 536_870_900,
            "cgroup_ratio": 305_000_000 / 536_870_900,
            "process_rss_bytes": 267_000_000,
        },
    ])

    monkeypatch.setattr(scheduler, "_runtime_memory_snapshot", lambda: next(snapshots))
    monkeypatch.setattr(
        scheduler,
        "run_once",
        lambda *, client: {
            "seen": 0,
            "waiting": 0,
            "rerun_completed": 0,
            "purged": 0,
            "expired": 0,
            "failed": 0,
        },
    )

    async def stop_after_first_interval(_seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(scheduler.asyncio, "sleep", stop_after_first_interval)
    logger = logging.getLogger("test.mlb.1ip.memory-delta")

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await scheduler.run_refresh_loop(
                db_client_fn=lambda: "db-client",
                logger=logger,
                interval_seconds=300,
                initial_delay_seconds=0,
            )

    with caplog.at_level(logging.WARNING):
        asyncio.run(exercise())

    assert "WOW_V17_BACKGROUND_MEMORY_DELTA operation=MLB_1IP_FINAL_REFRESH status=PASS" in caplog.text
    assert "cgroup_before_bytes=275000000" in caplog.text
    assert "cgroup_after_bytes=305000000" in caplog.text
    assert "cgroup_delta_bytes=30000000" in caplog.text
    assert "process_rss_before_bytes=240000000" in caplog.text
    assert "process_rss_after_bytes=267000000" in caplog.text
    assert "process_rss_delta_bytes=27000000" in caplog.text
    assert "can_execute=false" in caplog.text


def test_process_rss_reader_fails_open(monkeypatch):
    class BrokenPath:
        def __init__(self, *_args, **_kwargs):
            pass

        def read_text(self, **_kwargs):
            raise OSError("unavailable")

    monkeypatch.setattr(scheduler, "Path", BrokenPath)
    assert scheduler._process_rss_bytes() is None
