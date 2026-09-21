import base64
import json
import logging
import time

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from v17.action_invocation_telemetry import (
    RECOVERY_TABLE,
    TABLE,
    install_action_invocation_middleware,
)


class _Table:
    def __init__(self, sink): self.sink, self.payload = sink, None
    def insert(self, payload): self.payload = dict(payload); return self
    def execute(self): self.sink.append(self.payload); return type("Result", (), {"data": [self.payload]})()

class _DB:
    def __init__(self, sink): self.sink = sink
    def table(self, name): assert name == TABLE; return _Table(self.sink)

def _unsigned_jwt(payload):
    header = base64.urlsafe_b64encode(json.dumps({"alg":"none"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{header}.{body}.signature"

def test_action_invocation_telemetry_records_success_failure_and_caller_class_without_body():
    receipts=[]; app=FastAPI(); install_action_invocation_middleware(app,db_client_fn=lambda:_DB(receipts)); install_action_invocation_middleware(app,db_client_fn=lambda:_DB(receipts))
    @app.post("/score-prop")
    def score_prop(): return {"prediction":{"player":"must-not-be-persisted"},"can_execute":False}
    @app.post("/score-pick-request")
    def score_pick_request(): return {"rows":[{"player":"must-not-be-persisted"}],"can_execute":False}
    @app.post("/score-team-event")
    def score_team_event(): raise HTTPException(status_code=409,detail={"code":"MODEL_UNAVAILABLE"})
    @app.post("/score-team-event-request")
    def score_team_event_request(): raise HTTPException(status_code=401,detail={"code":"AUTH_FAILED"})
    @app.get("/health")
    def health(): return {"ok":True}
    github_oidc=_unsigned_jwt({"iss":"https://token.actions.githubusercontent.com"}); client=TestClient(app)
    assert client.post("/score-prop",headers={"Authorization":"Bearer test-key-placeholder","User-Agent":"Python-urllib/3.11","X-WOW-Request-ID":"req-api-key-1"},json={"player":"sensitive-player"}).status_code==200
    assert client.post("/score-pick-request",headers={"Authorization":"Bearer test-token-placeholder","User-Agent":"OpenAI-ChatGPT-Action/1.0","X-WOW-Request-ID":"req-chat-1","X-WOW-Rows-In":"2"},json={"rows":[{"player":"sensitive-player"}]}).status_code==200
    assert client.post("/score-team-event",headers={"Authorization":f"Bearer {github_oidc}","User-Agent":"Python-urllib/3.11","X-WOW-Request-ID":"req-ci-1"},json={"home_team":"A","away_team":"B"}).status_code==409
    assert client.post("/score-team-event-request",headers={"Authorization":f"Bearer {github_oidc}","User-Agent":"GitHub-Actions-Test"},json={"home_team":"A","away_team":"B"}).status_code==401
    assert client.get("/health").status_code==200
    deadline=time.monotonic()+1.0
    while len(receipts)<4 and time.monotonic()<deadline: time.sleep(0.01)
    assert len(receipts)==4
    prop,pick,team,unauthorized=receipts
    assert prop["route"]=="/score-prop" and prop["action_operation_id"]=="scoreWowProp" and prop["http_status"]==200 and prop["auth_scheme"]=="BEARER" and prop["caller_class"]=="ACTION_API_KEY" and prop["request_id"]=="req-api-key-1" and prop["can_execute"] is False
    assert pick["route"]=="/score-pick-request" and pick["action_operation_id"]=="scoreWowPickRequest" and pick["http_status"]==200 and pick["auth_scheme"]=="BEARER" and pick["caller_class"]=="CHATGPT_ACTION" and pick["request_id"]=="req-chat-1" and pick["rows_in"]==2 and pick["can_execute"] is False
    assert team["route"]=="/score-team-event" and team["action_operation_id"]=="scoreWowV17TeamEventFromWowHost" and team["http_status"]==409 and team["caller_class"]=="GITHUB_ACTIONS" and team["request_id"]=="req-ci-1" and team["can_execute"] is False
    assert unauthorized["route"]=="/score-team-event-request" and unauthorized["http_status"]==401 and unauthorized["caller_class"]=="UNKNOWN"
    assert all(receipt.get("invocation_id") for receipt in receipts)
    assert len({receipt["invocation_id"] for receipt in receipts}) == 4
    serialized=repr(receipts)
    for sensitive_value in ("test-token-placeholder","test-key-placeholder",github_oidc,"sensitive-player","must-not-be-persisted"): assert sensitive_value not in serialized

def test_action_invocation_telemetry_persistence_failure_never_changes_action_response(caplog):
    class _BrokenDB:
        def table(self,_name): raise RuntimeError("db unavailable")
    app=FastAPI(); install_action_invocation_middleware(app,db_client_fn=lambda:_BrokenDB())
    @app.post("/score-pick-request")
    def score_pick_request(): return {"ok":True,"can_execute":False}
    with caplog.at_level(logging.WARNING,logger="wow.v17.action_invocation"):
        with TestClient(app) as client:
            response=client.post("/score-pick-request")
            assert response.status_code==200 and response.json()["can_execute"] is False
            deadline=time.monotonic()+3.5
            while not any("WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED" in r.getMessage() for r in caplog.records) and time.monotonic()<deadline: time.sleep(0.01)
            assert any("WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED" in r.getMessage() for r in caplog.records)


def test_action_invocation_telemetry_owns_tasks_until_completion():
    receipts = []
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))
    @app.post("/score-pick-request")
    def score_pick_request(): return {"ok": True, "can_execute": False}
    with TestClient(app) as client:
        response = client.post("/score-pick-request")
        assert response.status_code == 200
        deadline = time.monotonic() + 1.0
        while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not app.state.wow_action_invocation_tasks
    assert len(receipts) == 1
    assert receipts[0]["can_execute"] is False


def test_action_invocation_telemetry_shutdown_drains_pending_receipt():
    import threading
    release = threading.Event()
    receipts = []
    class _SlowTable(_Table):
        def execute(self):
            release.wait(0.5)
            return super().execute()
    class _SlowDB(_DB):
        def table(self, name):
            assert name == TABLE
            return _SlowTable(self.sink)
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: _SlowDB(receipts))
    @app.post("/score-pick-request")
    def score_pick_request(): return {"ok": True, "can_execute": False}
    with TestClient(app) as client:
        response = client.post("/score-pick-request")
        assert response.status_code == 200
        assert response.json()["can_execute"] is False
        release.set()
    assert len(receipts) == 1
    assert not app.state.wow_action_invocation_tasks


