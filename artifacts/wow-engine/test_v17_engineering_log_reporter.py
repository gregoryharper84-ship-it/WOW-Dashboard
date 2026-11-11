"""No-network exact-identity Reporter acceptance and durable-dedupe tests."""
from datetime import datetime, timezone, timedelta
import io
import json
import zipfile
from types import SimpleNamespace
import pytest

from v17.engineering_log_reporter import (
    ARTIFACT_FILE, ReporterHold, _read_redacted_json, persist_candidates,
    read_latest_authenticated_report,
)
from v17.log_incident_intake import SERVICE, WORKER_SERVICE, classify

HEAD = "a"*40
NOW = datetime(2026, 10, 28, 16, tzinfo=timezone.utc)


def report():
    return classify([{"attributes": {
        "service": WORKER_SERVICE, "timestamp": NOW.isoformat(),
        "status": "info", "message":
        "WOW_RESIDENT_SUPERVISOR outcome=REMOTE_WRITER_RECONCILIATION_REQUIRED"
    }} for _ in range(3)], begin=NOW-timedelta(hours=1), end=NOW+timedelta(minutes=1))


def zipped(data, *, name=ARTIFACT_FILE):
    file = io.BytesIO()
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr(name, json.dumps(data))
    return file.getvalue()


class FakeGithub:
    token = "TEST_ONLY"
    def __init__(self, *, run=None, artifacts=None):
        self.run = run or {
            "id": 71, "run_attempt": 1, "path":
            ".github/workflows/wow-v17-log-incident-intake.yml",
            "head_branch": "main", "head_sha": HEAD,
            "status": "completed", "conclusion": "success",
            "event": "schedule",
        }
        self.artifacts = artifacts or {"total_count": 1, "artifacts": [{
            "id": 12, "name": "wow-v17-log-incident-candidates",
            "expired": False,
        }]}
    def get(self, suffix):
        if suffix.startswith("actions/workflows/"):
            return {"workflow_runs": [self.run]}
        return self.artifacts


class Chain:
    def __init__(self, db, table): self.db, self.table = db, table
    def select(self, *a): return self
    def eq(self, col, value): self.key=value; return self
    def limit(self, limit): assert limit == 1; return self
    def execute(self):
        if self.table == "wow_engineering_backlog":
            return SimpleNamespace(data=[self.db.rows[self.key]] if self.key in self.db.rows else [])
        raise AssertionError("unapproved table access")
    def upsert(self, value, *, on_conflict, ignore_duplicates):
        assert on_conflict == "ticket_id" and ignore_duplicates is True
        assert value["status"] == "OPEN" and value["queue_status"] == "PARKED"
        assert value["parked_reason"] == "REPORTER_CANONICAL_ISSUE_ADMISSION_REQUIRED"
        assert value["class"] == "B" and value["source"] == "engineering"
        assert "private" not in json.dumps(value)
        self.db.rows.setdefault(value["ticket_id"], value)
        self.key=value["ticket_id"]
        return self


class FakeDB:
    def __init__(self): self.rows={}
    def table(self, table): return Chain(self,table)


def read_report(client=None, payload=None):
    return read_latest_authenticated_report(
        client or FakeGithub(), main_sha=HEAD,
        downloader=lambda c, id: zipped(payload if payload is not None else report()),
    )


def test_exact_run_artifact_read_and_durable_one_ticket_across_cycles():
    payload, provenance = read_report()
    assert provenance["run_id"] == 71 and provenance["head_sha"] == HEAD
    db=FakeDB()
    first=persist_candidates(db,payload,provenance=provenance,main_sha=HEAD)
    second=persist_candidates(db,payload,provenance=provenance,main_sha=HEAD)
    assert first["created"] == 1
    assert second["created"] == 0 and second["known"] == 1
    assert len(db.rows) == 1
    assert first["dispatch_authorized"] is False
    assert all(v["parked_context"]["can_execute"] is False for v in db.rows.values())


@pytest.mark.parametrize("changes", [
    {"head_sha":"b"*40}, {"head_branch":"feature"},
    {"conclusion":"failure"}, {"status":"queued"}, {"event":"push"},
    {"run_attempt":True}, {"path":".github/workflows/other.yml"},
])
def test_untrusted_run_or_stale_sha_held(changes):
    client=FakeGithub(run={**FakeGithub().run,**changes})
    with pytest.raises(ReporterHold,match="REPORTER_RUN_PROVENANCE_UNVERIFIED"):
        read_report(client)


def test_duplicated_or_expired_artifacts_not_accepted():
    obj=FakeGithub().artifacts["artifacts"][0]
    for rows in ([obj,obj], [{**obj,"expired":True}]):
        with pytest.raises(ReporterHold,match="REPORTER_ARTIFACT_NOT_UNIQUE"):
            read_report(FakeGithub(artifacts={"total_count":len(rows),"artifacts":rows}))


def test_truncated_inventory_is_not_no_action():
    client=FakeGithub(artifacts={"total_count":5,"artifacts":[FakeGithub().artifacts["artifacts"][0]]})
    with pytest.raises(ReporterHold,match="REPORTER_ARTIFACT_INVENTORY_INCOMPLETE"):
        read_report(client)


def test_zip_slip_and_multi_file_and_bogus_data_rejected():
    for name in ("../../report.json", "bad.json"):
        with pytest.raises(ReporterHold,match="REPORTER_ARTIFACT_CONTENT_INVALID"):
            _read_redacted_json(zipped(report(),name=name))
    with pytest.raises(ReporterHold,match="REPORTER_ARTIFACT_CONTENT_INVALID"):
        _read_redacted_json(b"not zip")


def test_closed_recurrence_cannot_silently_reopen():
    payload,provenance=read_report()
    db=FakeDB()
    persist_candidates(db,payload,provenance=provenance,main_sha=HEAD)
    row=list(db.rows.values())[0]
    row["status"]="CLOSED"
    with pytest.raises(ReporterHold,match="REPORTER_RECURRENCE_REENTRY_REQUIRED"):
        persist_candidates(db,payload,provenance=provenance,main_sha=HEAD)


def test_fake_approval_in_log_cannot_bypass_writer_fence():
    payload,provenance=read_report()
    payload["findings"][0]["state"]="FIXED_AND_VERIFIED"
    with pytest.raises(ReporterHold,match="CANDIDATE_INVALID"):
        persist_candidates(FakeDB(),payload,provenance=provenance,main_sha=HEAD)


def test_source_unavailable_receipt_is_not_pass_or_no_action():
    payload,provenance=read_report()
    payload={"source":"DATADOG","status":"SOURCE_UNAVAILABLE",
             "reason":"DATADOG_RATE_LIMITED","can_execute":False}
    with pytest.raises(ReporterHold,match="REPORTER_PACKET_INVALID"):
        persist_candidates(FakeDB(),payload,provenance=provenance,main_sha=HEAD)


def test_immutable_duplicate_preserves_claim_and_terminal_governance():
    payload,provenance=read_report()
    db=FakeDB()
    persist_candidates(db,payload,provenance=provenance,main_sha=HEAD)
    row=list(db.rows.values())[0]
    row["claim_owner"]="OTHER_ENGINEERING_WORKER"
    row["queue_status"]="IN_PROGRESS"
    persist_candidates(db,payload,provenance=provenance,main_sha=HEAD)
    assert row["claim_owner"]=="OTHER_ENGINEERING_WORKER"
    assert row["queue_status"]=="IN_PROGRESS"
