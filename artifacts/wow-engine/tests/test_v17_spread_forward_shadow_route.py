from fastapi import Depends, FastAPI

from v17 import spread_margin_replay_route as route
from v17.mlb_run_line_shadow import MLBRunLineShadowUnavailable
from v17.spread_margin_challenger import SpreadChallengerUnavailable


def _request():
    return route.SpreadForwardShadowRequest(
        sport="NCAAF",
        event_id="401000001",
        event_start_time="2026-09-26T19:00:00+00:00",
        home_team="Home",
        away_team="Away",
        home_spread=-3.5,
        season=2026,
    )


def test_forward_shadow_route_success_is_non_publishable(monkeypatch):
    monkeypatch.setattr(
        route,
        "run_ncaaf_forward_shadow",
        lambda *_args, **_kwargs: {
            "status": "EXPERIMENT_CREATED",
            "code": "SPREAD_FORWARD_SHADOW_COMPLETE",
            "sport": "NCAAF",
            "event_id": "401000001",
            "p_cover": 0.62,
            "p_push": 0.0,
            "p_not_cover": 0.38,
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    payload = route.execute_spread_forward_shadow(object(), _request())
    assert payload["status"] == "EXPERIMENT_CREATED"
    assert payload["p_cover"] == 0.62
    assert payload["probability_publishable"] is False
    assert payload["automatic_certification"] is False
    assert payload["automatic_promotion"] is False
    assert payload["database_mutated"] is False
    assert payload["production_registry_mutated"] is False
    assert payload["can_execute"] is False


def test_forward_shadow_route_preserves_typed_failure(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise SpreadChallengerUnavailable("SPREAD_FORWARD_HISTORY_INSUFFICIENT", "need more prior games")

    monkeypatch.setattr(route, "run_ncaaf_forward_shadow", blocked)
    payload = route.execute_spread_forward_shadow(object(), _request())
    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"
    assert payload["code"] != "MODEL_UNAVAILABLE"
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False


def test_mlb_run_line_route_success_is_research_only(monkeypatch):
    monkeypatch.setattr(
        route,
        "run_mlb_run_line_forward_shadow",
        lambda *_args, **_kwargs: {
            "status": "EXPERIMENT_CREATED",
            "code": "MLB_RUN_LINE_FORWARD_SHADOW_COMPLETE",
            "sport": "MLB",
            "score_snapshot_id": "s1",
            "p_cover": .54,
            "p_push": 0.0,
            "p_not_cover": .46,
            "moneyline_probability_used": False,
            "run_line_used_as_feature": False,
            "probability_publishable": False,
            "can_execute": False,
        },
    )
    request = route.MLBRunLineForwardShadowRequest(
        sport="MLB", score_snapshot_id="s1", home_run_line=-1.5
    )
    payload = route.execute_mlb_run_line_forward_shadow(object(), request)
    assert payload["status"] == "EXPERIMENT_CREATED"
    assert payload["code"] == "MLB_RUN_LINE_FORWARD_SHADOW_COMPLETE"
    assert payload["moneyline_probability_used"] is False
    assert payload["automatic_certification"] is False
    assert payload["automatic_promotion"] is False
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False


def test_mlb_run_line_route_preserves_typed_failure(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise MLBRunLineShadowUnavailable(
            "MLB_RUN_LINE_GOVERNED_EVIDENCE_UNAVAILABLE", "missing pregame evidence"
        )

    monkeypatch.setattr(route, "run_mlb_run_line_forward_shadow", blocked)
    request = route.MLBRunLineForwardShadowRequest(
        sport="MLB", score_snapshot_id="s1", home_run_line=-1.5
    )
    payload = route.execute_mlb_run_line_forward_shadow(object(), request)
    assert payload["status"] == "BLOCKED"
    assert payload["code"] == "MLB_RUN_LINE_GOVERNED_EVIDENCE_UNAVAILABLE"
    assert payload["code"] != "MODEL_UNAVAILABLE"
    assert payload["can_execute"] is False


def test_route_is_mounted_once_and_auth_protected():
    app = FastAPI()
    route.install_spread_margin_replay_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: object(),
    )
    route.install_spread_margin_replay_route(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: object(),
    )
    expected = {
        "/internal/v17/spread-forward-shadow": "scoreWowV17SpreadForwardShadow",
        "/internal/v17/nfl-spread-forward-shadow": "scoreWowV17NFLSpreadForwardShadow",
        "/internal/v17/wnba-spread-forward-shadow": "scoreWowV17WNBASpreadForwardShadow",
        "/internal/v17/mlb-run-line-forward-shadow": "scoreWowV17MLBRunLineForwardShadow",
    }
    for path, operation_id in expected.items():
        matches = [r for r in app.router.routes if getattr(r, "path", None) == path]
        assert len(matches) == 1
        assert matches[0].operation_id == operation_id
        assert matches[0].dependant.dependencies
