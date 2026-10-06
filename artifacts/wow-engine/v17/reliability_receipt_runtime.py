"""Append-only persistence for machine-verifiable engineering receipts."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from github_actions_oidc import scout_route_auth_dependency
from v17.receipt_schema import VerificationReceipt, validate_terminal_receipt

TABLE = "wow_v17_reliability_receipts"
CAN_EXECUTE = False


def _persist(db: Any, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        receipt = VerificationReceipt.model_validate(payload)
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
    auth_dependency: Any,
    db_client_fn: Any,
) -> None:
    dependency = scout_route_auth_dependency(auth_dependency)
    routes = {getattr(route, "path", None) for route in app.router.routes}
    if "/internal/v17/reliability-receipts" in routes:
        return

    @app.post(
        "/internal/v17/reliability-receipts",
        dependencies=[dependency],
        operation_id="persistWowV17ReliabilityReceipt",
    )
    def persist_reliability_receipt(payload: dict[str, Any]) -> dict[str, Any]:
        return _persist(db_client_fn(), payload)


__all__ = ["CAN_EXECUTE", "TABLE", "install_reliability_receipt_routes"]
