from __future__ import annotations

from fastapi import Depends, FastAPI

import v17.spread_certification_replay_route as route
from v17.spread_margin_challenger import SpreadChallengerUnavailable


def test_execute_nfl_certification_replay_preserves_nonpublication(monkeypatch):
    monkeypatch.setattr(
        route,
        "run_nflverse_close_proxy_replay",
        lambda **_kwargs: {
            "status": "EXPERIMENT_CREATED",
            "code": "NFL_SPREAD_HISTORICAL_CLOSE_PROXY_REPLAY_COMPLETE",
            "sport": "NFL",
            "exact_line_metrics": {"evidence_row_n": 200},
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        },
    )
    result = route.execute_spread_certification_replay(
        object(),
        route.SpreadCertificationReplayRequest(sport="NFL"),
    )
    assert result["status"] == "EXPERIMENT_CREATED"
    assert result["evidence_scope"] == "HISTORICAL_CLOSE_PROXY_CERTIFICATION_REPLAY_ONLY"
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["production_registry_mutated"] is False
    assert result["can_execute"] is False


def test_execute_wnba_typed_failure_remains_blocked(monkeypatch):
    def broken(**_kwargs):
        raise SpreadChallengerUnavailable("SPREAD_ESPN_SUMMARY_LINE_UNAVAILABLE", "no usable close proxy")

    monkeypatch.setattr(route, "run_wnba_espn_close_proxy_replay", broken)
    result = route.execute_spread_certification_replay(
        object(),
        route.SpreadCertificationReplayRequest(sport="WNBA"),
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "SPREAD_ESPN_SUMMARY_LINE_UNAVAILABLE"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_runtime_exception_is_not_rewritten_to_model_unavailable(monkeypatch):
    def broken(**_kwargs):
        raise RuntimeError("fixture")

    monkeypatch.setattr(route, "run_nflverse_close_proxy_replay", broken)
    result = route.execute_spread_certification_replay(
        object(),
        route.SpreadCertificationReplayRequest(sport="NFL"),
    )
    assert result["status"] == "BLOCKED"
    assert result["code"] == "SPREAD_CERTIFICATION_REPLAY_RUNTIME_FAILED"
    assert result["error_type"] == "RuntimeError"
    assert result["code"] != "MODEL_UNAVAILABLE"


def test_route_installation_is_idempotent():
    app = FastAPI()
    kwargs = {
        "auth_dependency": Depends(lambda: None),
        "db_client_fn": lambda: object(),
    }
    route.install_spread_certification_replay_route(app, **kwargs)
    route.install_spread_certification_replay_route(app, **kwargs)
    matches = [
        item for item in app.router.routes
        if getattr(item, "path", None) == "/internal/v17/spread-certification-replay"
    ]
    assert len(matches) == 1
    assert matches[0].operation_id == "runWowV17SpreadCertificationReplay"
