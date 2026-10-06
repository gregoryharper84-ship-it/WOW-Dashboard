"""Machine-verifiable WOW engineering production-acceptance receipts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "WOW_ENGINEERING_RELIABILITY_V1"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SIGNATURE = re.compile(r"^sha256:[0-9a-f]{64}$")
REGISTRY_PATH = Path(__file__).with_name("production_acceptance_routes.json")


class VerificationReceipt(BaseModel):
    """Strict terminal-closure evidence.

    exact_head_sha is the final PR head. merge_sha is the protected-main merge
    commit. Normal GitHub merges make these different commits, so production
    equality is intentionally merge_sha == deployed_render_sha.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["WOW_ENGINEERING_RELIABILITY_V1"] = CONTRACT_VERSION
    issue_id: int = Field(gt=0)
    pr_number: int = Field(gt=0)
    exact_head_sha: str
    merge_sha: str
    deployed_render_sha: str
    method: str
    route_tested: str
    schema_hash: str
    http_status: int = Field(ge=100, le=599)
    dry_run_header_present: bool
    can_execute_header_false: bool
    raw_response_digest: str
    execution_trace_digest: str
    timestamp_utc: datetime
    sentinel_workflow: str
    sentinel_workflow_run_id: int = Field(gt=0)
    sentinel_signature: str
    audit_artifact_name: str

    @field_validator("exact_head_sha", "merge_sha", "deployed_render_sha")
    @classmethod
    def _sha40(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA40.fullmatch(normalized):
            raise ValueError("must be a 40-character lowercase git SHA")
        return normalized

    @field_validator("schema_hash", "raw_response_digest", "execution_trace_digest")
    @classmethod
    def _sha256(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("must be a lowercase SHA-256 hex digest")
        return normalized

    @field_validator("method")
    @classmethod
    def _method(cls, value: str) -> str:
        method = value.strip().upper()
        if method not in {"GET", "POST"}:
            raise ValueError("method must be GET or POST")
        return method

    @field_validator("route_tested")
    @classmethod
    def _route(cls, value: str) -> str:
        route = value.strip()
        if not route.startswith("/") or "://" in route or "?" in route:
            raise ValueError("route_tested must be a canonical path without host/query")
        return route

    @field_validator("timestamp_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_utc must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("sentinel_signature")
    @classmethod
    def _signature(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SIGNATURE.fullmatch(normalized):
            raise ValueError("sentinel_signature must be sha256:<64 hex>")
        return normalized

    @field_validator("sentinel_workflow", "audit_artifact_name")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must be non-empty")
        return normalized


def schema_hash() -> str:
    canonical = json.dumps(
        VerificationReceipt.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def response_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_acceptance_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("registry_version") != "WOW_PRODUCTION_ACCEPTANCE_ROUTES_V1":
        raise ValueError("PRODUCTION_ACCEPTANCE_REGISTRY_VERSION_INVALID")
    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValueError("PRODUCTION_ACCEPTANCE_REGISTRY_EMPTY")
    return data


def _allowlisted(method: str, route: str, registry: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict)
        and str(item.get("method") or "").upper() == method.upper()
        and str(item.get("path") or "") == route
        for item in registry.get("routes", [])
    )


def signature_payload(receipt: VerificationReceipt) -> bytes:
    payload = receipt.model_dump(mode="json", exclude={"sentinel_signature"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def expected_sentinel_signature(receipt: VerificationReceipt) -> str:
    return "sha256:" + hashlib.sha256(signature_payload(receipt)).hexdigest()


def validate_terminal_receipt(
    receipt: VerificationReceipt,
    *,
    expected_issue_id: int,
    expected_pr_number: int,
    expected_pr_head_sha: str,
    expected_merge_sha: str,
    registry: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    registry = registry or load_acceptance_registry()

    if receipt.issue_id != expected_issue_id:
        errors.append("ISSUE_ID_MISMATCH")
    if receipt.pr_number != expected_pr_number:
        errors.append("PR_NUMBER_MISMATCH")
    if receipt.exact_head_sha != expected_pr_head_sha.lower():
        errors.append("EXACT_HEAD_SHA_MISMATCH")
    if receipt.merge_sha != expected_merge_sha.lower():
        errors.append("MERGE_SHA_MISMATCH")
    if receipt.deployed_render_sha != receipt.merge_sha:
        errors.append("DEPLOYED_SHA_NOT_EXACT_MERGE")
    if receipt.schema_hash != schema_hash():
        errors.append("RECEIPT_SCHEMA_HASH_MISMATCH")
    if not _allowlisted(receipt.method, receipt.route_tested, registry):
        errors.append("ACCEPTANCE_ROUTE_NOT_ALLOWLISTED")
    if not 200 <= receipt.http_status <= 299:
        errors.append("ACCEPTANCE_HTTP_STATUS_NOT_SUCCESS")
    if receipt.dry_run_header_present is not True:
        errors.append("DRY_RUN_HEADER_MISSING")
    if receipt.can_execute_header_false is not True:
        errors.append("CAN_EXECUTE_FALSE_HEADER_MISSING")
    if receipt.sentinel_signature != expected_sentinel_signature(receipt):
        errors.append("SENTINEL_SIGNATURE_MISMATCH")
    return errors


__all__ = [
    "CONTRACT_VERSION",
    "VerificationReceipt",
    "expected_sentinel_signature",
    "load_acceptance_registry",
    "response_digest",
    "schema_hash",
    "validate_terminal_receipt",
]
