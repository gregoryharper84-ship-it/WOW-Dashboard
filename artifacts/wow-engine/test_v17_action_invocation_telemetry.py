import base64
import json
import logging
import re
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


# Typed receipt written by install_action_invocation_middleware. duration_ms is
# a timing measurement. It is not a payload field, so a value such as 0.774
# must not be treated as the sensitive probability 0.77.
_REDACTED_RECEIPT_FIELDS = {
    "invocation_id": (str,),
    "route": (str,),
    "action_operation_id": (str,),
    "http_method": (str,),
    "http_status": (int,),
    "validation_code": (str, type(None)),
    "auth_scheme": (str,),
    "caller_class": (str,),
    "caller_user_agent": (str, type(None)),
    "request_id": (str, type(None)),
    "rows_in": (int, type(None)),
    "duration_ms": (int, float),
    "can_execute": (bool,),
}
_TIMING_FIELDS = frozenset({"duration_ms"})
_NUMBER_TOKEN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?!\d)")
_SENSITIVE_STRINGS = ("PRIVATE_PLAYER", "PRIVATE_CREDENTIAL")
_SENSITIVE_NUMBER = 0.77


def _value_matches(value, expected_types):
    if isinstance(value, bool):
        return bool in expected_types
    return isinstance(value, expected_types)


def _string_discloses(text, *, sensitive_strings, sensitive_number):
    disclosed = [token for token in sensitive_strings if token in text]
    for match in _NUMBER_TOKEN.finditer(text):
        try:
            numeric = float(match.group(1))
        except ValueError:
            continue
        if numeric == sensitive_number:
            disclosed.append(format(sensitive_number, "g"))
            break
    return disclosed


def _collect_disclosures(value, path, violations, *, sensitive_strings, sensitive_number):
    if isinstance(value, str):
        for token in _string_discloses(
            value,
            sensitive_strings=sensitive_strings,
            sensitive_number=sensitive_number,
        ):
            violations.append(f"{path} discloses sensitive value {token!r}")
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)) and value == sensitive_number:
        violations.append(
            f"{path} discloses sensitive value {format(sensitive_number, 'g')!r}"
        )
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _collect_disclosures(
                child,
                f"{path}.{key}",
                violations,
                sensitive_strings=sensitive_strings,
                sensitive_number=sensitive_number,
            )
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _collect_disclosures(
                child,
                f"{path}[{index}]",
                violations,
                sensitive_strings=sensitive_strings,
                sensitive_number=sensitive_number,
            )


def redaction_violations(
    receipts,
    *,
    sensitive_strings=_SENSITIVE_STRINGS,
    sensitive_number=_SENSITIVE_NUMBER,
):
    """Return structural disclosures of sensitive 422 payload values.

    String secrets match as substrings. The sensitive number matches an exact
    numeric field or a numeric token, so duration_ms 0.774 does not match 0.77.
    Timing fields are excluded only when they have the typed timing shape.
    """
    violations = []
    for index, receipt in enumerate(receipts):
        path = f"receipt[{index}]"
        if not isinstance(receipt, dict):
            violations.append(f"{path} is not an object")
            _collect_disclosures(
                receipt,
                path,
                violations,
                sensitive_strings=sensitive_strings,
                sensitive_number=sensitive_number,
            )
            continue
        for key in sorted(set(receipt) - set(_REDACTED_RECEIPT_FIELDS)):
            violations.append(f"{path}.{key} is outside the redacted receipt schema")
            _collect_disclosures(
                receipt[key],
                f"{path}.{key}",
                violations,
                sensitive_strings=sensitive_strings,
                sensitive_number=sensitive_number,
            )
        for key, expected_types in _REDACTED_RECEIPT_FIELDS.items():
            if key not in receipt:
                violations.append(f"{path} missing {key}")
                continue
            value = receipt[key]
            field_path = f"{path}.{key}"
            if not _value_matches(value, expected_types):
                violations.append(f"{field_path} has type {type(value).__name__}")
                _collect_disclosures(
                    value,
                    field_path,
                    violations,
                    sensitive_strings=sensitive_strings,
                    sensitive_number=sensitive_number,
                )
                continue
            if key in _TIMING_FIELDS:
                continue
            _collect_disclosures(
                value,
                field_path,
                violations,
                sensitive_strings=sensitive_strings,
                sensitive_number=sensitive_number,
            )
    return violations


def _redacted_receipt(**overrides):
    receipt = {
        "invocation_id": "d66d28e4-3e19-42af-aaf5-57cb9cdf35f1",
        "route": "/score-prop",
        "action_operation_id": "scoreWowProp",
        "http_method": "POST",
        "http_status": 422,
        "validation_code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
        "auth_scheme": "NONE",
        "caller_class": "UNKNOWN",
        "caller_user_agent": None,
        "request_id": "chat-correlation-1",
        "rows_in": None,
        "duration_ms": 0.774,
        "can_execute": False,
    }
    receipt.update(overrides)
    return receipt


