"""Gemini asynchronous Batch/cache identity and privacy contracts. No network."""
from datetime import datetime, timezone

import pytest

from v17.gemini_async_contracts import (
    GeminiContractError, prepare_inline_batch, prepare_explicit_cache,
    reconcile_inline_batch, reconcile_cache_resource,
)


def item(key):
    return {"key": key, "task": "research_challenger", "prompt": "Public RFC / PR review",
            "evidence_refs": ["https://example.invalid/public/spec"]}


def plan(items=None, urgency="BACKGROUND"):
    return prepare_inline_batch(model="gemini-test-model", items=items or [item("row_a")],
                                urgency=urgency, data_classification="PUBLIC")


def result_json(text='{"summary":"verified by reviewer?","findings":["example"],"evidence_refs":["source"]}'):
    return {"candidates": [{"finishReason": "STOP",
                            "content": {"parts": [{"text": text}]}}]}


def job(rows, state="JOB_STATE_SUCCEEDED"):
    return {"name": "batches/abc123", "metadata": {"state": state},
            "response": {"inlinedResponses": rows}}


def response(key):
    return {"metadata": {"key": key}, "response": result_json()}


def test_offline_batch_builds_documented_rest_shape_stable_hash():
    one = plan([item("row_b"), item("row_a")])
    two = plan([item("row_b"), item("row_a")])
    assert one["manifest_sha256"] == two["manifest_sha256"]
    assert one["status"] == "PREPARED_NOT_SUBMITTED"
    assert one["can_execute"] is False
    requests = one["api_payload"]["batch"]["input_config"]["requests"]["requests"]
    assert [x["metadata"]["key"] for x in requests] == ["row_b", "row_a"]
    assert one["api_route"] == "/v1beta/models/gemini-test-model:batchGenerateContent"
    assert all(x["request"]["generationConfig"]["responseMimeType"] == "application/json"
               for x in requests)


def test_batch_hash_changes_when_source_reference_changes():
    first = plan()
    modified = item("row_a")
    modified["evidence_refs"] = ["file:another"]
    second = plan([modified])
    assert first["manifest_sha256"] != second["manifest_sha256"]


def test_interactive_work_not_batch_scheduled():
    with pytest.raises(GeminiContractError, match="GEMINI_BATCH_LATENCY_INCOMPATIBLE"):
        plan(urgency="INTERACTIVE")


@pytest.mark.parametrize("items,code", [
    ([item("x"), item("x")], "GEMINI_BATCH_IDENTITY_INVALID"),
    ([item("../bad")], "GEMINI_BATCH_IDENTITY_INVALID"),
    ([{**item("x"), "task": "sports_probability"}], "GEMINI_BATCH_INPUT_INVALID"),
    ([{**item("x"), "evidence_refs": []}], "GEMINI_BATCH_INPUT_INVALID"),
    ([{**item("x"), "prompt": "x" * 12001}], "GEMINI_BATCH_INPUT_INVALID"),
])
def test_invalid_batch_fails_closed(items,code):
    with pytest.raises(GeminiContractError, match=code):
        plan(items)


def test_response_reconciliation_uses_keys_not_order():
    normalized = reconcile_inline_batch(job([response("row_b"), response("row_a")]),
                                        expected_batch_name="batches/abc123",
                                        expected_keys=["row_a", "row_b"])
    assert [x["key"] for x in normalized["rows"]] == ["row_a", "row_b"]
    assert normalized["status"] == "RECONCILED_UNVERIFIED"
    assert all(x["verification_state"] == "UNVERIFIED_CHALLENGER_OUTPUT"
               for x in normalized["rows"])


@pytest.mark.parametrize("rows,code", [
    ([{"response": result_json()}], "GEMINI_BATCH_RESULT_IDENTITY_MISSING"),
    ([response("row_a"), response("row_a")], "GEMINI_BATCH_RESULT_IDENTITY_DUPLICATED"),
    ([response("not_expected")], "GEMINI_BATCH_RESULT_SET_MISMATCH"),
    ([{"metadata": {"key": "row_a"}, "error": {"code": 500}}], "GEMINI_BATCH_ROW_FAILED"),
    ([{"metadata": {"key": "row_a"}, "response": result_json("{}")}], "GEMINI_BATCH_RESPONSE_INVALID"),
])
def test_bad_batch_result_no_row_published(rows,code):
    with pytest.raises(GeminiContractError, match=code):
        reconcile_inline_batch(job(rows),expected_batch_name="batches/abc123",
                               expected_keys=["row_a"])