class _FlakyReceiptDB:
    def __init__(self, *, fail_primary_attempts):
        self.fail_primary_attempts = fail_primary_attempts
        self.primary_attempts = 0
        self.receipts = []
        self.recovery = {}
        self.recovery_history = []

    def table(self, name):
        if name == TABLE:
            return _FlakyPrimaryTable(self)
        if name == RECOVERY_TABLE:
            return _RecoveryTable(self)
        raise AssertionError(name)


class _FlakyPrimaryTable:
    def __init__(self, db):
        self.db = db
        self.payload = None

    def upsert(self, payload, on_conflict=None):
        assert on_conflict == "invocation_id"
        self.payload = dict(payload)
        return self

    def execute(self):
        self.db.primary_attempts += 1
        if self.db.primary_attempts <= self.db.fail_primary_attempts:
            raise TimeoutError("simulated receipt timeout")
        self.db.receipts[:] = [
            row for row in self.db.receipts
            if row["invocation_id"] != self.payload["invocation_id"]
        ]
        self.db.receipts.append(self.payload)
        return type("Result", (), {"data": [self.payload]})()


class _RecoveryTable:
    def __init__(self, db):
        self.db = db
        self.mode = None
        self.payload = None
        self.key = None

    def upsert(self, payload, on_conflict=None):
        assert on_conflict == "invocation_id"
        self.mode = "upsert"
        self.payload = dict(payload)
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def eq(self, key, value):
        assert key == "invocation_id"
        self.key = value
        return self

    def execute(self):
        if self.mode == "upsert":
            self.db.recovery[self.payload["invocation_id"]] = dict(self.payload)
            self.db.recovery_history.append(dict(self.payload))
            return type("Result", (), {"data": [self.payload]})()
        if self.mode == "delete":
            self.db.recovery.pop(self.key, None)
            return type("Result", (), {"data": []})()
        raise AssertionError(self.mode)


def test_receipt_timeout_queues_then_recovers_idempotently():
    db = _FlakyReceiptDB(fail_primary_attempts=1)
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: db)
    @app.post("/score-team-event")
    def score_team_event(): return {"ok": True, "can_execute": False}

    with TestClient(app) as client:
        response = client.post("/score-team-event", headers={"X-WOW-Request-ID": "recover-me"})
        assert response.status_code == 200
        deadline = time.monotonic() + 2.5
        while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    assert db.primary_attempts == 2
    assert len(db.receipts) == 1
    assert db.receipts[0]["request_id"] == "recover-me"
    assert db.recovery == {}
    assert db.recovery_history[0]["state"] == "PENDING"
    assert db.recovery_history[0]["receipt"]["invocation_id"] == db.receipts[0]["invocation_id"]
    assert db.receipts[0]["can_execute"] is False


def test_receipt_retry_exhaustion_is_durable_dead_letter():
    db = _FlakyReceiptDB(fail_primary_attempts=99)
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: db)
    @app.post("/score-team-event")
    def score_team_event(): return {"ok": True, "can_execute": False}

    with TestClient(app) as client:
        response = client.post("/score-team-event", headers={"X-WOW-Request-ID": "dead-letter-me"})
        assert response.status_code == 200
        deadline = time.monotonic() + 3.0
        while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    assert db.primary_attempts == 3
    assert not db.receipts
    assert len(db.recovery) == 1
    recovery = next(iter(db.recovery.values()))
    assert recovery["state"] == "DEAD_LETTER"
    assert recovery["attempt_count"] == 3
    assert recovery["receipt"]["request_id"] == "dead-letter-me"
    assert recovery["can_execute"] is False
