"""No-network durability and read-only resource inspection tests."""
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

import pytest

from v17.gemini_async_journal import GeminiJournal, JournalError
from v17.gemini_readonly_inspection import inspect_batch, inspect_cache
from v17.gemini_async_contracts import GeminiContractError

DIGEST = "a" * 64


def fixture(tmp_path):
    return GeminiJournal(tmp_path / "standby-receipts.sqlite")


def reserve(journal):
    return journal.reserve(kind="BATCH_INLINE", manifest_sha256=DIGEST,
                           budget_reservation_ref="budget:reservation-1",
                           approval_ref="review:approval-1")


def test_reservation_is_exactly_once_and_survives_reopening(tmp_path):
    j = fixture(tmp_path)
    reserved = reserve(j)
    assert reserved["stage"] == "RESERVED"
    reopened = fixture(tmp_path)
    assert reopened.history(kind="BATCH_INLINE", manifest_sha256=DIGEST)[0]["stage"] == "RESERVED"
    with pytest.raises(JournalError, match="GEMINI_OPERATION_ALREADY_RESERVED"):
        reserve(reopened)


def test_ledger_is_append_only_no_raw_prompts_or_credentials(tmp_path):
    j = fixture(tmp_path)
    reserve(j)
    j.record(kind="BATCH_INLINE", manifest_sha256=DIGEST,
             next_stage="PROVIDER_CONFIRMED", provider_name="batches/123")
    j.record(kind="BATCH_INLINE", manifest_sha256=DIGEST, next_stage="RECONCILED")
    assert [r["stage"] for r in j.history(kind="BATCH_INLINE", manifest_sha256=DIGEST)] == [
        "RESERVED", "PROVIDER_CONFIRMED", "RECONCILED"
    ]
    with sqlite3.connect(j.path) as conn:
        cols=[x[1] for x in conn.execute("PRAGMA table_info(gemini_lifecycle_events)").fetchall()]
        assert "prompt" not in cols and "api_key" not in cols
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM gemini_lifecycle_events")


def test_timeout_unknown_outcome_cannot_retry_automatically(tmp_path):
    j=fixture(tmp_path)
    reserve(j)
    j.record(kind="BATCH_INLINE", manifest_sha256=DIGEST,next_stage="OUTCOME_UNKNOWN")
    with pytest.raises(JournalError, match="GEMINI_OPERATION_ALREADY_RESERVED"):
        reserve(j)
    j.record(kind="BATCH_INLINE", manifest_sha256=DIGEST,
             next_stage="PROVIDER_CONFIRMED", provider_name="batches/recovered")


def test_illegal_transitions_fail_closed(tmp_path):
    j=fixture(tmp_path)
    with pytest.raises(JournalError,match="GEMINI_JOURNAL_RESERVATION_MISSING"):
        j.record(kind="BATCH_INLINE",manifest_sha256=DIGEST,next_stage="RECONCILED")
    reserve(j)
    with pytest.raises(JournalError,match="GEMINI_JOURNAL_TRANSITION_REJECTED"):
        j.record(kind="BATCH_INLINE",manifest_sha256=DIGEST,next_stage="RECONCILED")
    with pytest.raises(JournalError,match="GEMINI_JOURNAL_PROVIDER_ID_INVALID"):
        j.record(kind="BATCH_INLINE",manifest_sha256=DIGEST,
                 next_stage="PROVIDER_CONFIRMED",provider_name="cachedContents/abc")


def test_cache_resource_name_not_confused_with_batch(tmp_path):
    j=fixture(tmp_path)
    j.reserve(kind="CACHE_CREATE",manifest_sha256=DIGEST,
              budget_reservation_ref="budget:1",approval_ref="approval:1")
    with pytest.raises(JournalError,match="GEMINI_JOURNAL_PROVIDER_ID_INVALID"):
        j.record(kind="CACHE_CREATE",manifest_sha256=DIGEST,
                 next_stage="PROVIDER_CONFIRMED",provider_name="batches/abc")
    j.record(kind="CACHE_CREATE",manifest_sha256=DIGEST,
             next_stage="PROVIDER_CONFIRMED",provider_name="cachedContents/abc")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False


