import asyncio
from types import SimpleNamespace

import pytest

import kalshi_weather_v2.empirical_runtime as subject


def test_empirical_shadow_loop_defers_first_database_cycle(monkeypatch):
    order = []

    def fake_cycle(*, db_client_fn):
        order.append(("cycle", db_client_fn()))
        return SimpleNamespace(
            status="PASS",
            targets_checked=0,
            contracts_discovered=0,
            samples_captured=0,
            samples_skipped_existing=0,
            supplemental_snapshots_captured=0,
            predictions_settled=0,
            capture_failures=(),
            supplemental_failures=(),
            settlement_failures=(),
        )

    async def fake_sleep(seconds):
        order.append(("sleep", seconds))
        if len([item for item in order if item[0] == "sleep"]) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(subject, "_run_bounded_shadow_cycle", fake_cycle)
    monkeypatch.setattr(subject.asyncio, "sleep", fake_sleep)

    async def exercise():
        with pytest.raises(asyncio.CancelledError):
            await subject._run_bounded_shadow_loop(
                db_client_fn=lambda: "db-client",
                interval_seconds=900,
                initial_delay_seconds=120,
            )

    asyncio.run(exercise())
    assert order[0] == ("sleep", 120)
    assert order[1] == ("cycle", "db-client")
