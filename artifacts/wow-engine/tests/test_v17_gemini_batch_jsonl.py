"""Exact-key JSONL reconciliation and protected read-only download tests."""
import io
import json
import pytest
from v17.gemini_batch_jsonl import (
    reconcile_jsonl_bytes,download_completed_jsonl,
)
from v17.gemini_async_contracts import GeminiContractError


def row(key):
    return {"key":key,"response":{"candidates":[
        {"finishReason":"STOP","content":{"parts":[{"text":json.dumps({
            "summary":"fixture","findings":["evidence only"],"evidence_refs":["source:fixture"]
        })}]}}]}}


def encode(*rows):
    return ("\n".join(json.dumps(r) for r in rows)+"\n").encode()


def fixture_job():
    return {"name":"batches/one","metadata":{"state":"JOB_STATE_SUCCEEDED"},
            "response":{"responsesFile":"files/result1"}}


def config(**kw):
    val={"WOW_GEMINI_ENABLED":"true",
         "WOW_GEMINI_PAID_API_PROJECT_VERIFIED":"true",
         "WOW_GEMINI_READONLY_INSPECTION_ENABLED":"true","GEMINI_API_KEY":"test"}
    val.update(kw)
    return val


class Resp(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*a):return False


def test_out_of_order_jsonl_maps_by_identity():
    result=reconcile_jsonl_bytes(encode(row("b"),row("a")),
        batch_name="batches/one",expected_keys=["a","b"])
    assert result["status"]=="RECONCILED_UNVERIFIED"
    assert [x["key"] for x in result["rows"]]==["a","b"]


@pytest.mark.parametrize("raw,code",[
    (b"invalid json\n","GEMINI_BATCH_JSONL_INVALID"),
    (b"","GEMINI_BATCH_JSONL_INVALID"),
    (encode(row("a"),row("a")),"GEMINI_BATCH_RESULT_IDENTITY_DUPLICATED"),
    (encode(row("unknown")),"GEMINI_BATCH_RESULT_SET_MISMATCH"),
    (encode({"key":"a","error":{"message":"quota"}}),"GEMINI_BATCH_ROW_FAILED"),
])
def test_invalid_jsonl_blocks_all_rows(raw,code):
    with pytest.raises(GeminiContractError,match=code):
        reconcile_jsonl_bytes(raw,batch_name="batches/one",expected_keys=["a"])


def test_verified_download_is_single_get_and_no_retries():
    seen=[]
    def fake(req,timeout):
        seen.append((req.full_url,req.get_method(),timeout))
        return Resp(encode(row("a")))
    out=download_completed_jsonl(batch_response=fixture_job(),
         expected_batch_name="batches/one",expected_keys=["a"],
         env=config(),opener=fake)
    assert out["status"]=="RECONCILED_UNVERIFIED"
    assert len(seen)==1 and seen[0][1]=="GET"
    assert seen[0][0].endswith("/files/result1:download?alt=media")


def test_path_injection_never_fetches():
    job=fixture_job()
    job["response"]["responsesFile"]="files/../secret"
    seen=[]
    with pytest.raises(GeminiContractError,match="GEMINI_BATCH_RESULTS_UNAVAILABLE"):
        download_completed_jsonl(batch_response=job,
            expected_batch_name="batches/one",expected_keys=["a"],
            env=config(),opener=lambda *a,**k:seen.append(True))
    assert seen==[]


def test_disabled_feature_prevents_any_download():
    called=[]
    with pytest.raises(GeminiContractError,match="GEMINI_DISABLED"):
        download_completed_jsonl(batch_response=fixture_job(),
           expected_batch_name="batches/one",expected_keys=["a"],
           env={},opener=lambda *a,**k:called.append(True))
    assert not called
