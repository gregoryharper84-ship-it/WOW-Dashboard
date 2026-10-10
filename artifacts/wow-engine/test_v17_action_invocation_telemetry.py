import base64
import json
import logging
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from v17.action_invocation_telemetry import (
    RECORD_RECEIPT_RPC,
    RECORD_RECOVERY_RPC,
    RECOVERY_TABLE,
    RECONCILE_RECOVERY_RPC,
    TABLE,
    _configure_transport_timeouts,
    install_action_invocation_middleware,
)


class _DB:
    def __init__(self, sink):
        self.sink = sink
        self.recovery = {}

    def rpc(self, name, params):
        return _RPC(self, name, params)


class _RPC:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        if self.name == RECONCILE_RECOVERY_RPC:
            stale = [key for key in self.db.recovery if any(row["invocation_id"] == key for row in self.db.sink)]
            for key in stale[: self.params["p_limit"]]:
                self.db.recovery.pop(key, None)
            return type("Result", (), {"data": {"rows_deleted": len(stale)}})()
        receipt = dict(self.params["p_receipt"])
        invocation_id = receipt["invocation_id"]
        if self.name == RECORD_RECEIPT_RPC:
            if not any(row["invocation_id"] == invocation_id for row in self.db.sink):
                self.db.sink.append(receipt)
            self.db.recovery.pop(invocation_id, None)
            return type("Result", (), {"data": {"status": "INSERTED"}})()
        if self.name == RECORD_RECOVERY_RPC:
            if any(row["invocation_id"] == invocation_id for row in self.db.sink):
                self.db.recovery.pop(invocation_id, None)
                status = "RECONCILED"
            else:
                self.db.recovery[invocation_id] = dict(self.params)
                status = self.params["p_state"]
            return type("Result", (), {"data": {"status": status}})()
        raise AssertionError(self.name)

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
        def rpc(self, _name, _params): raise TimeoutError("db unavailable")
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
            assert any("typed_failure=PERSISTENCE_FAILURE" in r.getMessage() for r in caplog.records)


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
    release = threading.Event()
    receipts = []
    class _SlowRPC(_RPC):
        def execute(self):
            if self.name == RECORD_RECEIPT_RPC:
                release.wait(0.5)
            return super().execute()
    class _SlowDB(_DB):
        def rpc(self, name, params):
            return _SlowRPC(self, name, params)
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
        self.calls = []

    def rpc(self, name, params):
        self.calls.append(name)
        return _FlakyRPC(self, name, params)


class _FlakyRPC:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        if self.name == RECONCILE_RECOVERY_RPC:
            stale = [key for key in self.db.recovery if any(row["invocation_id"] == key for row in self.db.receipts)]
            for key in stale[: self.params["p_limit"]]:
                self.db.recovery.pop(key, None)
            return type("Result", (), {"data": {"rows_deleted": len(stale)}})()

        receipt = dict(self.params["p_receipt"])
        invocation_id = receipt["invocation_id"]
        if self.name == RECORD_RECEIPT_RPC:
            self.db.primary_attempts += 1
            if self.db.primary_attempts <= self.db.fail_primary_attempts:
                raise TimeoutError("simulated transport receipt timeout")
            if not any(row["invocation_id"] == invocation_id for row in self.db.receipts):
                self.db.receipts.append(receipt)
            self.db.recovery.pop(invocation_id, None)
            return type("Result", (), {"data": {"status": "INSERTED"}})()

        if self.name == RECORD_RECOVERY_RPC:
            if any(row["invocation_id"] == invocation_id for row in self.db.receipts):
                self.db.recovery.pop(invocation_id, None)
                status = "RECONCILED"
            else:
                previous = self.db.recovery.get(invocation_id)
                state = self.params["p_state"]
                if previous and previous["state"] == "DEAD_LETTER":
                    state = "DEAD_LETTER"
                payload = {
                    "invocation_id": invocation_id,
                    "attempt_count": max(
                        int((previous or {}).get("attempt_count", 0)),
                        self.params["p_attempt_count"],
                    ),
                    "state": state,
                    "last_error_type": self.params["p_error_type"],
                    "receipt": receipt,
                    "can_execute": False,
                }
                self.db.recovery[invocation_id] = payload
                self.db.recovery_history.append(dict(payload))
                status = state
            return type("Result", (), {"data": {"status": status}})()
        raise AssertionError(self.name)


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


def test_transport_timeout_is_owned_by_postgrest_http_client():
    class _Session:
        timeout = None
    class _Postgrest:
        session = _Session()
    class _TransportDB:
        postgrest = _Postgrest()

    db = _configure_transport_timeouts(_TransportDB())
    timeout = db.postgrest.session.timeout
    assert timeout.connect == 2.0
    assert timeout.read == 5.0
    assert timeout.write == 5.0
    assert timeout.pool == 2.0


