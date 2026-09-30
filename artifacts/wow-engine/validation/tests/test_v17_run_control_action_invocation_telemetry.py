import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17.action_invocation_telemetry import (
    RECORD_RECEIPT_RPC,
    RECORD_RECOVERY_RPC,
    RECONCILE_RECOVERY_RPC,
    install_action_invocation_middleware,
)


class _Result:
    def __init__(self, data):
        self.data = data


class _DB:
    def __init__(self, sink):
        self.sink = sink

    def rpc(self, name, params):
        return _RPC(self, name, params)


class _RPC:
    def __init__(self, db, name, params):
        self.db = db
        self.name = name
        self.params = params

    def execute(self):
        if self.name == RECONCILE_RECOVERY_RPC:
            return _Result({"status": "RECONCILED", "rows_deleted": 0})
        if self.name == RECORD_RECEIPT_RPC:
            self.db.sink.append(dict(self.params["p_receipt"]))
            return _Result({"status": "INSERTED"})
        if self.name == RECORD_RECOVERY_RPC:
            return _Result({"status": self.params["p_state"]})
        raise AssertionError(self.name)


def _wait_for_receipts(app, receipts, expected):
    deadline = time.monotonic() + 2.0
    while len(receipts) < expected and time.monotonic() < deadline:
        time.sleep(0.01)
    deadline = time.monotonic() + 2.0
    while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(receipts) == expected


def test_run_control_routes_are_normalized_and_auditable_without_request_body_capture():
    receipts = []
    app = FastAPI()
    install_action_invocation_middleware(app, db_client_fn=lambda: _DB(receipts))

    @app.post("/v17/pick-request-runs/resumable")
    def resumable():
        return {"status": "QUEUED", "can_execute": False}

    @app.get("/v17/pick-request-runs/{request_id}")
    def state(request_id: str):
        return {"request_id": request_id, "can_execute": False}

    @app.post("/v17/pick-request-runs/{request_id}/close")
    def close(request_id: str):
        return {"request_id": request_id, "can_execute": False}

    headers = {
        "Authorization": "Bearer test-action-key-placeholder",
        "User-Agent": "Python-urllib/3.12",
    }
    request_id = "wow-v17-run-control-telemetry-test"

    with TestClient(app) as client:
        assert client.post(
            "/v17/pick-request-runs/resumable",
            headers={**headers, "X-WOW-Request-ID": request_id, "X-WOW-Rows-In": "5"},
            json={"request_id": request_id, "sensitive_rows": "must-not-be-persisted"},
        ).status_code == 200
        assert client.get(
            f"/v17/pick-request-runs/{request_id}", headers=headers
        ).status_code == 200
        assert client.post(
            f"/v17/pick-request-runs/{request_id}/close",
            headers=headers,
            json={"closure_reason": "must-not-be-persisted"},
        ).status_code == 200
        _wait_for_receipts(app, receipts, 3)

    by_operation = {item["action_operation_id"]: item for item in receipts}
    resumable_receipt = by_operation["runWowV17ResumablePickRequest"]
    state_receipt = by_operation["getWowV17PickRequestRunState"]
    close_receipt = by_operation["closeWowV17PickRequestRun"]

    assert resumable_receipt["route"] == "/v17/pick-request-runs/resumable"
    assert resumable_receipt["request_id"] == request_id
    assert resumable_receipt["rows_in"] == 5
    assert state_receipt["route"] == "/v17/pick-request-runs/{request_id}"
    assert state_receipt["request_id"] == request_id
    assert close_receipt["route"] == "/v17/pick-request-runs/{request_id}/close"
    assert close_receipt["request_id"] == request_id

    for receipt in receipts:
        assert receipt["auth_scheme"] == "BEARER"
        assert receipt["caller_class"] == "ACTION_API_KEY"
        assert receipt["can_execute"] is False
        assert request_id not in receipt["route"]

    serialized = repr(receipts)
    assert "test-action-key-placeholder" not in serialized
    assert "must-not-be-persisted" not in serialized
