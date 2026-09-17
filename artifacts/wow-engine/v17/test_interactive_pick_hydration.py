from __future__ import annotations

import threading
import time
from typing import Optional

from fastapi import Depends, FastAPI, Header
from fastapi.testclient import TestClient

import v17.interactive_pick_hydration as subject
from pick_request_runtime_core import PickRequestBatch, RawPropEvidence


class _BaseApi:
    @staticmethod
    def _controlling_specialist_provider(_sport, _stat):
        return {"controlling_specialist": "wow.mlb-prop-expert"}


class _Prod:
    PROP_CAPABILITY_KEY = "PROP"
    base_api = _BaseApi()

    @staticmethod
    def _runtime_capability(_key):
        return {"capability_status": "AVAILABLE"}


class _Market:
    prod = _Prod()

    @staticmethod
    def _prop_route_artifact(_sport, _stat):
        return {"ok": True, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY"}


def _evidence() -> RawPropEvidence:
    return RawPropEvidence(
        captured_at="2026-09-17T12:00:00+00:00",
        game_log=[1.0] * 10,
        box_score_log=[{"game": index} for index in range(10)],
        role_status={"status": "STARTER"},
        role_timestamp="2026-09-17T12:00:00+00:00",
        opportunity_ledger={"status": "PASS"},
        source_timestamps={"official": "2026-09-17T12:00:00+00:00"},
        rate_provenance="OFFICIAL_TEST",
    )


def _row(player: str) -> dict:
    return {
        "event_id": "777",
        "event_start_time": "2026-09-18T00:00:00+00:00",
        "sport": "MLB",
        "player": player,
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 4.5,
        "direction": "MORE",
        "source_type": "NORMALIZED",
    }


def _client(monkeypatch, hydrate_fn):
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", "4")
    monkeypatch.setattr(subject, "_hydrate", hydrate_fn)
    captured = {}
    app = FastAPI()

    @app.post(
        "/score-pick-request",
        dependencies=[Depends(lambda: None)],
        operation_id="scoreWowPickRequest",
    )
    def canonical(
        batch: PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        captured["batch"] = batch
        captured["identity"] = x_wow_model_identity
        return {
            "ok": True,
            "rows": [
                {"player": row.player, "has_evidence": row.evidence is not None}
                for row in batch.rows
            ],
            "can_execute": False,
        }

    assert subject.install_interactive_pick_hydration_wrapper(app, market_api=_Market()) is True
    assert subject.install_interactive_pick_hydration_wrapper(app, market_api=_Market()) is True
    return TestClient(app), captured


def test_wrapper_parallelizes_only_successful_external_hydration_and_preserves_order(monkeypatch):
    lock = threading.Lock()
    state = {"active": 0, "max_active": 0}

    def hydrate(_row):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return _evidence()

    client, captured = _client(monkeypatch, hydrate)
    response = client.post(
        "/score-pick-request",
        headers={"X-WOW-Model-Identity": "WOW_BETTING_ENGINE"},
        json={"request_id": "parallel-test", "rows": [_row("Pitcher A"), _row("Pitcher B"), _row("Pitcher C")]},
    )
    assert response.status_code == 200
    assert response.json()["can_execute"] is False
    assert state["max_active"] >= 2
    assert [row.player for row in captured["batch"].rows] == ["Pitcher A", "Pitcher B", "Pitcher C"]
    assert all(row.evidence is not None for row in captured["batch"].rows)
    assert captured["identity"] == "WOW_BETTING_ENGINE"

    routes = [
        route
        for route in client.app.router.routes
        if getattr(route, "path", None) == "/score-pick-request"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
    assert routes[0].operation_id == "scoreWowPickRequest"


def test_failed_prefetch_delegates_untouched_row_to_canonical_handler(monkeypatch):
    def hydrate(row):
        if row.player == "Pitcher A":
            raise RuntimeError("provider failure belongs to canonical handler")
        return _evidence()

    client, captured = _client(monkeypatch, hydrate)
    response = client.post(
        "/score-pick-request",
        json={"rows": [_row("Pitcher A"), _row("Pitcher B")]},
    )
    assert response.status_code == 200
    rows = captured["batch"].rows
    assert rows[0].player == "Pitcher A" and rows[0].evidence is None
    assert rows[1].player == "Pitcher B" and rows[1].evidence is not None


def test_unproven_route_is_never_prehydrated(monkeypatch):
    class BlockedMarket(_Market):
        @staticmethod
        def _prop_route_artifact(_sport, _stat):
            return {"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"}

    called = {"count": 0}

    def hydrate(_row):
        called["count"] += 1
        return _evidence()

    batch = PickRequestBatch(rows=[_row("Pitcher A"), _row("Pitcher B")])
    monkeypatch.setattr(subject, "_hydrate", hydrate)
    prepared = subject.prehydrate_batch(batch, market_api=BlockedMarket())
    assert called["count"] == 0
    assert prepared is batch
    assert all(row.evidence is None for row in prepared.rows)


def test_single_worker_setting_preserves_canonical_serial_path(monkeypatch):
    monkeypatch.setenv("WOW_INTERACTIVE_PROP_HYDRATION_WORKERS", "1")
    called = {"count": 0}

    def hydrate(_row):
        called["count"] += 1
        return _evidence()

    monkeypatch.setattr(subject, "_hydrate", hydrate)
    batch = PickRequestBatch(rows=[_row("Pitcher A"), _row("Pitcher B")])
    prepared = subject.prehydrate_batch(batch, market_api=_Market())
    assert prepared is batch
    assert called["count"] == 0
