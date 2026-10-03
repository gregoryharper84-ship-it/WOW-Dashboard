from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
V17 = HERE / "v17"


def _text(name: str) -> str:
    return (V17 / name).read_text(encoding="utf-8")


def test_idle_async_workers_use_low_pressure_polling_and_immediate_wake_contract():
    daily = _text("daily_async_runtime.py")
    pickem = _text("nfl_pickem_async_runtime.py")

    assert 'WOW_V17_DAILY_ASYNC_POLL_SECONDS", 30, minimum=5, maximum=60' in daily
    assert 'WOW_V17_DAILY_ASYNC_DB_FAILURE_BACKOFF_SECONDS", 30, minimum=5, maximum=120' in daily
    assert 'WOW_V17_NFL_PICKEM_ASYNC_POLL_SECONDS", 30, minimum=5, maximum=60' in pickem
    assert 'WOW_V17_NFL_PICKEM_ASYNC_DB_FAILURE_BACKOFF_SECONDS", 30, minimum=5, maximum=120' in pickem

    for text in (daily, pickem):
        assert "claim_failed = False" in text
        assert "claim_failed = True" in text
        assert "timeout = db_failure_backoff_seconds if claim_failed else poll_seconds" in text
        assert "wake.wait()" in text
        assert '"can_execute": False' in text or "CAN_EXECUTE = False" in text


def test_durable_pick_worker_reduces_idle_and_failure_poll_pressure():
    queue = _text("pick_request_durable_job_queue.py")

    assert "POLL_SECONDS = 10.0" in queue
    assert "DB_FAILURE_BACKOFF_SECONDS = 30.0" in queue
    assert "timeout=POLL_SECONDS" in queue
    assert "timeout=DB_FAILURE_BACKOFF_SECONDS" in queue
    assert "CAN_EXECUTE = False" in queue
