from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import v17.interactive_pick_parallel as subject
from pick_request_runtime_core import PickRequestBatch


def _row(key: str) -> dict:
    return {
        "row_key": key,
        "event_id": "evt-1",
        "event_start_time": "2026-09-22T00:00:00+00:00",
        "sport": "NFL",
        "league": "NFL",
        "player": f"Player {key}",
        "stat_type": "RECEIVING_YARDS",
        "line": 50.5,
        "direction": "MORE",
        "source_type": "NORMALIZED",
    }


def test_compact_rows_score_concurrently_preserve_order_and_reapply_portfolio(monkeypatch):
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_SCORE_WORKERS", "2")
    monkeypatch.setattr(subject, "prehydrate_batch", lambda batch, market_api: batch)
    monkeypatch.setattr(subject, "enforce_top10_completion", lambda response, rows: response)
    monkeypatch.setattr(subject.pick_runtime, "_canonical_stat", lambda sport, stat: stat)
    monkeypatch.setattr(
        subject.pick_runtime,
        "_portfolio_leg",
        lambda row_key, row, stat, result: {"row_key": row_key, "event_id": row.event_id},
    )
    portfolio_calls = []

    def portfolio(request_id, scored_legs):
        portfolio_calls.append((request_id, [leg[0]["row_key"] for leg in scored_legs]))
        for _leg, outcome in scored_legs:
            outcome["portfolio_governance"] = {"status": "PASS", "can_execute": False}

    monkeypatch.setattr(subject.pick_runtime, "_apply_portfolio_governance", portfolio)
    monkeypatch.setattr(subject.pick_runtime, "_telemetry", lambda outcomes: {"model_completed": len(outcomes)})
    monkeypatch.setattr(
        subject.pick_runtime,
        "_specialist_utilization_summary",
        lambda outcomes: {"rows_audited": len(outcomes), "can_execute": False},
    )
    monkeypatch.setattr(subject.pick_runtime, "_compact_pick_outcome", lambda outcome: dict(outcome))

    lock = threading.Lock()
    active = 0
    max_active = 0
    calls = []
    app = FastAPI()

    @app.post("/score-pick-request", operation_id="scoreWowPickRequest")
    def canonical(batch: PickRequestBatch):
        nonlocal active, max_active
        row = batch.rows[0]
        with lock:
            active += 1
            max_active = max(max_active, active)
            calls.append(row.row_key)
        time.sleep(0.05)
        with lock:
            active -= 1
        return {
            "rows": [
                {
                    "row_key": row.row_key,
                    "terminal_status": "COMPLETED",
                    "result": {"prediction": {"prediction_id": f"p-{row.row_key}"}},
                    "rank_eligible": True,
                    "probability_publishable": True,
                    "pick_rejected": False,
                    "infrastructure_blocked": False,
                    "can_execute": False,
                }
            ],
            "can_execute": False,
        }

    assert subject.install_interactive_pick_parallel_wrapper(app, market_api=object()) is True
    client = TestClient(app)
    response = client.post(
        "/score-pick-request",
        json={"request_id": "parallel", "response_mode": "COMPACT", "rows": [_row("a"), _row("b"), _row("c")]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["can_execute"] is False
    assert body["reconciliation_pass"] is True
    assert body["rows_completed"] == 3
    assert [row["row_key"] for row in body["rows"]] == ["a", "b", "c"]
    assert max_active >= 2
    assert sorted(calls) == ["a", "b", "c"]
    assert portfolio_calls == [("parallel", ["a", "b", "c"])]
    assert body["interactive_parallelism"]["workers"] == 2


def test_full_mode_keeps_canonical_serial_boundary(monkeypatch):
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_SCORE_WORKERS", "2")
    monkeypatch.setattr(subject, "prehydrate_batch", lambda batch, market_api: (_ for _ in ()).throw(AssertionError("must not prehydrate here")))
    app = FastAPI()
    calls = []

    @app.post("/score-pick-request", operation_id="scoreWowPickRequest")
    def canonical(batch: PickRequestBatch):
        calls.append(len(batch.rows))
        return {"rows": [], "can_execute": False}

    assert subject.install_interactive_pick_parallel_wrapper(app, market_api=object()) is True
    client = TestClient(app)
    response = client.post(
        "/score-pick-request",
        json={"request_id": "full", "response_mode": "FULL", "rows": [_row("a"), _row("b")]},
    )
    assert response.status_code == 200
    assert response.json()["can_execute"] is False
    assert calls == [2]
