import base64
import json
import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from v17.action_invocation_telemetry import install_action_invocation_middleware


class _Table:
    def __init__(self, sink):
        self.sink = sink
        self.payload = None

    def insert(self, payload):
        self.payload = dict(payload)
        return self

    def execute(self):
        self.sink.append(self.payload)
        return type("Result", (), {"data": [self.payload]})()


class _DB:
    def __init__(self, sink):
        self.sink = sink

    def table(self, name):
        assert name == "wow_action_invocation_receipts"
        return _Table(self.sink)


def _unsigned_jwt(payload):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.signature"


def test_action_invocation_telemetry_records_success_failure_and_caller_class_without_body():
    receipts = []
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))
    install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))

    @app.post("/score-prop")
    def score_prop():
        return {"prediction": {"player": "must-not-be-persisted"}, "can_execute": False}

    @app.post("/score-pick-request")
    def score_pick_request():
        return {"rows": [{"player": "must-not-be-persisted"}], "can_execute": False}

    @app.post("/score-team-event")
    def score_team_event():
        raise HTTPException(status_code=409, detail={"code": "MODEL_UNAVAILABLE"})

    @app.post("/score-team-event-request")
    def score_team_event_request():
        raise HTTPException(status_code=401, detail={"code": "AUTH_FAILED"})

    @app.get("/health")
    def health():
        return {"ok": True}

    github_oidc = _unsigned_jwt({"iss": "https://token.actions.githubusercontent.com"})
    client = TestClient(app)

    assert client.post(
        "/score-prop",
        headers={
            "Authorization": "Bearer generic-valid-action-key",
            "User-Agent": "Python-urllib/3.11",
            "X-WOW-Request-ID": "req-api-key-1",
        },
        json={"player": "sensitive-player"},
    ).status_code == 200

    assert client.post(
        "/score-pick-request",
        headers={
            "Authorization": "Bearer super-secret-token",
            "User-Agent": "OpenAI-ChatGPT-Action/1.0",
            "X-WOW-Request-ID": "req-chat-1",
            "X-WOW-Rows-In": "2",
        },
        json={"rows": [{"player": "sensitive-player"}]},
    ).status_code == 200

    assert client.post(
        "/score-team-event",
        headers={
            "Authorization": f"Bearer {github_oidc}",
            "User-Agent": "Python-urllib/3.11",
            "X-WOW-Request-ID": "req-ci-1",
        },
        json={"home_team": "A", "away_team": "B"},
    ).status_code == 409

    assert client.post(
        "/score-team-event-request",
        headers={
            "Authorization": f"Bearer {github_oidc}",
            "User-Agent": "GitHub-Actions-Test",
        },
        json={"home_team": "A", "away_team": "B"},
    ).status_code == 401

    assert client.get("/health").status_code == 200

    assert len(receipts) == 4
    prop, pick, team, unauthorized = receipts

    assert prop["route"] == "/score-prop"
    assert prop["action_operation_id"] == "scoreWowProp"
    assert prop["http_status"] == 200
    assert prop["auth_scheme"] == "BEARER"
    assert prop["caller_class"] == "ACTION_API_KEY"
    assert prop["request_id"] == "req-api-key-1"
    assert prop["can_execute"] is False

    assert pick["route"] == "/score-pick-request"
    assert pick["action_operation_id"] == "scoreWowPickRequest"
    assert pick["http_status"] == 200
    assert pick["auth_scheme"] == "BEARER"
    assert pick["caller_class"] == "CHATGPT_ACTION"
    assert pick["request_id"] == "req-chat-1"
    assert pick["rows_in"] == 2
    assert pick["can_execute"] is False

    assert team["route"] == "/score-team-event"
    assert team["action_operation_id"] == "scoreWowV17TeamEventFromWowHost"
    assert team["http_status"] == 409
    assert team["caller_class"] == "GITHUB_ACTIONS"
    assert team["request_id"] == "req-ci-1"
    assert team["can_execute"] is False

    assert unauthorized["route"] == "/score-team-event-request"
    assert unauthorized["http_status"] == 401
    assert unauthorized["caller_class"] == "UNKNOWN"

    serialized = repr(receipts)
    assert "super-secret-token" not in serialized
    assert "generic-valid-action-key" not in serialized
    assert github_oidc not in serialized
    assert "sensitive-player" not in serialized
    assert "must-not-be-persisted" not in serialized


def test_action_invocation_telemetry_persistence_failure_never_changes_action_response(caplog):
    class _BrokenDB:
        def table(self, _name):
            raise RuntimeError("db unavailable")

    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: _BrokenDB())

    @app.post("/score-pick-request")
    def score_pick_request():
        return {"ok": True, "can_execute": False}

    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="wow.v17.action_invocation"):
        response = client.post("/score-pick-request")

    assert response.status_code == 200
    assert response.json()["can_execute"] is False
    assert any("WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED" in record.getMessage() for record in caplog.records)
