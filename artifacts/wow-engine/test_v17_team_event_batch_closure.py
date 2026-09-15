from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import team_event_request_runtime as runtime


CANONICAL = {
    "official_event_id": "822925",
    "official_date": "2026-09-15",
    "event_start_time": "2026-09-15T22:40:00+00:00",
    "event_status": "Scheduled",
    "home_team": "Tampa Bay Rays",
    "away_team": "Athletics",
    "venue_name": "Tropicana Field",
    "home_probable_pitcher": "Griffin Jax",
    "away_probable_pitcher": "Jack Perkins",
    "snapshot_id": "11111111-1111-4111-8111-111111111111",
    "snapshot_timestamp": "2026-09-15T16:47:00+00:00",
    "feature_hydration_status": "PASS",
}


class Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.limit_n = None
        self.order_key = None
        self.order_desc = False

    def select(self, *args):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def order(self, key, desc=False):
        self.order_key = key
        self.order_desc = desc
        return self

    def limit(self, value):
        self.limit_n = value
        return self

    def execute(self):
        rows = [dict(row) for row in self.rows]
        for key, value in self.filters:
            rows = [row for row in rows if row.get(key) == value]
        if self.order_key:
            rows.sort(key=lambda row: str(row.get(self.order_key) or ""), reverse=self.order_desc)
        if self.limit_n is not None:
            rows = rows[: self.limit_n]
        return type("Result", (), {"data": rows})()


class DB:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return Query(self.rows)


class EventAPI:
    class ScoreEventRequest:
        def __init__(self, **kwargs):
            self.payload = kwargs

    @staticmethod
    def score_event(_req):
        raise AssertionError("V17 production path must not call legacy score_event directly")


def row(**updates):
    payload = {
        "research_run_id": "run-closure",
        "objective_lane": "OUTRIGHT_WIN_PROBABILITY",
        "sport": "MLB",
        "league": "MLB",
        "event_key": "MLB:espn-401816943",
        "event_state": "PREGAME",
        "event_date": "2026-09-15",
        "timezone": "America/Chicago",
        "price_required_for_objective": False,
        "event_start_time_utc": "2026-09-15T22:40:00Z",
        "home_team": "Tampa Bay Rays",
        "away_team": "Athletics",
    }
    payload.update(updates)
    return payload


def client(monkeypatch, rows):
    monkeypatch.setenv("WOW_V17_ACTIVE", "1")
    app = FastAPI()
    runtime.install_team_event_request_routes(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: DB(rows),
        event_api=EventAPI,
    )
    return TestClient(app)


def _scored():
    return {
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "blockers": ["PROSPECTIVE_CERTIFICATION_CEILING"],
        "calibrated_home_probability": 0.62,
        "calibrated_away_probability": 0.38,
        "calibrated_home_lower_bound": 0.57,
        "calibrated_away_lower_bound": 0.33,
        "calibrated_home_upper_bound": 0.67,
        "calibrated_away_upper_bound": 0.43,
        "base_score_snapshot_id": "score-1",
        "probability_publishable": False,
        "can_execute": False,
    }


def test_v17_batch_routes_through_active_scorer_and_reuses_cross_provider_duplicate(monkeypatch):
    calls = []

    def fake_v17(req, *, event_api, canonical_hydration_required):
        calls.append((req.official_event_id, req.home_team, canonical_hydration_required))
        return _scored()

    monkeypatch.setattr(runtime, "score_v17_team_event_request", fake_v17)
    batch = {
        "rows": [
            row(),
            row(
                event_key="MLB:rundown-b990cab78bfee393e6502eee75f90aa7",
                home_team="Tampa Bay",
            ),
        ]
    }
    body = client(monkeypatch, [CANONICAL]).post("/score-team-event-request", json=batch).json()

    assert body["run_status"] == "COMPLETE"
    assert body["rows_completed"] == 2
    assert len(calls) == 1
    assert calls[0] == ("822925", "Tampa Bay Rays", False)
    first, duplicate = body["rows"]
    assert first["code"] == "SPORTING_PROBABILITY_COMPLETED"
    assert first["calibrated_probability"] == 0.62
    assert first["probability_publishable"] is False
    assert duplicate["code"] == "SPORTING_PROBABILITY_REUSED_CANONICAL_EVENT"
    assert duplicate["cross_provider_dedupe"] is True
    assert duplicate["canonical_official_event_id"] == "822925"
    assert duplicate["calibrated_probability"] == first["calibrated_probability"]
    assert duplicate["can_execute"] is False


def test_exact_team_identity_accepts_bounded_official_schedule_time_update():
    shifted = dict(CANONICAL, official_date="2026-09-16", event_start_time="2026-09-16T17:10:00+00:00")
    request = runtime.TeamEventRequestRow(**row(
        event_key="MLB:espn-401816960",
        event_date="2026-09-16",
        event_start_time_utc="2026-09-16T17:00:00Z",
        home_team="Tampa Bay Rays",
        away_team="Athletics",
    ))
    hydrated = runtime._hydrate(DB([shifted]), request)
    assert hydrated is not None
    assert hydrated["official_event_id"] == "822925"


def test_provider_alias_matching_fails_closed_when_canonical_identity_is_ambiguous():
    first = dict(CANONICAL, official_event_id="1", home_team="Los Angeles Angels", away_team="Seattle Mariners")
    second = dict(CANONICAL, official_event_id="2", home_team="Los Angeles Dodgers", away_team="Seattle Mariners")
    request = runtime.TeamEventRequestRow(**row(
        event_key="MLB:rundown-ambiguous",
        home_team="Los Angeles",
        away_team="Seattle",
    ))
    assert runtime._hydrate(DB([first, second]), request) is None


def test_typed_model_input_failure_is_not_reclassified_as_model_unavailable():
    assert runtime._typed_mlb_failure({"code": "MODEL_INPUTS_INSUFFICIENT"}) == (
        "MODEL_INPUTS_INSUFFICIENT",
        "MODEL_INPUTS_INSUFFICIENT",
    )


def test_unhydrated_canonical_game_remains_genuine_evidence_hold(monkeypatch):
    delayed = dict(CANONICAL, feature_hydration_status="DELAYED_STARTER_UNRESOLVED", away_probable_pitcher=None)
    monkeypatch.setattr(runtime, "score_v17_team_event_request", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not score")))
    body = client(monkeypatch, [delayed]).post("/score-team-event-request", json={"rows": [row()]}).json()
    assert body["run_status"] == "BLOCKED"
    assert body["rows"][0]["code"] == "INPUT_INCOMPLETE"
    assert body["rows"][0]["blockers"] == ["EVENT_EVIDENCE_INCOMPLETE"]
    assert body["rows"][0]["can_execute"] is False
