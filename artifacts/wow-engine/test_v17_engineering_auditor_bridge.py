from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from v17.engineering_auditor_bridge_api import install_engineering_audit_bridge_routes
from v17.engineering_auditor_http_client import EngineeringAuditHttpClient


TOKEN = "test-engineering-bridge-token"


@dataclass
class _Response:
    data: list[dict]


class _Query:
    def __init__(self, table: str, calls: list):
        self.table = table
        self.calls = calls

    def select(self, columns="*"):
        self.calls.append(("select", self.table, columns))
        return self

    def insert(self, payload):
        self.calls.append(("insert", self.table, payload))
        return self

    def upsert(self, payload, on_conflict=None):
        self.calls.append(("upsert", self.table, payload, on_conflict))
        return self

    def update(self, payload):
        self.calls.append(("update", self.table, payload))
        return self

    def eq(self, column, value):
        self.calls.append(("eq", column, value))
        return self

    def lte(self, column, value):
        self.calls.append(("lte", column, value))
        return self

    def limit(self, value):
        self.calls.append(("limit", value))
        return self

    def execute(self):
        self.calls.append(("execute", self.table))
        return _Response(data=[{"ok": True, "table": self.table}])


class _Db:
    def __init__(self):
        self.calls = []

    def table(self, table):
        self.calls.append(("table", table))
        return _Query(table, self.calls)


def _client(monkeypatch):
    monkeypatch.setenv("WOW_ENGINEERING_AUDIT_BRIDGE_TOKEN", TOKEN)
    db = _Db()
    app = FastAPI()
    install_engineering_audit_bridge_routes(app, db_client_fn=lambda: db)
    return TestClient(app), db, app


def _post(client: TestClient, payload: dict, *, token: str | None = TOKEN):
    headers = {"X-WOW-Engineering-Token": token} if token is not None else {}
    return client.post("/internal/engineering-audit-store", json=payload, headers=headers)


def test_bridge_is_hidden_from_openapi_and_requires_worker_token(monkeypatch):
    client, _db, app = _client(monkeypatch)
    paths = app.openapi()["paths"]
    assert "/internal/engineering-audit-store" not in paths

    response = _post(
        client,
        {"table": "wow_engineering_backlog", "operation": "select"},
        token=None,
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "ENGINEERING_AUDIT_BRIDGE_UNAUTHORIZED"


def test_bridge_allows_narrow_read_and_preserves_non_execution_receipt(monkeypatch):
    client, db, _app = _client(monkeypatch)
    response = _post(
        client,
        {
            "table": "wow_engineering_audit_work_items",
            "operation": "select",
            "columns": "work_item_id,state",
            "filters": [{"operator": "eq", "column": "state", "value": "OPEN"}],
            "limit": 10,
            "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["can_execute"] is False
    assert body["probability_authority"] == "NONE"
    assert body["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert ("table", "wow_engineering_audit_work_items") in db.calls


def test_bridge_rejects_non_audit_tables_and_unfiltered_updates(monkeypatch):
    client, _db, _app = _client(monkeypatch)
    forbidden = _post(client, {"table": "predictions", "operation": "select"})
    assert forbidden.status_code == 403

    unfiltered = _post(
        client,
        {
            "table": "wow_engineering_auditor_runtime",
            "operation": "update",
            "payload": {
                "status": "RUNNING",
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            },
        },
    )
    assert unfiltered.status_code == 400
    assert unfiltered.json()["detail"]["code"] == "ENGINEERING_AUDIT_UNFILTERED_UPDATE_FORBIDDEN"


def test_bridge_rejects_probability_or_execution_authority_payloads(monkeypatch):
    client, _db, _app = _client(monkeypatch)
    probability = _post(
        client,
        {
            "table": "wow_engineering_audit_findings",
            "operation": "insert",
            "payload": {
                "fingerprint": "x",
                "finding_type": "CODE_HEALTH",
                "component": "x",
                "severity": "P1",
                "evidence": {"model_probability": 0.61},
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            },
        },
    )
    assert probability.status_code == 400
    assert probability.json()["detail"]["code"] == "ENGINEERING_AUDIT_PROBABILITY_BOUNDARY_VIOLATION"

    execution = _post(
        client,
        {
            "table": "wow_agent_audit_events",
            "operation": "insert",
            "payload": {
                "event_type": "TEST",
                "actor": "auditor",
                "detail_redacted": {},
                "can_execute": True,
            },
        },
    )
    assert execution.status_code == 400


def test_bridge_only_mutates_auditor_owned_backlog_rows(monkeypatch):
    client, _db, _app = _client(monkeypatch)
    bad = _post(
        client,
        {
            "table": "wow_engineering_backlog",
            "operation": "update",
            "payload": {"status": "CLOSED"},
            "filters": [{"operator": "eq", "column": "ticket_id", "value": "OTHER-123"}],
        },
    )
    assert bad.status_code == 403

    good = _post(
        client,
        {
            "table": "wow_engineering_backlog",
            "operation": "update",
            "payload": {"status": "CLOSED", "terminal_state": "FIXED_AND_VERIFIED"},
            "filters": [{"operator": "eq", "column": "ticket_id", "value": "AUDIT-ABC123"}],
        },
    )
    assert good.status_code == 200


def test_bridge_enforces_exact_upsert_conflict_keys(monkeypatch):
    client, _db, _app = _client(monkeypatch)
    payload = {
        "fingerprint": "fp-1",
        "source_system": "GITHUB",
        "source_kind": "GITHUB_PR",
        "source_ref": "1159",
        "title": "test",
        "state": "OPEN",
        "source_status": "OPEN",
        "severity": "P1",
        "draft": False,
        "labels": [],
        "state_payload": {},
        "last_meaningful_progress_at": "2026-10-01T22:00:00Z",
        "next_audit_at": "2026-10-01T23:00:00Z",
        "last_seen_at": "2026-10-01T22:00:00Z",
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "updated_at": "2026-10-01T22:00:00Z",
    }
    wrong = _post(
        client,
        {
            "table": "wow_engineering_audit_work_items",
            "operation": "upsert",
            "payload": payload,
            "on_conflict": "source_ref",
        },
    )
    assert wrong.status_code == 400

    correct = _post(
        client,
        {
            "table": "wow_engineering_audit_work_items",
            "operation": "upsert",
            "payload": payload,
            "on_conflict": "fingerprint",
        },
    )
    assert correct.status_code == 200


def test_worker_http_adapter_preserves_chain_shape_and_never_sends_execution_authority(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {"data": [{"auditor_id": "WOW_V17_ENGINEERING_AUDITOR"}]}

    def fake_post(url, *, headers, json, timeout):
        captured.update({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("v17.engineering_auditor_http_client.requests.post", fake_post)
    adapter = EngineeringAuditHttpClient("https://example.invalid/internal/engineering-audit-store", "secret-token")
    response = adapter.table("wow_engineering_auditor_runtime").select("auditor_id").eq(
        "auditor_id", "WOW_V17_ENGINEERING_AUDITOR"
    ).limit(1).execute()

    assert response.data[0]["auditor_id"] == "WOW_V17_ENGINEERING_AUDITOR"
    assert captured["json"]["can_execute"] is False
    assert captured["json"]["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert captured["headers"]["X-WOW-Engineering-Token"] == "secret-token"


def test_worker_http_adapter_requires_https():
    with pytest.raises(ValueError, match="must use https"):
        EngineeringAuditHttpClient("http://example.invalid/internal", "token")
