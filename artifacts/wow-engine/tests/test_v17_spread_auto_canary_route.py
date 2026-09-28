from __future__ import annotations

from fastapi import Depends, FastAPI

import v17.spread_margin_replay_route as route
from v17.spread_margin_challenger import SpreadChallengerUnavailable


def test_auto_canary_routes_mount_once_with_auth_dependencies():
    app = FastAPI()
    kwargs = {"auth_dependency": Depends(lambda: None), "db_client_fn": lambda: object()}
    route.install_spread_margin_replay_route(app, **kwargs)
    route.install_spread_margin_replay_route(app, **kwargs)

    expected = {
        "/internal/v17/nfl-spread-forward-auto-canary": "runWowV17NFLSpreadForwardAutoCanary",
        "/internal/v17/wnba-spread-forward-auto-canary": "runWowV17WNBASpreadForwardAutoCanary",
    }
    for path, operation_id in expected.items():
        mounted = [r for r in app.router.routes if getattr(r, "path", None) == path]
        assert len(mounted) == 1
        assert mounted[0].operation_id == operation_id
        assert mounted[0].dependant.dependencies


def test_nfl_auto_canary_success_cannot_become_publishable(monkeypatch):
    monkeypatch.setattr(
        route,
        "run_nfl_spread_auto_canary",
        lambda _db: {
            "status": "EXPERIMENT_CREATED",
            "code": "NFL_SPREAD_FORWARD_SHADOW_COMPLETE",
            "sport": "NFL",
            "p_cover": 0.53,
            "p_push": 0.0,
            "p_not_cover": 0.47,
            "probability_publishable": True,
            "automatic_certification": True,
            "automatic_promotion": True,
            "can_execute": True,
            "identity_acquisition_location": "BACKEND_RUNTIME",
        },
    )
    payload = route.execute_nfl_spread_auto_canary(object())
    assert payload["status"] == "EXPERIMENT_CREATED"
    assert payload["code"] == "NFL_SPREAD_FORWARD_SHADOW_COMPLETE"
    assert payload["probability_publishable"] is False
    assert payload["automatic_certification"] is False
    assert payload["automatic_promotion"] is False
    assert payload["can_execute"] is False
    assert payload["global_terminal_reducer"] == "V17_TERMINAL_REDUCER"


def test_wnba_auto_canary_typed_source_failure_is_preserved(monkeypatch):
    def blocked(_db):
        raise SpreadChallengerUnavailable(
            "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE",
            "backend ESPN schedule acquisition failed",
        )

    monkeypatch.setattr(route, "run_wnba_spread_auto_canary", blocked)
    payload = route.execute_wnba_spread_auto_canary(object())
    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "WNBA_SPREAD_CANARY_IDENTITY_SOURCE_UNAVAILABLE"
    assert payload["code"] != "MODEL_UNAVAILABLE"
    assert payload["identity_acquisition_location"] == "BACKEND_RUNTIME"
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False