def test_validation_code_capture_preserves_422_response_and_redacts_details():
    receipts = []
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))

    @app.post("/score-prop")
    def rejected():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND",
                "player": "PRIVATE_PLAYER",
                "probability": 0.77,
                "can_execute": False,
            },
        )

    with TestClient(app) as client:
        r = client.post(
            "/score-prop",
            headers={"X-WOW-Request-ID": "chat-correlation-1"},
            json={"token": "PRIVATE_CREDENTIAL"},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["player"] == "PRIVATE_PLAYER"
        deadline = time.monotonic() + 2.0
        while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
            time.sleep(0.01)

    assert len(receipts) == 1
    assert receipts[0]["validation_code"] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
    assert receipts[0]["request_id"] == "chat-correlation-1"
    assert receipts[0]["can_execute"] is False
    assert redaction_violations(
        receipts,
        sensitive_strings=_SENSITIVE_STRINGS,
        sensitive_number=_SENSITIVE_NUMBER,
    ) == []


def test_redaction_check_accepts_duration_ms_0_774_and_rejects_sensitive_0_77():
    """Substring search of repr false-positives on the CI timing value.

    Run 39181449192 reported ``tion_ms': 0.774, 'can_execute': False``.
    That timing measurement must pass. An actual 0.77 payload disclosure,
    and the private player/credential strings, must still fail.
    """
    timing = _redacted_receipt(duration_ms=0.774)
    rendered = repr([timing])
    assert "duration_ms': 0.774, 'can_execute': False" in rendered
    assert "0.77" in rendered
    assert redaction_violations([timing]) == []

    exact_timing = _redacted_receipt(duration_ms=0.77)
    assert redaction_violations([exact_timing]) == []

    longer_token = _redacted_receipt(request_id="elapsed-0.774")
    assert redaction_violations([longer_token]) == []

    leaked_probability = _redacted_receipt()
    leaked_probability["probability"] = 0.77
    probability_violations = redaction_violations([leaked_probability])
    assert any(
        "probability" in item and "0.77" in item for item in probability_violations
    )

    leaked_request = _redacted_receipt(request_id="0.77")
    assert any("0.77" in item for item in redaction_violations([leaked_request]))

    leaked_prose = _redacted_receipt(caller_user_agent="probability 0.77")
    assert any("0.77" in item for item in redaction_violations([leaked_prose]))

    leaked_same_number = _redacted_receipt(request_id="0.770")
    assert any("0.77" in item for item in redaction_violations([leaked_same_number]))

    leaked_player = _redacted_receipt(caller_user_agent="PRIVATE_PLAYER")
    assert any("PRIVATE_PLAYER" in item for item in redaction_violations([leaked_player]))

    leaked_credential = _redacted_receipt(request_id="PRIVATE_CREDENTIAL")
    assert any(
        "PRIVATE_CREDENTIAL" in item
        for item in redaction_violations([leaked_credential])
    )

    nested = _redacted_receipt()
    nested["detail"] = {"player": "PRIVATE_PLAYER", "probability": 0.77}
    nested_violations = redaction_violations([nested])
    assert any("PRIVATE_PLAYER" in item for item in nested_violations)
    assert any("0.77" in item for item in nested_violations)

    string_timing = _redacted_receipt(duration_ms="0.77")
    assert any(
        "duration_ms" in item and "0.77" in item
        for item in redaction_violations([string_timing])
    )


def test_validation_code_capture_does_not_record_unsafe_or_unstructured_details():
    from v17.action_invocation_telemetry import _safe_validation_code
    assert _safe_validation_code(b'{"detail":{"code":"VALID_CODE"}}') == "VALID_CODE"
    assert _safe_validation_code(b'{"detail":{"code":"a secret"}}') is None
    assert _safe_validation_code(b'{"detail":[{"loc":["body"],"msg":"secret"}]}') is None
    assert _safe_validation_code(b'{"detail":{"code":"TOKEN_123\\nSECRET"}}') is None
    assert _safe_validation_code(b'not json') is None
    # Pydantic/FastAPI 422 includes field locations and possibly rejected input:
    # no such values may be copied to the invocation ledger.
    schema_failure = json.dumps({
        "detail": [{
            "type": "missing",
            "loc": ["body", "PRIVATE_PLAYER"],
            "msg": "PRIVATE_REASON",
            "input": "PRIVATE_CREDENTIAL",
        }]
    }).encode()
    assert _safe_validation_code(schema_failure) == "REQUEST_SCHEMA_VALIDATION_FAILED"
    assert _safe_validation_code(b'{"detail":[{"msg":"no type or loc"}]}') is None
    assert _safe_validation_code(b'{"detail":[]}') is None

