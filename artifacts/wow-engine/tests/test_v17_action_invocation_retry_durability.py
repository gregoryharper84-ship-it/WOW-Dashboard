import logging
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.action_invocation_telemetry import install_action_invocation_middleware


def test_action_invocation_retry_reuses_one_stable_invocation_id(caplog):
    persisted = []
    attempts = []

    class RetryTable:
        def __init__(self):
            self.payload = None

        def upsert(self, payload, on_conflict=None):
            assert on_conflict == "invocation_id"
            self.payload = dict(payload)
            attempts.append(self.payload["invocation_id"])
            return self

        def execute(self):
            if len(attempts) == 1:
                raise TimeoutError("first acknowledgement unavailable")
            persisted.append(dict(self.payload))
            return type("Result", (), {"data": [self.payload]})()

    class RetryDb:
        def table(self, name):
            assert name == "wow_action_invocation_receipts"
            return RetryTable()

    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: RetryDb())

    @app.post("/score-team-event")
    def score_team_event():
        return {"ok": True, "can_execute": False}

    with caplog.at_level(logging.WARNING, logger="wow.v17.action_invocation"):
        with TestClient(app) as client:
            response = client.post("/score-team-event", headers={"X-WOW-Request-ID": "retry-self-test"})
            assert response.status_code == 200
            assert response.json()["can_execute"] is False
            deadline = time.monotonic() + 1.5
            while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
                time.sleep(0.01)

    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert len(persisted) == 1
    assert persisted[0]["invocation_id"] == attempts[0]
    assert persisted[0]["request_id"] == "retry-self-test"
    assert any(
        "WOW_V17_ACTION_INVOCATION_PERSISTENCE_RECOVERED" in record.getMessage()
        for record in caplog.records
    )


def test_receipt_payload_has_stable_id_and_no_execution_authority():
    receipts = []

    class Table:
        def __init__(self):
            self.payload = None

        def upsert(self, payload, on_conflict=None):
            assert on_conflict == "invocation_id"
            self.payload = dict(payload)
            return self

        def execute(self):
            receipts.append(dict(self.payload))
            return type("Result", (), {"data": [self.payload]})()

    class Db:
        def table(self, name):
            assert name == "wow_action_invocation_receipts"
            return Table()

    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: Db())

    @app.post("/score-team-event")
    def score_team_event():
        return {"ok": True, "can_execute": False}

    with TestClient(app) as client:
        response = client.post("/score-team-event")
        assert response.status_code == 200
        deadline = time.monotonic() + 1.0
        while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    assert len(receipts) == 1
    assert receipts[0]["invocation_id"]
    assert receipts[0]["occurred_at"]
    assert receipts[0]["can_execute"] is False
