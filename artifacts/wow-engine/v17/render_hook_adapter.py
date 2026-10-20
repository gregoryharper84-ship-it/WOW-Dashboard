"""Inert request and admission contracts for a future Render Deploy Hook writer.

This module has no network transport. Candidate evidence is caller supplied and
cannot establish authorization. The protected release workflow must remain
fail-closed until an independent cutover review activates a sole writer.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request


SERVICE_ID = "srv-da7sa9gu01pc73brt80g"
HOOK_SECRET_NAME = "WOW_RENDER_DEPLOY_HOOK"
HOOK_WRITER_ACTIVATED = False
REQUIRED_CHECKS = (
    "WOW required-three regression",
    "WOW additional required regression",
    "WOW governed probability backend",
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DEPLOY_ID_RE = re.compile(r"^dep-[a-z0-9]+$")
_PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class RenderHookContractError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class HookRequestCandidate:
    request: Request = field(repr=False)
    service_id: str
    target_sha: str
    can_execute: bool = False

    def __repr__(self) -> str:
        return (
            f"HookRequestCandidate(service_id={self.service_id!r}, "
            f"target_sha={self.target_sha!r}, hook_url='[REDACTED]', "
            "can_execute=False)"
        )

    def receipt_fields(self) -> dict[str, object]:
        return {
            "service_id": self.service_id,
            "target_sha": self.target_sha,
            "status": "HOOK_REQUEST_CANDIDATE_NOT_SENT",
            "can_execute": False,
        }


def plan_hook_activation(evidence: object) -> dict[str, object]:
    """Validate supplied release facts without treating them as authorization."""
    if not isinstance(evidence, Mapping):
        return _hold("RENDER_HOOK_EVIDENCE_INVALID")

    target_sha = evidence.get("target_sha")
    if not isinstance(target_sha, str) or _SHA_RE.fullmatch(target_sha) is None:
        return _hold("RENDER_HOOK_TARGET_SHA_INVALID")
    if evidence.get("current_main_sha") != target_sha:
        return _hold("RENDER_HOOK_STALE_MAIN_SHA")
    if evidence.get("service_id") != SERVICE_ID:
        return _hold("RENDER_HOOK_SERVICE_IDENTITY_MISMATCH")

    checks = evidence.get("checks")
    if not isinstance(checks, Mapping):
        return _hold("RENDER_HOOK_REQUIRED_CHECKS_MISSING")
    for name in REQUIRED_CHECKS:
        check = checks.get(name)
        if not isinstance(check, Mapping):
            return _hold("RENDER_HOOK_REQUIRED_CHECK_MISSING:" + name)
        if (
            check.get("head_sha") != target_sha
            or check.get("status") != "completed"
            or check.get("conclusion") != "success"
        ):
            return _hold("RENDER_HOOK_REQUIRED_CHECK_NOT_SUCCESS:" + name)

    decision = evidence.get("release_decision")
    if not isinstance(decision, Mapping):
        return _hold("RENDER_HOOK_INDEPENDENT_RELEASE_DECISION_MISSING")
    if (
        not decision.get("decision_id")
        or decision.get("reviewed_sha") != target_sha
        or not decision.get("reviewer")
        or not decision.get("author")
        or decision.get("reviewer") == decision.get("author")
        or decision.get("role") not in {"security", "release", "codeowner"}
    ):
        return _hold("RENDER_HOOK_INDEPENDENT_RELEASE_DECISION_INVALID")

    scope = evidence.get("provider_scope")
    if not isinstance(scope, Mapping):
        return _hold("RENDER_HOOK_PROVIDER_SCOPE_UNATTESTED")
    if (
        scope.get("independently_attested") is not True
        or scope.get("service_id") != SERVICE_ID
        or not isinstance(scope.get("principal_id"), str)
        or not scope.get("principal_id")
        or scope.get("principal_id").strip().lower() in {"unknown", "unverified", "none"}
        or _PRINCIPAL_RE.fullmatch(scope.get("principal_id", "")) is None
        or scope.get("scope") != "service_deploy_hook"
    ):
        return _hold("RENDER_HOOK_PROVIDER_SCOPE_UNATTESTED")

    inventory = evidence.get("writer_inventory")
    if not isinstance(inventory, Mapping) or inventory.get("complete") is not True:
        return _hold("RENDER_HOOK_WRITER_INVENTORY_UNVERIFIED")
    active_writers = inventory.get("active_writers")
    if not isinstance(active_writers, list) or active_writers != [
        "wow-v17-render-production-deploy:service_deploy_hook"
    ]:
        return _hold("RENDER_HOOK_DUPLICATE_OR_UNKNOWN_WRITER")
    if inventory.get("old_controller_disabled") is not True:
        return _hold("RENDER_HOOK_OLD_WRITER_NOT_DISABLED")

    run = evidence.get("run")
    if (
        not isinstance(run, Mapping)
        or run.get("repository") != "gregoryharper84-ship-it/WOW-Dashboard"
        or run.get("workflow_path") != ".github/workflows/wow-v17-render-production-deploy.yml"
        or run.get("ref") != "refs/heads/main"
        or run.get("head_sha") != target_sha
        or not str(run.get("run_id") or "").isdigit()
        or not str(run.get("run_attempt") or "").isdigit()
    ):
        return _hold("RENDER_HOOK_WORKFLOW_IDENTITY_UNVERIFIED")
    previous_runs = evidence.get("previously_dispatched_run_ids")
    if not isinstance(previous_runs, list) or any(
        not str(run_id).isdigit() for run_id in previous_runs
    ):
        return _hold("RENDER_HOOK_RUN_INVENTORY_UNVERIFIED")
    if str(run.get("run_id")) in {str(run_id) for run_id in previous_runs}:
        return _hold("RENDER_HOOK_DUPLICATE_RUN")

    receipt = evidence.get("live_receipt")
    if (
        not isinstance(evidence.get("expected_live_sha"), str)
        or _SHA_RE.fullmatch(evidence["expected_live_sha"]) is None
        or not isinstance(receipt, Mapping)
        or receipt.get("service_id") != SERVICE_ID
        or receipt.get("status") != "live"
        or receipt.get("sha") != evidence.get("expected_live_sha")
        or not isinstance(receipt.get("revision"), str)
        or not receipt.get("revision")
    ):
        return _hold("RENDER_HOOK_LIVE_RECEIPT_STALE_OR_UNVERIFIED")
    if evidence.get("provider_ack") is not None:
        return _hold("RENDER_HOOK_PROVIDER_ACK_UNCERTAIN")
    if evidence.get("can_execute") is not False:
        return _hold("RENDER_HOOK_CAN_EXECUTE_MUST_BE_FALSE")

    return {
        "status": "CLAIMS_CONSISTENT_NOT_AUTHORIZED",
        "reason_code": "RENDER_HOOK_CUTOVER_REQUIRES_INDEPENDENT_APPROVAL",
        "can_execute": False,
        "dispatch_enabled": False,
    }


def _hold(reason_code: str) -> dict[str, object]:
    return {
        "status": "BLOCKED_WITH_EXACT_REASON",
        "reason_code": reason_code,
        "can_execute": False,
        "dispatch_enabled": False,
    }


def prepare_exact_sha_request(
    hook_url: str,
    target_sha: str,
    *,
    current_main_sha: str,
    service_id: str = SERVICE_ID,
) -> HookRequestCandidate:
    """Build a redacted request candidate in memory; never sends it."""
    if not isinstance(target_sha, str) or _SHA_RE.fullmatch(target_sha) is None:
        raise RenderHookContractError("RENDER_HOOK_TARGET_SHA_INVALID")
    if current_main_sha != target_sha:
        raise RenderHookContractError("RENDER_HOOK_STALE_MAIN_SHA")
    if service_id != SERVICE_ID:
        raise RenderHookContractError("RENDER_HOOK_SERVICE_IDENTITY_MISMATCH")
    if not isinstance(hook_url, str) or not hook_url:
        raise RenderHookContractError("RENDER_HOOK_SECRET_MISSING")
    try:
        parsed = urlsplit(hook_url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        raise RenderHookContractError("RENDER_HOOK_URL_INVALID") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.render.com"
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.path != f"/deploy/{SERVICE_ID}"
    ):
        raise RenderHookContractError("RENDER_HOOK_ENDPOINT_MISMATCH")
    if len(query) != 1 or query[0][0] != "key" or not query[0][1]:
        raise RenderHookContractError("RENDER_HOOK_QUERY_INVALID")

    request_url = urlunsplit(
        (
            "https",
            "api.render.com",
            parsed.path,
            urlencode((("key", query[0][1]), ("ref", target_sha))),
            "",
        )
    )
    request = Request(
        request_url,
        method="POST",
        headers={"Accept": "application/json"},
    )
    return HookRequestCandidate(request, SERVICE_ID, target_sha)


def classify_hook_ack(http_status: object, body: bytes | str | None) -> dict[str, object]:
    """Classify an already-observed response; never authorizes a retry or deploy."""
    if type(http_status) is not int:
        return _ack("HOOK_RESPONSE_UNVERIFIED")
    if http_status == 202:
        return _ack("HOOK_QUEUED_RECONCILE_NO_RETRY")
    if http_status != 200:
        return _ack("HOOK_RESPONSE_HOLD", reason_code=f"HOOK_HTTP_{http_status}")
    try:
        payload = json.loads(body if body is not None else "")
    except (TypeError, UnicodeDecodeError, ValueError):
        return _ack("HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY")
    deploy = payload.get("deploy") if isinstance(payload, dict) else None
    deploy_id = deploy.get("id") if isinstance(deploy, dict) else None
    if not isinstance(deploy_id, str) or _DEPLOY_ID_RE.fullmatch(deploy_id) is None:
        return _ack("HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY")
    return _ack("HOOK_ACK_PENDING_EXACT_SHA_READBACK", deploy_id=deploy_id)


def _ack(status: str, **extra: object) -> dict[str, object]:
    return {
        "status": status,
        "retry": False,
        "can_execute": False,
        **extra,
    }
