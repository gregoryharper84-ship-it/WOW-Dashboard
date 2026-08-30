from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from wnba_history_runtime import install_wnba_history_routes


def _official_row():
    return {
        "PLAYER_ID": 123,
        "PLAYER_NAME": "Player A",
        "TEAM_ABBREVIATION": "DAL",
        "GAME_ID": "2026050101",
        "GAME_DATE": "MAY 01, 2026",
        "MATCHUP": "DAL vs. NYL",
        "MIN": 31,
        "PTS": 18,
        "REB": 7,
        "AST": 5,
        "FG3M": 2,
    }


class _StatsClient:
    def __init__(self, rows):
        self.rows = rows

    def player_game_logs(self, *, season, season_type):
        return SimpleNamespace(
            season=season,
            season_type=season_type,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            rows=self.rows,
        )


class _Mutation:
    def __init__(self, sink, payload):
        self.sink = sink
        self.payload = payload

    def execute(self):
        self.sink.append(self.payload)
        return SimpleNamespace(data=[self.payload])


class _Table:
    def __init__(self, sink):
        self.sink = sink

    def upsert(self, payload, on_conflict=None):
        return _Mutation(self.sink, payload)


class _DB:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "wow_wnba_player_game_logs"
        return _Table(self.rows)


def _app(rows):
    app = FastAPI()
    db = _DB()
    install_wnba_history_routes(
        app,
        auth_dependency=Depends(lambda: None),
        db_client_fn=lambda: db,
        stats_client_factory=lambda: _StatsClient(rows),
    )
    return TestClient(app), db


def test_hydration_persists_raw_history_but_cannot_promote_model():
    client, db = _app([_official_row()])
    response = client.post("/internal/wnba/hydrate-history?season=2026&season_type=Regular%20Season")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "WNBA_RAW_HISTORY_PERSISTED"
    assert body["persisted_n"] == 1
    assert body["role_evidence_status"] == "UNRESOLVED"
    assert body["training_materialization_status"] == "BLOCKED_ROLE_EVIDENCE"
    assert body["model_training_status"] == "NOT_ATTEMPTED"
    assert body["artifact_registration_status"] == "NOT_ATTEMPTED"
    assert body["artifact_certification_status"] == "NOT_ATTEMPTED"
    assert body["runtime_model_status"] == "MODEL_UNAVAILABLE"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert "starter" not in db.rows[0]


def test_bad_source_row_is_422_and_writes_nothing():
    bad = _official_row()
    bad["PTS"] = -1
    client, db = _app([bad])
    response = client.post("/internal/wnba/hydrate-history?season=2026")
    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["code"] == "WNBA_HISTORY_SOURCE_ROWS_REJECTED"
    assert body["persisted_n"] == 0
    assert body["runtime_model_status"] == "MODEL_UNAVAILABLE"
    assert body["can_execute"] is False
    assert db.rows == []