def test_ambiguous_late_primary_completion_never_overlaps_retry(monkeypatch):
    """A late commit may report timeout; recovery must observe primary and stop."""
    monkeypatch.setattr(
        "v17.action_invocation_telemetry._RETRY_DELAYS_SECONDS", (0.0, 0.0)
    )

    class _AmbiguousDB(_FlakyReceiptDB):
        def __init__(self):
            super().__init__(fail_primary_attempts=0)
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def rpc(self, name, params):
            if name == RECORD_RECEIPT_RPC:
                return _AmbiguousRPC(self, name, params)
            return super().rpc(name, params)

    class _AmbiguousRPC(_FlakyRPC):
        def execute(self):
            with self.db.lock:
                self.db.active += 1
                self.db.max_active = max(self.db.max_active, self.db.active)
            try:
                self.db.primary_attempts += 1
                receipt = dict(self.params["p_receipt"])
                if self.db.primary_attempts == 1:
                    # Commit succeeds, but the response is lost. Recovery then
                    # observes the canonical row and returns RECONCILED.
                    time.sleep(0.05)
                    self.db.receipts.append(receipt)
                    raise TimeoutError("response lost after database commit")
                raise AssertionError("primary retry must not occur after RECONCILED")
            finally:
                with self.db.lock:
                    self.db.active -= 1

    db = _AmbiguousDB()
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: db)
    @app.post("/score-prop")
    def score_prop(): return {"ok": True, "can_execute": False}

    with TestClient(app) as client:
        assert client.post("/score-prop", headers={"X-WOW-Request-ID": "late-commit"}).status_code == 200
        deadline = time.monotonic() + 2.0
        while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    assert db.primary_attempts == 1
    assert db.max_active == 1
    assert len(db.receipts) == 1
    assert db.receipts[0]["request_id"] == "late-commit"
    assert db.recovery == {}


def test_recovery_state_is_monotonic_and_primary_receipt_wins():
    db = _FlakyReceiptDB(fail_primary_attempts=99)
    receipt = {
        "invocation_id": "d66d28e4-3e19-42af-aaf5-57cb9cdf35f1",
        "route": "/score-prop",
        "http_status": 503,
        "can_execute": False,
    }
    db.rpc(RECORD_RECOVERY_RPC, {
        "p_receipt": receipt, "p_attempt_count": 3,
        "p_error_type": "ReadTimeout", "p_state": "DEAD_LETTER",
    }).execute()
    db.rpc(RECORD_RECOVERY_RPC, {
        "p_receipt": receipt, "p_attempt_count": 1,
        "p_error_type": "LatePending", "p_state": "PENDING",
    }).execute()
    assert db.recovery[receipt["invocation_id"]]["state"] == "DEAD_LETTER"
    assert db.recovery[receipt["invocation_id"]]["attempt_count"] == 3

    db.fail_primary_attempts = 0
    db.rpc(RECORD_RECEIPT_RPC, {"p_receipt": receipt}).execute()
    assert len(db.receipts) == 1
    assert receipt["invocation_id"] not in db.recovery

def test_stale_recovery_reconciliation_is_repeat_idempotent():
    db = _DB([])
    invocation_id = "76364c64-c2ef-4c8a-876c-a3323990eac1"
    receipt = {"invocation_id": invocation_id, "can_execute": False}
    db.sink.append(dict(receipt))
    db.recovery[invocation_id] = {"state": "PENDING"}
    first = db.rpc(RECONCILE_RECOVERY_RPC, {"p_limit": 100}).execute().data
    second = db.rpc(RECONCILE_RECOVERY_RPC, {"p_limit": 100}).execute().data
    assert first["rows_deleted"] == 1
    assert second["rows_deleted"] == 0
    assert db.sink == [receipt]


def test_atomic_receipt_migration_preserves_security_and_immutability():
    from pathlib import Path

    sql = Path("migrations/20260927_action_invocation_receipt_atomic_recovery.sql").read_text()
    assert sql.count("security definer") == 3
    assert sql.count("set search_path = pg_catalog, public") == 3
    assert sql.count("pg_advisory_xact_lock") == 3
    assert "on conflict (invocation_id) do nothing" in sql
    assert "state = case" in sql and "'DEAD_LETTER'" in sql
    assert "to service_role" in sql
    assert "from public, anon, authenticated" in sql
    assert "can_execute', false" in sql


def test_action_invocation_uses_actual_handler_row_count_in_both_middleware_orders():
    """Actual validated scoring metadata wins over absent/spoofed Action headers."""
    from v17.interactive_latency_telemetry import (
        annotate_request, install_interactive_latency_middleware,
    )

    for invocation_first in (False, True):
        receipts = []
        app = FastAPI()
        if invocation_first:
            install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))
            install_interactive_latency_middleware(app)
        else:
            install_interactive_latency_middleware(app)
            install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))

        @app.post("/score-pick-request")
        def score_pick_request():
            annotate_request(sport="NFL", row_count=3, batch_size=3)
            return {"rows": [], "can_execute": False}

        with TestClient(app) as client:
            assert client.post("/score-pick-request", json={"ignored": True}).status_code == 200
            assert client.post(
                "/score-pick-request",
                headers={"X-WOW-Rows-In": "999"},
                json={"ignored": True},
            ).status_code == 200
            deadline = time.monotonic() + 3
            while len(receipts) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
        assert len(receipts) == 2
        assert [row["rows_in"] for row in receipts] == [3, 3]
        assert all(row["can_execute"] is False for row in receipts)
        assert "ignored" not in repr(receipts)


def test_invalid_handler_row_count_cannot_be_overridden_by_caller_header():
    from v17.interactive_latency_telemetry import (
        annotate_request, install_interactive_latency_middleware,
    )
    receipts = []
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))
    install_interactive_latency_middleware(app)

    @app.post("/score-pick-request")
    def score_pick_request():
        annotate_request(sport="NFL", row_count=-5, batch_size=1)
        return {"can_execute": False}

    with TestClient(app) as client:
        response = client.post("/score-pick-request", headers={"X-WOW-Rows-In": "900"})
        assert response.status_code == 200
        deadline = time.monotonic() + 3
        while not receipts and time.monotonic() < deadline:
            time.sleep(0.01)
    assert len(receipts) == 1
    assert receipts[0]["rows_in"] is None
