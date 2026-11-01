"""Opt-in, OIDC-bound Scout persistence V2 with exact-once chunk receipts.

Never replay a legacy/unchecked batch after a 502. CAPABILITIES must attest the
server's durable checkpoint protocol before any BEGIN/APPEND mutation. Retry only
under that contract; every replay is verified against server-side chunk identity.
"""
from __future__ import annotations
import json
import os
import time
from pathlib import Path

from v17 import scout_brain_edge_sync as old

PROTOCOL = 2
RETRY_CODES = {
    "SCOUT_EDGE_PERSIST_HTTP_502",
    "SCOUT_EDGE_PERSIST_HTTP_503",
    "SCOUT_EDGE_PERSIST_HTTP_504",
    "SCOUT_EDGE_PERSIST_TRANSPORT_FAILED",
    "SCOUT_EDGE_PERSIST_RESPONSE_INVALID",
}


def _checked_post(url: str, payload: dict, *, retryable: bool = False) -> dict:
    attempts = 3 if retryable else 1
    for attempt in range(attempts):
        try:
            receipt = old._post(url, payload)
        except RuntimeError as exc:
            code = str(exc)
            if not retryable or code not in RETRY_CODES:
                raise
            if attempt == attempts - 1:
                raise RuntimeError("SCOUT_PERSIST_RETRY_EXHAUSTED:" + code) from exc
            time.sleep(2 ** attempt)
            continue
        if receipt.get("protocol_version") != PROTOCOL or receipt.get("can_execute") is not False:
            raise RuntimeError("SCOUT_PERSIST_V2_ACK_INVALID")
        return receipt
    raise AssertionError("bounded retry logic exhausted without a terminal result")


def sync_v2(input_path: str, receipt_path: str) -> dict:
    handoff = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if handoff.get("can_execute") is not False:
        raise RuntimeError("SCOUT_HANDOFF_GOVERNANCE_INVALID")
    url = old.persist_url()
    header = old.handoff_header(handoff)
    rows = old.candidate_rows(handoff)
    expected_evidence = sum(len(old.evidence_list(row)) for row in rows)
    meta = {**header, "protocol_version": PROTOCOL,
            "expected_candidates": len(rows), "expected_evidence": expected_evidence}
    # Preflight is essential to rolling out Edge before the GitHub caller:
    # a legacy Edge denies CAPABILITIES without touching research_runs.
    capability = _checked_post(url, {**header, "persist_phase": "CAPABILITIES"})
    if capability.get("schema_version") != 2:
        raise RuntimeError("SCOUT_PERSIST_SCHEMA_ATTESTATION_MISSING")
    begin = _checked_post(url, {**meta, "persist_phase": "BEGIN"}, retryable=True)
    if begin.get("persist_phase") != "BEGIN":
        raise RuntimeError("SCOUT_PERSIST_BEGIN_ACK_INVALID")
    previous = begin.get("next_chunk_index")
    if type(previous) is not int or previous < 0:
        raise RuntimeError("SCOUT_PERSIST_BEGIN_CURSOR_INVALID")

    bound = old._int_env("WOW_SCOUT_PERSIST_REQUEST_BYTES", old.DEFAULT_REQUEST_BYTES)
    content_bytes = max(old.MIN_CONTENT_BYTES, bound - old.envelope_bytes(meta))
    finalized = uploaded_evidence = request_count = 0
    for index, batch in enumerate(old.requests_for(
        rows, max_evidence_rows=old._int_env("WOW_SCOUT_PERSIST_EVIDENCE_ROWS", old.DEFAULT_EVIDENCE_ROWS),
        max_bytes=content_bytes,
    )):
        expected_final = sum(1 for e in batch if (e.get("evidence_slice") or {}).get("final") is True)
        expected_rows = sum(len(old.evidence_list(e)) for e in batch)
        ack = _checked_post(url, {
            **meta, "persist_phase": "APPEND", "chunk_index": index, "candidates": batch,
        }, retryable=True)
        if (ack.get("persist_phase") != "APPEND" or ack.get("chunk_index") != index
                or ack.get("batch_entry_count") != len(batch)
                or ack.get("batch_finalized_candidate_count") != expected_final
                or ack.get("batch_evidence_row_count") != expected_rows):
            raise RuntimeError("SCOUT_PERSIST_CHUNK_ACK_RECONCILIATION_FAILED")
        request_count += 1
        finalized += expected_final
        uploaded_evidence += expected_rows
        print(f"v2 chunk={index} entries={len(batch)} evidence_rows={expected_rows} "
              f"finalized={finalized}/{len(rows)}", flush=True)

    if previous > request_count:
        raise RuntimeError("SCOUT_PERSIST_SERVER_CURSOR_AHEAD_OF_SOURCE")
    if finalized != len(rows):
        raise RuntimeError("SCOUT_EDGE_PERSIST_CANDIDATE_RECONCILIATION_FAILED")
    if uploaded_evidence != expected_evidence:
        raise RuntimeError("SCOUT_EDGE_PERSIST_EVIDENCE_RECONCILIATION_FAILED")
    final = _checked_post(url, {**meta, "persist_phase": "FINALIZE"}, retryable=True)
    if (final.get("persist_phase") != "FINALIZE"
            or final.get("candidate_count") != finalized
            or final.get("observation_count") != uploaded_evidence):
        raise RuntimeError("SCOUT_EDGE_PERSIST_RECEIPT_RECONCILIATION_FAILED")
    receipt = {key: value for key, value in final.items() if key != "ok"}
    receipt["uploaded_candidate_rows"] = finalized
    receipt["uploaded_evidence_rows"] = uploaded_evidence
    receipt["upload_requests"] = request_count
    Path(receipt_path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
