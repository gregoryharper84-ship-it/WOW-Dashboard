import logging
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from v17 import action_invocation_telemetry as telemetry


class _Result:
    def __init__(self, data):
        self.data = data


class _AmbiguousThirdAttemptDB:
    def __init__(self):
        self.primary_attempts = 0
        self.receipts = []
        self.recovery = {}

    def rpc(self, name, params):
        return _AmbiguousThirdAttemptRPC(self, name, params)


class _AmbiguousThirdAttemptRPC:
    def __init__(self, db, name, params):
        self.db = db
        self.name = name
        self.params = params

    def execute(self):
        if self.name == telemetry.RECONCILE_RECOVERY_RPC:
            return _Result({"status": "RECONCILED", "rows_deleted": 0})

        receipt = dict(self.params["p_receipt"])
        invocation_id = receipt["invocation_id"]

        if self.name == telemetry.RECORD_RECEIPT_RPC:
            self.db.primary_attempts += 1
            if self.db.primary_attempts < 3:
                raise TimeoutError("simulated pre-commit transport timeout")
            if not self.db.receipts:
                self.db.receipts.append(receipt)
            # Commit succeeded, but the response was lost.
            raise TimeoutError("response lost after database commit")

        if self.name == telemetry.RECORD_RECOVERY_RPC:
            if any(row["invocation_id"] == invocation_id for row in self.db.receipts):
                self.db.recovery.pop(invocation_id, None)
                return _Result({"status": "RECONCILED"})
            state = self.params["p_state"]
            self.db.recovery[invocation_id] = {
                "state": state,
                "attempt_count": self.params["p_attempt_count"],
            }
            return _Result({"status": state})

        raise AssertionError(self.name)


def test_third_attempt_late_commit_reconciles_without_false_dead_letter(monkeypatch, caplog):
    monkeypatch.setattr(telemetry, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    db = _AmbiguousThirdAttemptDB()
    app = FastAPI()
    telemetry.install_action_invocation_middleware(app, db_client_fn=lambda: db)

    @app.post("/score-prop")
    def score_prop():
        return {"ok": True, "can_execute": False}

    with caplog.at_level(logging.WARNING, logger="wow.v17.action_invocation"):
        with TestClient(app) as client:
            response = client.post("/score-prop", headers={"X-WOW-Request-ID": "ambiguous-third"})
            assert response.status_code == 200
            deadline = time.monotonic() + 2.0
            while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
                time.sleep(0.01)

    assert db.primary_attempts == 3
    assert len(db.receipts) == 1
    assert db.receipts[0]["request_id"] == "ambiguous-third"
    assert db.recovery == {}
    messages = [record.getMessage() for record in caplog.records]
    assert not any("WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED" in message for message in messages)
    assert not any("recovery_state=DEAD_LETTER" in message for message in messages)


class _TotalFailureDB:
    def rpc(self, _name, _params):
        return _TotalFailureRPC()


class _TotalFailureRPC:
    def execute(self):
        raise TimeoutError("database unavailable")


def test_unconfirmed_recovery_never_claims_durable_dead_letter(monkeypatch, caplog):
    monkeypatch.setattr(telemetry, "_RETRY_DELAYS_SECONDS", (0.0, 0.0))
    app = FastAPI()
    telemetry.install_action_invocation_middleware(app, db_client_fn=lambda: _TotalFailureDB())

    @app.post("/score-prop")
    def score_prop():
        return {"ok": True, "can_execute": False}

    with caplog.at_level(logging.WARNING, logger="wow.v17.action_invocation"):
        with TestClient(app) as client:
            response = client.post("/score-prop")
            assert response.status_code == 200
            deadline = time.monotonic() + 2.0
            while app.state.wow_action_invocation_tasks and time.monotonic() < deadline:
                time.sleep(0.01)

    messages = [record.getMessage() for record in caplog.records]
    terminal = [message for message in messages if "WOW_V17_ACTION_INVOCATION_PERSISTENCE_FAILED" in message]
    assert terminal
    assert any("recovery_state=UNCONFIRMED" in message for message in terminal)
    assert not any("recovery_state=DEAD_LETTER" in message for message in terminal)
    assert any(
        "WOW_V17_ACTION_INVOCATION_RECOVERY_QUEUE_FAILED" in message
        and "durable_state=UNCONFIRMED" in message
        for message in messages
    )