def env(**more):
    e={"WOW_GEMINI_ENABLED":"true","WOW_GEMINI_PAID_API_PROJECT_VERIFIED":"true",
       "WOW_GEMINI_READONLY_INSPECTION_ENABLED":"true","GEMINI_API_KEY":"test-only"}
    e.update(more)
    return e


def test_get_disabled_by_default_no_network():
    called=[]
    with pytest.raises(GeminiContractError,match="GEMINI_DISABLED"):
        inspect_batch(batch_name="batches/abc",expected_keys=["x"],env={},
                      opener=lambda *a,**k: called.append(True))
    assert called==[]


def test_paid_billing_and_readonly_scope_required():
    for changes,code in [({"WOW_GEMINI_PAID_API_PROJECT_VERIFIED":""},"GEMINI_BILLING_UNVERIFIED"),
                         ({"WOW_GEMINI_READONLY_INSPECTION_ENABLED":""},"GEMINI_INSPECTION_DISABLED")]:
        called=[]
        with pytest.raises(GeminiContractError,match=code):
            inspect_batch(batch_name="batches/abc",expected_keys=["x"],
                env=env(**changes), opener=lambda *a,**k: called.append(True))
        assert called == []


def test_batch_readonly_pending_inspection_one_get():
    called=[]
    def opener(req,timeout):
        called.append((req,timeout))
        return FakeResponse(json.dumps({"name":"batches/abc",
            "metadata":{"state":"JOB_STATE_RUNNING"}}).encode())
    obj=inspect_batch(batch_name="batches/abc",expected_keys=["x"],env=env(),opener=opener)
    assert obj["status"]=="PENDING"
    assert len(called)==1 and called[0][0].get_method()=="GET"
    assert called[0][1]==10
    assert called[0][0].full_url.endswith("/v1beta/batches/abc")


def test_readonly_cache_expiration_no_content_exposure():
    def opener(*a,**k):
        return FakeResponse(json.dumps({"name":"cachedContents/abc",
            "model":"models/gemini-model-1","expireTime":"2026-12-01T00:00:00Z",
            "contents":[{"parts":[{"text":"Do not reveal this content"}]}]}).encode())
    obj=inspect_cache(cache_name="cachedContents/abc",expected_model="gemini-model-1",
        evaluated_at=datetime(2026,10,10,tzinfo=timezone.utc),env=env(),opener=opener)
    assert obj["status"]=="CACHE_AVAILABLE"
    assert "Do not reveal this content" not in str(obj)


@pytest.mark.parametrize("name",["../secrets","batches/../../a","batches/a?key=123","https://evil.invalid/a"])
def test_resource_name_not_a_url_or_path_traversal(name):
    called=[]
    with pytest.raises(GeminiContractError,match="GEMINI_RESOURCE_NAME_INVALID"):
        inspect_batch(batch_name=name,expected_keys=["x"],env=env(),
                      opener=lambda *a,**k: called.append(True))
    assert called==[]


def test_readonly_429_fails_typed():
    def opener(*args,**kwargs):
        raise HTTPError("https://example.invalid",429,"rate",{},None)
    with pytest.raises(GeminiContractError,match="GEMINI_RATE_LIMITED"):
        inspect_batch(batch_name="batches/abc",expected_keys=["x"],env=env(),opener=opener)


def test_mismatched_provider_resource_fails():
    def opener(*a,**k):
        return FakeResponse(b'{"name":"batches/other","metadata":{"state":"JOB_STATE_RUNNING"}}')
    with pytest.raises(GeminiContractError,match="GEMINI_RESOURCE_IDENTITY_MISMATCH"):
        inspect_batch(batch_name="batches/abc",expected_keys=["x"],env=env(),opener=opener)


def test_provider_journal_rejects_traversal_like_id(tmp_path):
    j = fixture(tmp_path)
    reserve(j)
    with pytest.raises(JournalError,match="GEMINI_JOURNAL_PROVIDER_ID_INVALID"):
        j.record(kind="BATCH_INLINE",manifest_sha256=DIGEST,
                 next_stage="PROVIDER_CONFIRMED", provider_name="batches/../invalid")
