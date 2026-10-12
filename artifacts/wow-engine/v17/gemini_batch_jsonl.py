"""Bounded read-only Gemini Batch JSONL results, exact manifest identity.

Never infers identity from line order, writes artifacts, reissues a Batch
submission, or treats unverified research as a governed probability.
"""
from __future__ import annotations
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from v17.gemini_async_contracts import (
    BATCH_NAME_RE, GeminiContractError, reconcile_inline_batch,
)

CAN_EXECUTE=False
FILE_NAME_RE=re.compile(r"^files/[A-Za-z0-9_-]{1,128}$")
BASE="https://generativelanguage.googleapis.com/download/v1beta/"
MAX_JSONL_BYTES=2_000_000
MAX_JSONL_ROWS=16


def reconcile_jsonl_bytes(data:bytes, *, batch_name:str, expected_keys:list[str])->dict:
    if not isinstance(data,bytes) or not data or len(data)>MAX_JSONL_BYTES:
        raise GeminiContractError("GEMINI_BATCH_JSONL_INVALID")
    try:
        lines=data.decode("utf-8").splitlines()
        if not lines or len(lines)>MAX_JSONL_ROWS:
            raise ValueError("rows")
        rows=[json.loads(line) for line in lines]
    except (UnicodeDecodeError,ValueError,TypeError):
        raise GeminiContractError("GEMINI_BATCH_JSONL_INVALID") from None
    return reconcile_inline_batch({
        "name":batch_name, "metadata":{"state":"JOB_STATE_SUCCEEDED"},
        "response":{"inlinedResponses":rows},
    },expected_batch_name=batch_name,expected_keys=expected_keys)


def download_completed_jsonl(*, batch_response:dict,
    expected_batch_name:str,expected_keys:list[str],env=None,opener=None)->dict:
    e=os.environ if env is None else env
    if e.get("WOW_GEMINI_ENABLED")!="true":
        raise GeminiContractError("GEMINI_DISABLED")
    if e.get("WOW_GEMINI_PAID_API_PROJECT_VERIFIED")!="true":
        raise GeminiContractError("GEMINI_BILLING_UNVERIFIED")
    if e.get("WOW_GEMINI_READONLY_INSPECTION_ENABLED")!="true":
        raise GeminiContractError("GEMINI_INSPECTION_DISABLED")
    if (not isinstance(expected_batch_name,str) or
        not BATCH_NAME_RE.fullmatch(expected_batch_name) or
        not isinstance(batch_response,dict) or
        batch_response.get("name")!=expected_batch_name):
        raise GeminiContractError("GEMINI_BATCH_IDENTITY_INVALID")
    state=batch_response.get("state")
    meta=batch_response.get("metadata")
    if isinstance(meta,dict):
        state=meta.get("state") or state
    if state!="JOB_STATE_SUCCEEDED":
        raise GeminiContractError("GEMINI_BATCH_RESULTS_UNAVAILABLE")
    result=batch_response.get("response") or batch_response.get("dest") or {}
    filename=result.get("responsesFile") if isinstance(result,dict) else None
    if not isinstance(filename,str) or not FILE_NAME_RE.fullmatch(filename):
        raise GeminiContractError("GEMINI_BATCH_RESULTS_UNAVAILABLE")
    key=e.get("GEMINI_API_KEY")
    if not key:
        raise GeminiContractError("GEMINI_CONFIG_MISSING")
    req=Request(BASE+filename+":download?alt=media",method="GET",
        headers={"x-goog-api-key":key,"Accept":"application/x-ndjson"})
    transport=urlopen if opener is None else opener
    try:
        with transport(req,timeout=15) as response:
            raw=response.read(MAX_JSONL_BYTES+1)
    except HTTPError as exc:
        if exc.code==429:
            raise GeminiContractError("GEMINI_RATE_LIMITED") from None
        raise GeminiContractError("GEMINI_TRANSPORT_FAILED") from None
    except (URLError,TimeoutError,OSError):
        raise GeminiContractError("GEMINI_TRANSPORT_FAILED") from None
    return reconcile_jsonl_bytes(raw,batch_name=expected_batch_name,expected_keys=expected_keys)
