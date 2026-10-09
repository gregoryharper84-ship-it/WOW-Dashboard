"""Unit contract for v17/engineering_receipts.py (no network).

The real-PostgREST path is exercised in test_agent_runtime_postgres_integration.py.
"""
from __future__ import annotations

import io
import json
import uuid
from urllib.error import HTTPError, URLError

import pytest

from v17 import engineering_receipts as er

SHA = "c" * 40
URL = "https://example.supabase.co"
KEY = "service-role-test-key-not-real"


def base(**kw):
    row = dict(incident_id="1554", provider="claude", role="implementer",
               worker_run_id="run-42", disposition="IN_PROGRESS")
    row.update(kw)
    return row


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakePostgrest:
    """Records requests; behaves like PostgREST insert + filtered select."""

    def __init__(self, *, mutate_readback=None, insert_error=None, readback_rows=None):
        self.requests = []
        self.stored = None
        self.mutate_readback = mutate_readback
        self.insert_error = insert_error
        self.readback_rows = readback_rows

    def __call__(self, request, timeout):
        self.requests.append(request)
        if request.get_method() == "POST":
            if self.insert_error:
                raise self.insert_error
            row = json.loads(request.data)
            row.update(receipt_id=str(uuid.uuid4()), can_execute=False, recorded_at="2026-10-09T03:00:00+00:00")
            self.stored = row
            return FakeResponse(json.dumps([row]).encode())
        if self.readback_rows is not None:
            return FakeResponse(json.dumps(self.readback_rows).encode())
        row = dict(self.stored)
        if self.mutate_readback:
            self.mutate_readback(row)
        return FakeResponse(json.dumps([row]).encode())


@pytest.fixture
def fake(monkeypatch):
    server = FakePostgrest()
    monkeypatch.setattr(er, "urlopen", server)
    return server


def test_records_and_verifies_readback(fake):
    stored = er.record_receipt(URL, KEY, base(tests=[{"name": "t", "result": "pass"}]))
    assert stored["can_execute"] is False
    post, get = fake.requests
    assert post.get_method() == "POST" and post.full_url == URL + "/rest/v1/wow_engineering_attempt_receipts"
    assert post.headers["Prefer"] == "return=representation"
    assert get.get_method() == "GET" and "receipt_id=eq." + stored["receipt_id"] in get.full_url
    sent = json.loads(post.data)
    assert "can_execute" not in sent and sent["retry_count"] == 0


def test_never_updates_or_upserts(fake):
    er.record_receipt(URL, KEY, base())
    assert {r.get_method() for r in fake.requests} == {"POST", "GET"}
    assert all("resolution=merge-duplicates" not in (r.headers.get("Prefer") or "") for r in fake.requests)


@pytest.mark.parametrize("overrides,field", [
    ({"can_execute": False}, "can_execute"),
    ({"can_execute": True}, "can_execute"),
    ({"unexpected": 1}, "unexpected"),
    ({"incident_id": " "}, "incident_id"),
    ({"incident_id": "x" * 201}, "incident_id"),
    ({"provider": "gemini"}, "provider"),
    ({"role": "approver"}, "role"),
    ({"worker_run_id": ""}, "worker_run_id"),
    ({"disposition": "MODEL_UNAVAILABLE"}, "disposition"),
    ({"disposition": "NO_DELIVERABLE"}, "disposition"),
    ({"priority": "P4"}, "priority"),
    ({"change_class": "D"}, "change_class"),
    ({"head_sha": "abc"}, "head_sha"),
    ({"head_sha": SHA.upper()}, "head_sha"),
    ({"pr_number": 0}, "pr_number"),
    ({"pr_number": True}, "pr_number"),
    ({"pr_number": "7"}, "pr_number"),
    ({"lease_epoch": -1}, "lease_epoch"),
    ({"retry_count": -1}, "retry_count"),
    ({"tests": "passed"}, "tests"),
    ({"typed_blocker": ""}, "typed_blocker"),
    ({"supersedes_receipt_id": "not-a-uuid"}, "supersedes_receipt_id"),
    ({"qa_decision": "APPROVED"}, "qa_decision"),
    ({"release_decision": "MERGED"}, "release_decision"),
    ({"disposition": "BLOCKED_WITH_EXACT_REASON", "typed_blocker": "POLICY_DENIAL"},
     "blocked_requires_typed_blocker_owner_revisit_trigger"),
    ({"disposition": "FIXED_AND_VERIFIED", "deployed_sha": SHA},
     "fixed_and_verified_requires_deployed_sha_and_qa_pass"),
    ({"disposition": "FIXED_AND_VERIFIED", "deployed_sha": SHA, "qa_decision": "HOLD"},
     "fixed_and_verified_requires_deployed_sha_and_qa_pass"),
    ({"disposition": "FIXED_AND_VERIFIED", "qa_decision": "PASS"},
     "fixed_and_verified_requires_deployed_sha_and_qa_pass"),
    ({"disposition": "PR_CREATED", "pr_number": 5}, "pr_created_requires_pr_number_and_head_sha"),
])
def test_invalid_receipts_fail_before_network(fake, overrides, field):
    with pytest.raises(er.ReceiptError) as exc:
        er.record_receipt(URL, KEY, base(**overrides))
    assert exc.value.code == "RECEIPT_INVALID:" + field
    assert fake.requests == []


