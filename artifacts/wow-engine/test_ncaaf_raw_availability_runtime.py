from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ncaaf_raw_availability_runtime import install_raw_availability_routes


class Result:
    data = [{"ok": True}]


class Table:
    def __init__(self):
        self.rows = []
    def upsert(self, rows, on_conflict=None):
        self.rows = list(rows)
        return self
    def execute(self):
        result = Result()
        result.data = [{"ok": True} for _ in self.rows]
        return result


class DB:
    def __init__(self):
        self.t = Table()
    def table(self, name):
        assert name == "wow_ncaaf_pregame_evidence"
        return self.t


def client_and_db():
    app = FastAPI()
    db = DB()
    install_raw_availability_routes(app, auth_dependency=Depends(lambda: True), db_client_fn=lambda: db)
    return TestClient(app), db


def valid_payload():
    return {
        "conference": "BIG12",
        "official_event_id": "evt-1",
        "event_start_time": "2026-09-05T23:00:00+00:00",
        "report_timestamp": "2026-09-05T20:00:00+00:00",
        "report_phase": "PRE_GAME",
        "team": "Texas",
        "players": [{"player": "QB One", "position": "QB", "status": "PROBABLE"}],
    }


def test_runtime_persists_only_raw_availability_and_never_scores():
    client, db = client_and_db()
    response = client.post("/internal/ncaaf/ingest-availability-report", json=valid_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["evidence_kind"] == "PLAYER_AVAILABILITY_REPORT"
    assert body["derived_role_evidence_status"] == "NOT_PRODUCED"
    assert body["model_scoring_status"] == "NOT_ATTEMPTED"
    assert body["probability_publishable"] is False
    assert body["can_execute"] is False
    assert db.t.rows[0]["evidence_kind"] == "PLAYER_AVAILABILITY_REPORT"


def test_runtime_rejects_unverified_conference_policy():
    client, _ = client_and_db()
    payload = valid_payload()
    payload["conference"] = "SEC"
    response = client.post("/internal/ncaaf/ingest-availability-report", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NCAAF_AVAILABILITY_POLICY_UNVERIFIED"


def test_runtime_rejects_post_kickoff_report():
    client, _ = client_and_db()
    payload = valid_payload()
    payload["report_timestamp"] = payload["event_start_time"]
    response = client.post("/internal/ncaaf/ingest-availability-report", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "NCAAF_AVAILABILITY_NOT_PREGAME"
