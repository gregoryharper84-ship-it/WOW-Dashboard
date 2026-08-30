"""Idempotency key derivation (packet section 15).

    run key        = caller idempotency key + canonical request hash
    job key         = run_id + candidate_id + worker_id + worker_version + input_hash
    prediction key  = candidate_id + evidence_snapshot_id + artifact_id +
                       calibrator_id + exact line + side

All three are stable sha256 hex digests over a canonical (sorted-key, no
whitespace) JSON encoding of their inputs, so the same logical request always
produces the same key regardless of dict ordering or float/str formatting
drift between callers.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_request_hash(payload: dict[str, Any]) -> str:
    """Canonical hash of a run-create request body, excluding fields that are
    response-only or caller-identity metadata rather than part of the request
    the run represents (mirrors the server-owned-fields rule already enforced
    on /wow/daily/run in the Flask app)."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def run_key(idempotency_key: str, request_hash: str) -> str:
    if not idempotency_key or not isinstance(idempotency_key, str):
        raise ValueError("idempotency_key must be a non-empty string")
    return hashlib.sha256(
        _canonical_json({"idempotency_key": idempotency_key, "request_hash": request_hash}).encode("utf-8")
    ).hexdigest()


def job_key(
    *,
    run_id: str,
    candidate_id: Optional[str],
    worker_id: str,
    worker_version: str,
    input_hash: str,
) -> str:
    return hashlib.sha256(
        _canonical_json({
            "run_id": run_id,
            "candidate_id": candidate_id,
            "worker_id": worker_id,
            "worker_version": worker_version,
            "input_hash": input_hash,
        }).encode("utf-8")
    ).hexdigest()


def prediction_key(
    *,
    candidate_id: str,
    evidence_snapshot_id: str,
    artifact_id: str,
    calibrator_id: Optional[str],
    exact_line: float,
    side: str,
) -> str:
    return hashlib.sha256(
        _canonical_json({
            "candidate_id": candidate_id,
            "evidence_snapshot_id": evidence_snapshot_id,
            "artifact_id": artifact_id,
            "calibrator_id": calibrator_id,
            "exact_line": exact_line,
            "side": side,
        }).encode("utf-8")
    ).hexdigest()


def input_hash(payload: dict[str, Any]) -> str:
    """Hash of a job's input envelope, used as job_key's input_hash component
    and stored on wow_agent_jobs.input_hash for auditability."""
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
