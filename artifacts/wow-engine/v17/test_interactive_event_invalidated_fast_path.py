from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import v17.interactive_pick_hydration as subject
from pick_request_runtime_core import PickRequestBatch


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


def _row(key: str, direction: str) -> dict:
    return {
        "row_key": key,
        "event_id": "777",
        "event_start_time": "2020-09-18T00:00:00+00:00",
        "sport": "MLB",
        "player": "Pitcher A",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 4.5,
        "direction": direction,
        "source_type": "NORMALIZED",
        "platform": "PrizePicks",
    }


def test_all_started_ready_batch_fast_terminalizes_without_canonical_execution(monkeypatch):
    canonical_called = {"value": False}
    hydrate_called = {"value": False}

    def fail_hydrate(_row):
        hydrate_called["value"] = True
        raise AssertionError("hydration must not run for proven event-invalidated batch")

    monkeypatch.setattr(subject, "_hydrate", fail_hydrate)
    app = FastAPI()

    @app.post("/score-pick-request", dependencies=[Depends(lambda: None)], operation_id="scoreWowPickRequest")
    def canonical(_batch: PickRequestBatch):
        canonical_called["value"] = True
        raise AssertionError("captured canonical endpoint must not run after proven event invalidation")

    assert subject.install_interactive_pick_hydration_wrapper(app, market_api=_Market()) is True
    response = TestClient(app).post(
        "/score-pick-request",
        json={
            "request_id": "historical-fast-path",
            "response_mode": "COMPACT",
            "rows": [_row("r-more", "MORE"), _row("r-less", "LESS")],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reconciliation_pass"] is True
    assert body["rows_in"] == 2
    assert body["rows_rejected"] == 2
    assert body["response_mode"] == "COMPACT"
    assert body["can_execute"] is False
    assert canonical_called["value"] is False
    assert hydrate_called["value"] is False
    assert [row["code"] for row in body["rows"]] == ["EVENT_ALREADY_STARTED", "EVENT_ALREADY_STARTED"]
    assert all(row["terminal_label"] == "NO_PLAY" for row in body["rows"])
    assert all(row["probability_publishable"] is False for row in body["rows"])


def test_fast_path_refuses_to_override_route_blocker():
    class _BlockedMarket(_Market):
        @staticmethod
        def _prop_route_artifact(_sport, _stat):
            return {"ok": False, "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"}

    batch = PickRequestBatch(
        request_id="blocked-route",
        response_mode="COMPACT",
        rows=[_row("r-more", "MORE")],
    )
    assert subject._all_rows_started_and_preflight_ready(batch, market_api=_BlockedMarket()) is False
