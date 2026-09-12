"""Regressions for the scheduled prop evidence acquisition pass.

The forward cohort consumes ``wow_prop_evidence_snapshots`` every 15 minutes but
acquisition only ran inside the authenticated daily route, so a live future
cohort could never build on its own. These tests pin the schedule contract: off
unless enabled, bounded, covering a forward slate, and never aborting the whole
pass because one date failed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI

from v17.daily_snapshot_runtime import _install_prop_evidence_acquisition_scheduler
from v17.prop_evidence_acquisition_scheduler import (
    CAN_EXECUTE,
    run_prop_evidence_acquisition_loop,
    slate_dates,
)

NOW = datetime(2026, 9, 12, 4, 30, tzinfo=timezone.utc)


def test_acquisition_never_claims_execution_capability():
    assert CAN_EXECUTE is False


def test_forward_slate_covers_the_next_local_date():
    # 04:30Z is still 2026-09-11 in America/Chicago; the cohort needs the
    # following date too or the future slate empties after the last first pitch.
    assert slate_dates(NOW, "America/Chicago", 2) == ["2026-09-11", "2026-09-12"]


def test_forward_slate_is_bounded_and_never_empty():
    assert slate_dates(NOW, "America/Chicago", 0) == ["2026-09-11"]
    assert len(slate_dates(NOW, "America/Chicago", 3)) == 3


def test_unknown_timezone_falls_back_to_utc_instead_of_raising():
    assert slate_dates(NOW, "Not/AZone", 1) == ["2026-09-12"]


def _run_one_pass(monkeypatch, acquire: Any) -> list[str]:
    calls: list[str] = []

    def _acquire(*, db, requested_date, requested_timezone, max_candidates):
        calls.append(requested_date)
        return acquire(requested_date)

    monkeypatch.setattr(
        "v17.prop_evidence_acquisition_scheduler.acquire_daily_prop_snapshots", _acquire
    )

    async def _drive():
        task = asyncio.create_task(
            run_prop_evidence_acquisition_loop(
                db_client_fn=lambda: object(),
                logger=logging.getLogger("test.acquisition"),
                interval_seconds=86400,
                forward_days=2,
                initial_delay_seconds=0,
                now_fn=lambda: NOW,
            )
        )
        # Each pass hands the synchronous acquisition to a worker thread, so the
        # loop has to actually yield rather than just spin the event loop.
        for _ in range(200):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_drive())
    return calls


def test_every_forward_date_is_acquired_in_one_pass(monkeypatch):
    calls = _run_one_pass(monkeypatch, lambda _date: {"status": "COMPLETED", "persisted": 3})

    assert calls == ["2026-09-11", "2026-09-12"]


def test_one_failing_date_does_not_abort_the_remaining_slate(monkeypatch):
    def _acquire(requested_date: str):
        if requested_date == "2026-09-11":
            raise RuntimeError("boom")
        return {"status": "COMPLETED", "persisted": 1}

    calls = _run_one_pass(monkeypatch, _acquire)

    assert calls == ["2026-09-11", "2026-09-12"]


def test_scheduler_defaults_off(monkeypatch):
    monkeypatch.delenv("WOW_PROP_EVIDENCE_ACQUISITION_ENABLED", raising=False)
    app = FastAPI()

    _install_prop_evidence_acquisition_scheduler(app, db_client_fn=lambda: object())

    assert getattr(app.state, "wow_prop_evidence_acquisition_scheduler_installed", False) is False


def test_scheduler_registers_once_when_enabled(monkeypatch):
    monkeypatch.setenv("WOW_PROP_EVIDENCE_ACQUISITION_ENABLED", "1")
    app = FastAPI()

    _install_prop_evidence_acquisition_scheduler(app, db_client_fn=lambda: object())
    _install_prop_evidence_acquisition_scheduler(app, db_client_fn=lambda: object())

    assert app.state.wow_prop_evidence_acquisition_scheduler_installed is True
