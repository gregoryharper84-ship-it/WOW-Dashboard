"""Regressions for certification-independent Action invocation telemetry.

WOW-RUNTIME-ACTION-CANARY-NEVER-VERIFIED-011, repairs P0-A and P0-C.

The defect these lock down is interpretive, not arithmetic: an empty
certification-gated canary table was read as proof that the canonical Action had
never been invoked. These tests assert the new ledger is independent of
certification, records failures, identifies the caller class, and never leaks a
credential or blocks a request.
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import v17.action_invocation_telemetry as subject
from v17.interactive_latency_telemetry import INTERACTIVE_PATHS


class _Table:
    def __init__(self, sink, *, fail=False):
        self.sink = sink
        self.fail = fail
        self._payload = None

    def insert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("ledger unavailable")
        self.sink.append(self._payload)
        return self


class _Client:
    def __init__(self, *, fail=False):
        self.rows = []
        self.fail = fail
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        return _Table(self.rows, fail=self.fail)


def _app(client, *, endpoint_status=200):
    app = FastAPI()

    @app.post("/score-pick-request")
    def score_pick_request():
        if endpoint_status != 200:
            from fastapi import HTTPException
            raise HTTPException(status_code=endpoint_status, detail={"code": "BLOCKED"})
        return {"rows": [], "can_execute": False}

    @app.post("/score-team-event")
    def score_team_event():
        return {"can_execute": False}

    @app.post("/unrelated")
    def unrelated():
        return {"ok": True}

    subject.install_action_invocation_telemetry(app, db_client_fn=lambda: client)
    return app


def test_successful_invocation_is_recorded_without_certification():
    """The whole point: a receipt lands with no reviewed release in existence."""
    from v17.prop_production_registration import REVIEWED_CERTIFICATION_RELEASES

    assert REVIEWED_CERTIFICATION_RELEASES == {}, "precondition: registry is empty in production"

    client = _Client()
    with TestClient(_app(client)) as http:
        response = http.post(
            "/score-pick-request",
            json={},
            headers={"User-Agent": "ChatGPT-User/1.0", "Authorization": "Bearer super-secret-token"},
        )
    assert response.status_code == 200
    assert client.tables == [subject.LEDGER_TABLE]
    assert len(client.rows) == 1
    row = client.rows[0]
    assert row["route"] == "/score-pick-request"
    assert row["action_operation_id"] == "scoreWowPickRequest"
    assert row["http_status"] == 200
    assert row["caller_class"] == "CHATGPT_ACTION"
    assert row["auth_scheme"] == "BEARER"
    assert row["can_execute"] is False


def test_no_credential_is_ever_recorded():
    client = _Client()
    with TestClient(_app(client)) as http:
        http.post(
            "/score-pick-request",
            json={},
            headers={"Authorization": "Bearer super-secret-token", "User-Agent": "ChatGPT-User/1.0"},
        )
    serialized = repr(client.rows)
    assert "super-secret-token" not in serialized
    assert client.rows[0]["auth_scheme"] == "BEARER"


@pytest.mark.parametrize("status", [401, 409, 422, 500])
def test_failed_invocations_are_recorded(status):
    """Failures are the signal the canary could never provide."""
    client = _Client()
    with TestClient(_app(client, endpoint_status=status), raise_server_exceptions=False) as http:
        http.post("/score-pick-request", json={}, headers={"User-Agent": "ChatGPT-User/1.0"})
    assert len(client.rows) == 1
    assert client.rows[0]["http_status"] == status


def test_schema_rejected_request_is_still_recorded():
    """A 422 never reaches the endpoint, so a route wrapper could not see it."""
    app = FastAPI()

    from pydantic import BaseModel

    class _Body(BaseModel):
        required_field: str

    @app.post("/score-team-event")
    def score_team_event(body: _Body):
        return {"can_execute": False}

    client = _Client()
    subject.install_action_invocation_telemetry(app, db_client_fn=lambda: client)
    with TestClient(app) as http:
        response = http.post("/score-team-event", json={})
    assert response.status_code == 422
    assert len(client.rows) == 1
    assert client.rows[0]["http_status"] == 422
    assert client.rows[0]["action_operation_id"] == "scoreWowTeamEvent"


def test_non_action_routes_are_not_recorded():
    client = _Client()
    with TestClient(_app(client)) as http:
        http.post("/unrelated", json={})
    assert client.rows == []


def test_ledger_failure_never_breaks_the_request():
    client = _Client(fail=True)
    with TestClient(_app(client)) as http:
        response = http.post("/score-pick-request", json={})
    assert response.status_code == 200
    assert client.rows == []


def test_client_factory_failure_never_breaks_the_request():
    def _explode():
        raise RuntimeError("no client")

    app = FastAPI()

    @app.post("/score-pick-request")
    def score_pick_request():
        return {"can_execute": False}

    subject.install_action_invocation_telemetry(app, db_client_fn=_explode)
    with TestClient(app) as http:
        assert http.post("/score-pick-request", json={}).status_code == 200


def test_install_is_idempotent():
    app = FastAPI()

    @app.post("/score-pick-request")
    def score_pick_request():
        return {"can_execute": False}

    client = _Client()
    assert subject.install_action_invocation_telemetry(app, db_client_fn=lambda: client) is True
    assert subject.install_action_invocation_telemetry(app, db_client_fn=lambda: client) is True
    with TestClient(app) as http:
        http.post("/score-pick-request", json={})
    assert len(client.rows) == 1


def test_disabled_ledger_still_logs_but_does_not_persist(monkeypatch, caplog):
    monkeypatch.setenv(subject.ENABLE_ENV, "0")
    client = _Client()
    with caplog.at_level("WARNING"):
        with TestClient(_app(client)) as http:
            http.post("/score-pick-request", json={})
    assert client.rows == []
    assert "WOW_V17_ACTION_INVOCATION" in caplog.text


@pytest.mark.parametrize(
    "agent,expected",
    [
        ("ChatGPT-User/1.0", "CHATGPT_ACTION"),
        ("openai-python/1.2", "CHATGPT_ACTION"),
        ("GitHub-Actions-Runner", "GITHUB_ACTIONS"),
        ("wow-self-acceptance/1", "SELF_ACCEPTANCE"),
        ("Go-http-client/2.0", "RENDER_INTERNAL"),
        ("curl/8.0", "OTHER"),
        ("", "UNKNOWN"),
    ],
)
def test_caller_classification(agent, expected):
    assert subject.classify_caller(agent) == expected


@pytest.mark.parametrize(
    "header,expected",
    [("Bearer abc", "BEARER"), ("Basic abc", "BASIC"), ("Weird abc", "OTHER"), ("", "NONE"), (None, "NONE")],
)
def test_auth_scheme_classification(header, expected):
    assert subject.classify_auth_scheme(header) == expected


def test_operation_map_matches_migration_route_constraint():
    sql = (Path(__file__).parent / "migrations" / "20260918_action_invocation_receipts.sql").read_text()
    for route in subject.OPERATION_BY_ROUTE:
        assert f"'{route}'" in sql, f"{route} missing from migration route constraint"


def test_migration_is_service_role_only_and_fail_closed():
    sql = (Path(__file__).parent / "migrations" / "20260918_action_invocation_receipts.sql").read_text().lower()
    assert "enable row level security" in sql
    assert "revoke all on table public.wow_action_invocation_receipts from public, anon, authenticated" in sql
    assert "grant select, insert on table public.wow_action_invocation_receipts to service_role" in sql
    assert "check (can_execute = false)" in sql
    assert "update" not in sql.split("grant")[1].split(";")[0]


def test_both_team_event_route_variants_are_observed():
    """P0-C: /score-team-event was absent, so direct calls had no latency record."""
    assert "/score-team-event" in INTERACTIVE_PATHS
    assert "/score-team-event-request" in INTERACTIVE_PATHS
    assert "/score-pick-request" in INTERACTIVE_PATHS


def test_telemetry_declares_no_execution_authority():
    assert subject.CAN_EXECUTE is False
