"""Append-only persistence for machine-verifiable engineering receipts."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from fastapi import FastAPI, HTTPException

from github_actions_oidc import release_receipt_route_auth_dependency
from v17.receipt_schema import VerificationReceipt, validate_terminal_receipt

TABLE = "wow_v17_reliability_receipts"
CAN_EXECUTE = False
MAX_EVIDENCE_BYTES = 1_000_000


def _decode_evidence(payload: dict[str, Any], field: str) -> bytes:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_RECEIPT_EVIDENCE", "field": field, "can_execute": False},
        )
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_RECEIPT_EVIDENCE_BASE64", "field": field, "can_execute": False},
        ) from exc
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "RECEIPT_EVIDENCE_TOO_LARGE", "field": field, "can_execute": False},
        )
    return raw


def _header_value(raw_headers: bytes, name: str) -> str:
    target = name.lower()
    for line in raw_headers.decode("utf-8", errors="replace").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key.strip().lower() == target:
            return value.strip().lower()
    return ""


def _persist(db: Any, payload: dict[str, Any], *, auth_claims: dict[str, Any]) -> dict[str, Any]:
    receipt_payload = payload.get("receipt")
    if not isinstance(receipt_payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_RECEIPT_SCHEMA", "error_type": "RECEIPT_OBJECT_REQUIRED", "can_execute": False},
        )
    try:
        receipt = VerificationReceipt.model_validate(receipt_payload)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_RECEIPT_SCHEMA", "error_type": type(exc).__name__, "can_execute": False},
        ) from exc

    errors = validate_terminal_receipt(
        receipt,
        expected_issue_id=receipt.issue_id,
        expected_pr_number=receipt.pr_number,
        expected_pr_head_sha=receipt.exact_head_sha,
        expected_merge_sha=receipt.merge_sha,
    )
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_RECEIPT_SCHEMA", "errors": errors, "can_execute": False},
        )

    auth_errors: list[str] = []
    if receipt.sentinel_workflow != "wow-v17-release-production-verification-agent":
        auth_errors.append("SENTINEL_WORKFLOW_IDENTITY_MISMATCH")
    claim_run_id = str(auth_claims.get("run_id") or "").strip()
    if not claim_run_id.isdigit() or int(claim_run_id) != receipt.sentinel_workflow_run_id:
        auth_errors.append("SENTINEL_WORKFLOW_RUN_ID_MISMATCH")
    if auth_errors:
        raise HTTPException(
            status_code=401,
            detail={"code": "RECEIPT_OIDC_BINDING_MISMATCH", "errors": auth_errors, "can_execute": False},
        )

    response_bytes = _decode_evidence(payload, "raw_response_body_base64")
    header_bytes = _decode_evidence(payload, "raw_response_headers_base64")
    trace_bytes = _decode_evidence(payload, "execution_trace_base64")
    evidence_errors: list[str] = []
    if hashlib.sha256(response_bytes).hexdigest() != receipt.raw_response_digest:
        evidence_errors.append("RAW_RESPONSE_DIGEST_MISMATCH")
    if hashlib.sha256(trace_bytes).hexdigest() != receipt.execution_trace_digest:
        evidence_errors.append("EXECUTION_TRACE_DIGEST_MISMATCH")
    try:
        trace = json.loads(trace_bytes.decode("utf-8"))
    except Exception:
        trace = None
        evidence_errors.append("EXECUTION_TRACE_JSON_INVALID")
    if isinstance(trace, dict):
        trace_bindings = {
            "contract_version": receipt.contract_version,
            "issue_id": receipt.issue_id,
            "pr_number": receipt.pr_number,
            "exact_head_sha": receipt.exact_head_sha,
            "merge_sha": receipt.merge_sha,
            "deployed_render_sha": receipt.deployed_render_sha,
            "method": receipt.method,
            "route_tested": receipt.route_tested,
            "workflow": receipt.sentinel_workflow,
            "workflow_run_id": receipt.sentinel_workflow_run_id,
            "can_execute": False,
        }
        for field, expected in trace_bindings.items():
            if trace.get(field) != expected:
                evidence_errors.append(f"TRACE_BINDING_MISMATCH:{field}")
        environment_signature = str(trace.get("runtime_environment_signature") or "")
        if len(environment_signature) != 64 or any(ch not in "0123456789abcdef" for ch in environment_signature):
            evidence_errors.append("TRACE_ENVIRONMENT_SIGNATURE_INVALID")
        executed_utc = str(trace.get("executed_utc") or "")
        if not executed_utc or ("+" not in executed_utc and not executed_utc.endswith("Z")):
            evidence_errors.append("TRACE_EXECUTED_UTC_INVALID")
    if _header_value(header_bytes, "x-wow-dry-run-only") != "true":
        evidence_errors.append("RAW_DRY_RUN_HEADER_INVALID")
    if _header_value(header_bytes, "x-wow-can-execute") != "false":
        evidence_errors.append("RAW_CAN_EXECUTE_HEADER_INVALID")
    if evidence_errors:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_RECEIPT_EVIDENCE", "errors": evidence_errors, "can_execute": False},
        )

    signature = receipt.sentinel_signature
    existing = (
        db.table(TABLE)
        .select("sentinel_signature")
        .eq("sentinel_signature", signature)
        .limit(1)
        .execute()
    )
    if getattr(existing, "data", None):
        return {
            "status": "ALREADY_PERSISTED",
            "sentinel_signature": signature,
            "can_execute": False,
        }

    row = {
        "sentinel_signature": signature,
        "contract_version": receipt.contract_version,
        "issue_id": receipt.issue_id,
        "pr_number": receipt.pr_number,
        "exact_head_sha": receipt.exact_head_sha,
        "merge_sha": receipt.merge_sha,
        "deployed_render_sha": receipt.deployed_render_sha,
        "method": receipt.method,
        "route_tested": receipt.route_tested,
        "schema_hash": receipt.schema_hash,
        "http_status": receipt.http_status,
        "dry_run_header_present": receipt.dry_run_header_present,
        "can_execute_header_false": receipt.can_execute_header_false,
        "raw_response_digest": receipt.raw_response_digest,
        "execution_trace_digest": receipt.execution_trace_digest,
        "sentinel_workflow": receipt.sentinel_workflow,
        "sentinel_workflow_run_id": receipt.sentinel_workflow_run_id,
        "audit_artifact_name": receipt.audit_artifact_name,
        "verified_at": receipt.timestamp_utc.isoformat(),
        "receipt_json": receipt.model_dump(mode="json"),
        "raw_response_body_base64": payload["raw_response_body_base64"],
        "raw_response_headers_base64": payload["raw_response_headers_base64"],
        "execution_trace_base64": payload["execution_trace_base64"],
        "can_execute": False,
    }
    db.table(TABLE).insert(row).execute()
    return {
        "status": "PERSISTED",
        "sentinel_signature": signature,
        "can_execute": False,
    }


def install_reliability_receipt_routes(
    app: FastAPI,
    *,
    db_client_fn: Any,
) -> None:
    routes = {getattr(route, "path", None) for route in app.router.routes}
    if "/internal/v17/reliability-receipts" in routes:
        return

    @app.post(
        "/internal/v17/reliability-receipts",
        operation_id="persistWowV17ReliabilityReceipt",
    )
    def persist_reliability_receipt(
        payload: dict[str, Any],
        auth_claims: dict[str, Any] = release_receipt_route_auth_dependency(),
    ) -> dict[str, Any]:
        return _persist(db_client_fn(), payload, auth_claims=auth_claims)


__all__ = ["CAN_EXECUTE", "MAX_EVIDENCE_BYTES", "TABLE", "install_reliability_receipt_routes"]
