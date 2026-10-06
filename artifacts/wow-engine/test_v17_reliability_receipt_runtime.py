import base64
from datetime import datetime, timezone
import hashlib
import json

import pytest
from fastapi import HTTPException

from v17 import reliability_receipt_runtime as runtime
from v17.receipt_schema import VerificationReceipt, expected_sentinel_signature, schema_hash


AUTH_CLAIMS = {"run_id": "123"}


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.row = None
        self.lookup = ""

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, _field, value):
        self.lookup = value
        return self

    def limit(self, _value):
        return self

    def insert(self, row):
        self.row = row
        return self

    def execute(self):
        if self.row is not None:
            self.db.rows.append(self.row)
            return Result([self.row])
        found = [r for r in self.db.rows if r["sentinel_signature"] == self.lookup]
        return Result(found)


class DB:
    def __init__(self):
        self.rows = []

    def table(self, table):
        assert table == runtime.TABLE
        return Query(self, table)


def _envelope(trace_overrides=None):
    response = b'{"status":"ok","can_execute":false}\n'
    headers = (
        b"HTTP/2 200\r\n"
        b"X-WOW-Dry-Run-Only: true\r\n"
        b"X-WOW-Can-Execute: false\r\n"
    )
    trace_payload = {
        "contract_version": "WOW_ENGINEERING_RELIABILITY_V1",
        "issue_id": 1388,
        "pr_number": 1437,
        "exact_head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "deployed_render_sha": "b" * 40,
        "method": "GET",
        "route_tested": "/health/live",
        "workflow": "wow-v17-release-production-verification-agent",
        "workflow_run_id": 123,
        "runtime_environment_signature": "e" * 64,
        "executed_utc": datetime.now(timezone.utc).isoformat(),
        "can_execute": False,
    }
    trace_payload.update(trace_overrides or {})
    trace = (json.dumps(trace_payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    payload = {
        "contract_version": "WOW_ENGINEERING_RELIABILITY_V1",
        "issue_id": 1388,
        "pr_number": 1437,
        "exact_head_sha": "a" * 40,
        "merge_sha": "b" * 40,
        "deployed_render_sha": "b" * 40,
        "method": "GET",
        "route_tested": "/health/live",
        "schema_hash": schema_hash(),
        "http_status": 200,
        "dry_run_header_present": True,
        "can_execute_header_false": True,
        "raw_response_digest": hashlib.sha256(response).hexdigest(),
        "execution_trace_digest": hashlib.sha256(trace).hexdigest(),
        "timestamp_utc": datetime.now(timezone.utc),
        "sentinel_workflow": "wow-v17-release-production-verification-agent",
        "sentinel_workflow_run_id": 123,
        "sentinel_signature": "sha256:" + ("0" * 64),
        "audit_artifact_name": "receipt",
    }
    unsigned = VerificationReceipt.model_validate(payload)
    payload["sentinel_signature"] = expected_sentinel_signature(unsigned)
    receipt = VerificationReceipt.model_validate(payload).model_dump(mode="json")
    return {
        "receipt": receipt,
        "raw_response_body_base64": base64.b64encode(response).decode("ascii"),
        "raw_response_headers_base64": base64.b64encode(headers).decode("ascii"),
        "execution_trace_base64": base64.b64encode(trace).decode("ascii"),
    }


def test_receipt_persistence_is_insert_once_and_repeat_safe():
    db = DB()
    envelope = _envelope()

    first = runtime._persist(db, envelope, auth_claims=AUTH_CLAIMS)
    second = runtime._persist(db, envelope, auth_claims=AUTH_CLAIMS)

    assert first["status"] == "PERSISTED"
    assert second["status"] == "ALREADY_PERSISTED"
    assert len(db.rows) == 1
    assert db.rows[0]["merge_sha"] == "b" * 40
    assert db.rows[0]["deployed_render_sha"] == "b" * 40
    assert db.rows[0]["raw_response_body_base64"] == envelope["raw_response_body_base64"]
    assert db.rows[0]["raw_response_headers_base64"] == envelope["raw_response_headers_base64"]
    assert db.rows[0]["execution_trace_base64"] == envelope["execution_trace_base64"]
    assert db.rows[0]["can_execute"] is False


@pytest.mark.parametrize(
    ("field", "replacement", "expected_error"),
    [
        ("raw_response_body_base64", base64.b64encode(b"tampered").decode("ascii"), "RAW_RESPONSE_DIGEST_MISMATCH"),
        ("execution_trace_base64", base64.b64encode(b"tampered-trace").decode("ascii"), "EXECUTION_TRACE_DIGEST_MISMATCH"),
        (
            "raw_response_headers_base64",
            base64.b64encode(
                b"HTTP/2 200\r\nX-WOW-Dry-Run-Only: false\r\nX-WOW-Can-Execute: false\r\n"
            ).decode("ascii"),
            "RAW_DRY_RUN_HEADER_INVALID",
        ),
    ],
)
def test_receipt_persistence_recomputes_exact_evidence_before_insert(field, replacement, expected_error):
    db = DB()
    envelope = _envelope()
    envelope[field] = replacement

    with pytest.raises(HTTPException) as exc:
        runtime._persist(db, envelope, auth_claims=AUTH_CLAIMS)

    detail = exc.value.detail
    assert detail["code"] == "INVALID_RECEIPT_EVIDENCE"
    assert expected_error in detail["errors"]
    assert db.rows == []


def test_receipt_persistence_rejects_semantically_unbound_trace_even_when_digest_matches():
    db = DB()
    envelope = _envelope({"issue_id": 999})

    with pytest.raises(HTTPException) as exc:
        runtime._persist(db, envelope, auth_claims=AUTH_CLAIMS)

    assert exc.value.detail["code"] == "INVALID_RECEIPT_EVIDENCE"
    assert "TRACE_BINDING_MISMATCH:issue_id" in exc.value.detail["errors"]
    assert "EXECUTION_TRACE_DIGEST_MISMATCH" not in exc.value.detail["errors"]
    assert db.rows == []


def test_receipt_persistence_rejects_oversized_evidence():
    db = DB()
    envelope = _envelope()
    envelope["execution_trace_base64"] = base64.b64encode(
        b"x" * (runtime.MAX_EVIDENCE_BYTES + 1)
    ).decode("ascii")

    with pytest.raises(HTTPException) as exc:
        runtime._persist(db, envelope, auth_claims=AUTH_CLAIMS)

    assert exc.value.status_code == 413
    assert exc.value.detail["code"] == "RECEIPT_EVIDENCE_TOO_LARGE"
    assert db.rows == []


def test_receipt_persistence_binds_workflow_run_id_to_oidc_claim():
    db = DB()
    envelope = _envelope()

    with pytest.raises(HTTPException) as exc:
        runtime._persist(db, envelope, auth_claims={"run_id": "999"})

    assert exc.value.status_code == 401
    assert exc.value.detail["code"] == "RECEIPT_OIDC_BINDING_MISMATCH"
    assert "SENTINEL_WORKFLOW_RUN_ID_MISMATCH" in exc.value.detail["errors"]
    assert db.rows == []