@pytest.mark.parametrize("state,expected", [
    ("JOB_STATE_PENDING", "PENDING"),
    ("JOB_STATE_RUNNING", "PENDING"),
    ("JOB_STATE_FAILED", "TERMINAL_HOLD"),
    ("JOB_STATE_CANCELLED", "TERMINAL_HOLD"),
    ("JOB_STATE_EXPIRED", "TERMINAL_HOLD"),
])
def test_pending_and_terminal_failure_never_publish(state,expected):
    out=reconcile_inline_batch(job([],state),expected_batch_name="batches/abc123",
                               expected_keys=["row_a"])
    assert out["rows"]==[] and out["status"]==expected


def test_result_file_is_distinct_unimplemented_contract():
    j={"name":"batches/abc123","metadata":{"state":"JOB_STATE_SUCCEEDED"},
       "response":{"responsesFile":"files/abc"}}
    with pytest.raises(GeminiContractError,match="GEMINI_BATCH_RESULTS_UNAVAILABLE"):
        reconcile_inline_batch(j,expected_batch_name="batches/abc123",
                               expected_keys=["row_a"])


def test_never_infer_provider_job_or_cache_name():
    with pytest.raises(GeminiContractError,match="GEMINI_BATCH_IDENTITY_INVALID"):
        reconcile_inline_batch(job([response("row_a")]),expected_batch_name="batches/other",
                               expected_keys=["row_a"])
    with pytest.raises(GeminiContractError,match="GEMINI_BATCH_IDENTITY_INVALID"):
        reconcile_inline_batch(job([response("row_a")]),expected_batch_name="../escape",
                               expected_keys=["row_a"])


def test_prepare_cache_does_not_create_or_send():
    out=prepare_explicit_cache(model="gemini-test-model",
        content="Approved public document content",ttl_seconds=900,
        source_refs=["sha256:aabbcc"],data_classification="PUBLIC")
    assert out["status"] == "PREPARED_NOT_CREATED"
    assert out["api_payload"]["ttl"]=="900s"
    assert out["api_payload"]["model"]=="models/gemini-test-model"
    assert out["content_sha256"]
    assert out["can_execute"] is False


@pytest.mark.parametrize("ttl",[-1,0,299,3601,True,1.5])
def test_cache_ttl_bounded_and_type_safe(ttl):
    with pytest.raises(GeminiContractError,match="GEMINI_CACHE_INPUT_INVALID"):
        prepare_explicit_cache(model="gemini-test-model", content="public",
              ttl_seconds=ttl,source_refs=["file:public"],data_classification="PUBLIC")


def test_cache_rejects_private_material():
    with pytest.raises(GeminiContractError,match="GEMINI_INPUT_CLASS_BLOCKED"):
        prepare_explicit_cache(model="gemini-test-model",content="private",ttl_seconds=300,
              data_classification="PRIVATE",source_refs=["private:rfc"])


def test_cache_resource_freshness_and_identity():
    now=datetime(2026,10,10,tzinfo=timezone.utc)
    payload={"name":"cachedContents/abc123","model":"models/gemini-test-model",
             "expireTime":"2026-10-10T01:00:00Z"}
    good=reconcile_cache_resource(payload,expected_name="cachedContents/abc123",
           expected_model="gemini-test-model",evaluated_at=now)
    assert good["status"]=="CACHE_AVAILABLE"
    assert "Approved public document" not in str(good)
    stale=reconcile_cache_resource(payload,expected_name="cachedContents/abc123",
           expected_model="gemini-test-model",
           evaluated_at=datetime(2026,10,11,tzinfo=timezone.utc))
    assert stale["status"]=="CACHE_EXPIRED"
    with pytest.raises(GeminiContractError,match="GEMINI_CACHE_IDENTITY_INVALID"):
        reconcile_cache_resource(payload,expected_name="cachedContents/not-same",
           expected_model="gemini-test-model",evaluated_at=now)
