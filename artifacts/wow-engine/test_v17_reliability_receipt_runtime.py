from datetime import datetime, timezone
import hashlib

from v17 import reliability_receipt_runtime as runtime
from v17.receipt_schema import VerificationReceipt, expected_sentinel_signature, schema_hash


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.row = None

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


def _payload():
    digest = hashlib.sha256(b"response").hexdigest()
    trace = hashlib.sha256(b"trace").hexdigest()
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
        "raw_response_digest": digest,
        "execution_trace_digest": trace,
        "timestamp_utc": datetime.now(timezone.utc),
        "sentinel_workflow": "wow-v17-release-production-verification-agent",
        "sentinel_workflow_run_id": 123,
        "sentinel_signature": "sha256:" + ("0" * 64),
        "audit_artifact_name": "receipt",
    }
    unsigned = VerificationReceipt.model_validate(payload)
    payload["sentinel_signature"] = expected_sentinel_signature(unsigned)
    return VerificationReceipt.model_validate(payload).model_dump(mode="json")


def test_receipt_persistence_is_insert_once_and_repeat_safe():
    db = DB()
    payload = _payload()

    first = runtime._persist(db, payload)
    second = runtime._persist(db, payload)

    assert first["status"] == "PERSISTED"
    assert second["status"] == "ALREADY_PERSISTED"
    assert len(db.rows) == 1
    assert db.rows[0]["merge_sha"] == "b" * 40
    assert db.rows[0]["deployed_render_sha"] == "b" * 40
    assert db.rows[0]["can_execute"] is False