@pytest.mark.parametrize("overrides", [
    {"disposition": "PR_CREATED", "pr_number": 1552, "head_sha": SHA, "change_class": "B", "priority": "P2"},
    {"disposition": "FIXED_AND_VERIFIED", "deployed_sha": SHA, "qa_decision": "PASS"},
    {"disposition": "BLOCKED_WITH_EXACT_REASON", "typed_blocker": "OWNER_GOVERNANCE_BOUNDARY",
     "blocker_owner": "repository_owner", "revisit_trigger": "QA app identity created"},
    {"supersedes_receipt_id": str(uuid.uuid4()), "lease_id": "lease-1", "lease_epoch": 0},
])
def test_valid_terminal_receipts_accepted(fake, overrides):
    er.record_receipt(URL, KEY, base(**overrides))


@pytest.mark.parametrize("url", ["", "http://example.supabase.co", "ftp://x", "https://"])
def test_insecure_or_missing_url_is_configuration_error(fake, url):
    with pytest.raises(er.ReceiptError, match="RECEIPT_CONFIGURATION_MISSING"):
        er.record_receipt(url, KEY, base())
    assert fake.requests == []


def test_missing_key_is_configuration_error(fake):
    with pytest.raises(er.ReceiptError, match="RECEIPT_CONFIGURATION_MISSING"):
        er.record_receipt(URL, "", base())


def test_localhost_http_allowed_for_ephemeral_ci(fake):
    er.record_receipt("http://localhost:3000", KEY, base())


@pytest.mark.parametrize("error,code", [
    (HTTPError(URL, 400, "bad", {}, None), "RECEIPT_PERSISTENCE_REJECTED"),
    (HTTPError(URL, 403, "denied", {}, None), "RECEIPT_PERSISTENCE_REJECTED"),
    (HTTPError(URL, 503, "down", {}, None), "RECEIPT_PERSISTENCE_UNAVAILABLE"),
    (URLError("dns"), "RECEIPT_PERSISTENCE_UNAVAILABLE"),
    (TimeoutError(), "RECEIPT_PERSISTENCE_UNAVAILABLE"),
])
def test_transport_failures_are_typed(monkeypatch, error, code):
    monkeypatch.setattr(er, "urlopen", FakePostgrest(insert_error=error))
    with pytest.raises(er.ReceiptError) as exc:
        er.record_receipt(URL, KEY, base())
    assert exc.value.code == code


def test_readback_mismatch_is_not_success(monkeypatch):
    monkeypatch.setattr(er, "urlopen", FakePostgrest(mutate_readback=lambda r: r.update(disposition="DUPLICATE")))
    with pytest.raises(er.ReceiptError, match="RECEIPT_READBACK_MISMATCH"):
        er.record_receipt(URL, KEY, base())


def test_readback_can_execute_true_is_not_success(monkeypatch):
    monkeypatch.setattr(er, "urlopen", FakePostgrest(mutate_readback=lambda r: r.update(can_execute=True)))
    with pytest.raises(er.ReceiptError, match="RECEIPT_READBACK_MISMATCH"):
        er.record_receipt(URL, KEY, base())


def test_readback_missing_is_not_success(monkeypatch):
    monkeypatch.setattr(er, "urlopen", FakePostgrest(readback_rows=[]))
    with pytest.raises(er.ReceiptError, match="RECEIPT_READBACK_MISSING"):
        er.record_receipt(URL, KEY, base())


def test_cli_success_and_no_secret_leak(fake, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    payload = tmp_path / "r.json"
    payload.write_text(json.dumps(base()))
    assert er.main(["--receipt-json", str(payload)]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["status"] == "RECEIPT_PERSISTED_AND_VERIFIED"
    assert KEY not in out and URL not in out


@pytest.mark.parametrize("env,payload,code,reason", [
    ({}, base(), 2, "RECEIPT_CONFIGURATION_MISSING"),
    ({"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": KEY}, base(disposition="DONE"), 1, "RECEIPT_INVALID:disposition"),
    ({"SUPABASE_URL": URL, "SUPABASE_SERVICE_ROLE_KEY": KEY}, ["not", "object"], 1, "RECEIPT_INVALID:payload"),
])
def test_cli_non_success_is_typed_and_nonzero(fake, monkeypatch, capsys, tmp_path, env, payload, code, reason):
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    path = tmp_path / "r.json"
    path.write_text(json.dumps(payload))
    assert er.main(["--receipt-json", str(path)]) == code
    out = json.loads(capsys.readouterr().out)
    assert out == {"status": "NON_SUCCESS", "reason": reason, "can_execute": False}


def test_cli_unavailable_exit_code(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(er, "urlopen", FakePostgrest(insert_error=URLError("down")))
    monkeypatch.setenv("SUPABASE_URL", URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    path = tmp_path / "r.json"
    path.write_text(json.dumps(base()))
    assert er.main(["--receipt-json", str(path)]) == 3
    assert json.loads(capsys.readouterr().out)["reason"] == "RECEIPT_PERSISTENCE_UNAVAILABLE"
