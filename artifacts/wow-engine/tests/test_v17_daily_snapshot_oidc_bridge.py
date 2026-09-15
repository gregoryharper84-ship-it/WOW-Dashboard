from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

import v17.daily_snapshot_oidc_bridge as bridge
from v17 import daily_snapshot_runtime as daily_runtime


class _Prod:
    event_api = object()

    def __init__(self):
        self.db = object()

    def get_client(self):
        return self.db


class _MarketApi:
    def __init__(self):
        self.prod = _Prod()


def _route(app: FastAPI):
    return next(
        route
        for route in app.router.routes
        if getattr(route, "path", None) == bridge.INTERNAL_DAILY_SNAPSHOT_ROUTE
    )


def test_internal_contract_covers_full_mlb_slate_without_widening_public_contract():
    internal = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
        lanes=["MONEYLINE"],
        max_team_events=24,
    )
    assert internal.max_team_events == 24
    assert bridge.INTERNAL_MAX_TEAM_EVENTS == 32

    with pytest.raises(ValidationError):
        bridge.InternalDailySnapshotRequest(
            requested_slate_date="2026-09-15",
            requested_timezone="America/Chicago",
            max_team_events=33,
        )

    with pytest.raises(ValidationError):
        daily_runtime.DailySnapshotRequest(
            requested_slate_date="2026-09-15",
            requested_timezone="America/Chicago",
            max_team_events=13,
        )


def test_bridge_installs_once_and_uses_server_owned_runtime(monkeypatch):
    app = FastAPI()
    market_api = _MarketApi()
    captured = {}

    monkeypatch.setattr(
        bridge,
        "authorize_action_key_or_multiscout_oidc",
        lambda authorization: "GITHUB_ACTIONS_OIDC",
    )

    def fake_run(req, *, db, market_api: object, event_api):
        captured.update(req=req, db=db, market_api=market_api, event_api=event_api)
        return {
            "run_id": "v17-daily-test",
            "run_status": "COMPLETED",
            "rows": [],
            "reconciliation": {"balanced": True},
            "probability_publishable": False,
            "can_execute": False,
        }

    monkeypatch.setattr(daily_runtime, "run_daily_snapshot", fake_run)

    assert bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api) is True
    assert bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api) is True
    assert sum(
        getattr(route, "path", None) == bridge.INTERNAL_DAILY_SNAPSHOT_ROUTE
        for route in app.router.routes
    ) == 1

    req = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
        lanes=["MONEYLINE"],
        max_team_events=24,
    )
    result = _route(app).endpoint(req, authorization="Bearer oidc-token")

    assert captured["req"] is req
    assert captured["db"] is market_api.prod.db
    assert captured["market_api"] is market_api
    assert captured["event_api"] is market_api.prod.event_api
    assert result["automation_auth"] == "GITHUB_ACTIONS_OIDC"
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_bridge_fails_closed_on_invalid_automation_auth(monkeypatch):
    app = FastAPI()
    market_api = _MarketApi()
    bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api)

    def fail(_authorization):
        raise bridge.GitHubOIDCValidationError("bad-token")

    monkeypatch.setattr(bridge, "authorize_action_key_or_multiscout_oidc", fail)
    req = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
    )

    with pytest.raises(HTTPException) as caught:
        _route(app).endpoint(req, authorization="Bearer invalid")

    assert caught.value.status_code == 401
    assert caught.value.detail == {
        "code": "DAILY_SNAPSHOT_AUTOMATION_AUTH_INVALID",
        "can_execute": False,
    }


def test_bridge_cannot_upgrade_execution_or_probability_claim(monkeypatch):
    app = FastAPI()
    market_api = _MarketApi()
    monkeypatch.setattr(
        bridge,
        "authorize_action_key_or_multiscout_oidc",
        lambda authorization: "GITHUB_ACTIONS_OIDC",
    )
    monkeypatch.setattr(
        daily_runtime,
        "run_daily_snapshot",
        lambda *args, **kwargs: {
            "run_id": "v17-daily-held",
            "run_status": "COMPLETED_WITH_ACQUISITION_BLOCKERS",
            "rows": [],
            "probability_publishable": False,
            "can_execute": True,
        },
    )
    bridge.install_daily_snapshot_oidc_bridge(app=app, market_api=market_api)

    req = bridge.InternalDailySnapshotRequest(
        requested_slate_date="2026-09-15",
        requested_timezone="America/Chicago",
    )
    result = _route(app).endpoint(req, authorization="Bearer oidc-token")

    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert result["global_terminal_authority"] == "V17_TERMINAL_REDUCER"
