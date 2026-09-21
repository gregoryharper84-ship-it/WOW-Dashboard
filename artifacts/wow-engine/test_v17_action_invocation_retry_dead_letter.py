import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.action_invocation_telemetry import (
    DEAD_LETTER_TABLE,
    TABLE,
    install_action_invocation_middleware,
)


class _Query:
    def __init__(self, db, table_name):
        self.db = db
        self.table_name = table_name
        self.payload = None

    def upsert(self, payload, on_conflict=None):
        self.payload = dict(payload)
        self.on_conflict = on_conflict
        return self

    def insert(self, payload):
        self.payload = dict(payload)
        return self

    def execute(self):
        if self.table_name == TABLE:
            self.db.primary_attempts += 1
            if self.db.primary_attempts <= self.db.fail_primary_attempts:
                raise TimeoutError("transient receipt timeout")
            self.db.receipts.append(dict(self.payload))
            return type("Result", (), {"data": [self.payload]})()
        if self.table_name == DEAD_LETTER_TABLE:
            self.db.dead_letters.append(dict(self.payload))
            return type("Result", (), {"data": [self.payload]})()
        raise AssertionError(self.table_name)


class _DB:
    def __init__(self, fail_primary_attempts):
        self.fail_primary_attempts = fail_primary_attempts
        self.primary_attempts = 0
        self.receipts = []
        self.dead_letters = []

    def table(self, name):
        return _Query(self, name)


def _wait_until(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _app(db):
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: db)

    @app.post("/score-team-event")
    def score_team_event():
        return {"ok": True, "can_execute": False}

    return app


def test_action_receipt_transient_timeout_retries_idempotently():
    db = _DB(fail_primary_attempts=2)
    with TestClient(_app(db)) as client:
        response = client.post("/score-team-event")
        assert response.status_code == 200
        assert _wait_until(lambda: len(db.receipts) == 1)

    assert db.primary_attempts == 3
    assert db.dead_letters == []
    assert db.receipts[0]["invocation_id"]
    assert db.receipts[0]["can_execute"] is False


def test_action_receipt_exhausted_retries_is_dead_lettered():
    db = _DB(fail_primary_attempts=99)
    with TestClient(_app(db)) as client:
        response = client.post("/score-team-event")
        assert response.status_code == 200
        assert _wait_until(lambda: len(db.dead_letters) == 1)

    assert db.primary_attempts == 3
    dead = db.dead_letters[0]
    assert dead["invocation_id"] == dead["receipt"]["invocation_id"]
    assert dead["attempt_count"] == 3
    assert dead["last_error"] == "TimeoutError"
    assert dead["can_execute"] is False
