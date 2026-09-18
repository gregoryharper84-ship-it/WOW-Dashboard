from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

import team_event_request_runtime as runtime


SPORTS = (
    ("NBA", "NBA"),
    ("WNBA", "WNBA"),
    ("NCAAF", "NCAAF"),
    ("NCAAB", "NCAAB"),
    ("NHL", "NHL"),
    ("SOCCER", "MLS"),
    ("TENNIS", "ATP"),
    ("PGA", "PGA"),
    ("MMA", "UFC"),
    ("BOXING", "BOXING"),
)


class DB:
    def table(self, _name):
        raise AssertionError("non-MLB registered sports must not use MLB hydration")


class EventAPI:
    pass


def _row(sport: str, league: str, **updates):
    payload = {
        "research_run_id": f"separation-{sport.lower()}",
        "objective_lane": "MARKET_EDGE",
        "sport": sport,
        "league": league,
        "event_key": f"{sport}:event-1",
        "event_state": "PREGAME",
        "event_date": "2026-09-20",
        "timezone": "America/Chicago",
        "price_required_for_objective": True,
        "event_start_time_utc": "2026-09-20T20:00:00Z",
        "home_team": "Alpha",
        "away_team": "Beta",
        "source_snapshot_id": f"snapshot-{sport.lower()}",
        "settlement_basis": "FULL_EVENT_OUTRIGHT",
        "sport_specific_evidence": {"test_evidence": "present"},
    }
    payload.update(updates)
    return payload


def _client(monkeypatch, scorer):
    monkeypatch.setenv("WOW_V17_ACTIVE", "1")
    monkeypatch.setattr(runtime, "score_v17_team_event_request", scorer)
    monkeypatch.setattr(runtime, "install_nfl_hydration_startup", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "install_nfl_model_startup", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "install_nfl_team_event_publication", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "install_basketball_model_maintenance_route", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "install_ncaaf_model_maintenance_route", lambda *args, **kwargs: None)
    app = FastAPI()
    runtime.install_team_event_request_routes(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: DB(),
        event_api=EventAPI,
    )
    return TestClient(app)


def _valid_standard_package(req):
    return {
        "candidate_id": req.event_key,
        "selected_participant": req.home_team,
        "calibrated_probability": 0.72,
        "calibrated_lower_bound": 0.66,
        "calibrated_upper_bound": 0.78,
        "immutable_model_timestamp": "2026-09-18T20:00:00Z",
        "model_version": "test-certified-v17",
        "calibration_method": "TEST",
        "calibration_version": "v17-test",
        "source_snapshot_id": req.source_snapshot_id,
        "source_snapshot_timestamp": "2026-09-18T19:59:00Z",
        "outcome_space": "GOVERNED_OUTRIGHT",
        "market_gate": "DATA_UNOBTAINABLE",
        "edge_publication_status": "BLOCKED",
        "terminal_label": "FINAL_APPROVED",
        "probability_publishable": True,
        "rank_eligible": True,
        "blockers": [],
        "can_execute": False,
    }


@pytest.mark.parametrize(("sport", "league"), SPORTS)
def test_completed_sporting_probability_survives_market_failure_for_every_catalog_family(monkeypatch, sport, league):
    calls = []

    def scorer(req, *, event_api, canonical_hydration_required):
        calls.append((req.sport, req.league, canonical_hydration_required))
        return _valid_standard_package(req)

    body = _client(monkeypatch, scorer).post(
        "/score-team-event-request",
        json={"rows": [_row(sport, league)]},
    ).json()

    result = body["rows"][0]
    assert calls == [(sport, league, True)]
    assert result["terminal_status"] == "COMPLETED"
    assert result["code"] == "SPORTING_PROBABILITY_COMPLETED"
    assert result["calibrated_probability"] == 0.72
    assert result["calibrated_lower_bound"] == 0.66
    assert result["sporting_probability_status"] == "COMPLETE"
    assert result["probability_gate"] == "PASS"
    assert result["market_gate"] == "DATA_UNOBTAINABLE"
    assert result["market_edge_status"] == "MARKET_DATA_UNOBTAINABLE"
    assert result["probability_rank_eligible"] is True
    assert result["objective_rank_eligible"] is False
    assert result["blockers"] == ["MARKET_DATA_UNOBTAINABLE"]
    assert result["can_execute"] is False


def test_cross_sport_batch_preserves_typed_model_unavailable_from_shared_scorer(monkeypatch):
    def scorer(req, *, event_api, canonical_hydration_required):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "blockers": ["SOCCER_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE"],
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    body = _client(monkeypatch, scorer).post(
        "/score-team-event-request",
        json={"rows": [_row("SOCCER", "MLS", objective_lane="OUTRIGHT_WIN_PROBABILITY", price_required_for_objective=False)]},
    ).json()
    result = body["rows"][0]
    assert result["code"] == "MODEL_UNAVAILABLE"
    assert result["blockers"] == ["SOCCER_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE"]
    assert result["sporting_probability_status"] == "NOT_COMPLETED"
    assert result["probability_gate"] == "BLOCKED"
    assert result["can_execute"] is False


def test_cross_sport_batch_preserves_scorer_failure_not_model_unavailable(monkeypatch):
    def scorer(req, *, event_api, canonical_hydration_required):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MODEL_SCORER_FAILED",
                "blockers": ["TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE"],
                "model_invoked": True,
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    body = _client(monkeypatch, scorer).post(
        "/score-team-event-request",
        json={"rows": [_row("NBA", "NBA", objective_lane="OUTRIGHT_WIN_PROBABILITY", price_required_for_objective=False)]},
    ).json()
    result = body["rows"][0]
    assert result["code"] == "MODEL_SCORER_FAILED"
    assert result["blockers"] == ["TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE"]
    assert result["code"] != "MODEL_UNAVAILABLE"
    assert result["can_execute"] is False
