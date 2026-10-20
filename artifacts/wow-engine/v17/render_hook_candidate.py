"""Inert adapter for the proposed protected Render service Deploy Hook.

This module NEVER sends an HTTP request. It only constructs a request object
in memory and classifies provider responses. The live protected controller,
authoritative GitHub evidence, exclusive lease, single-use intent, secret-scope
attestation, and independent release gate MUST be implemented/approved separately.

Hook URLs are credentials: never log, serialize, persist, or interpolate them into
exceptions/receipts. Runtime trading execution remains disabled.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request

SERVICE_ID = "srv-da7sa9gu01pc73brt80g"
HOOK_SECRET = "WOW_RENDER_DEPLOY_HOOK"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_DEPLOY_ID = re.compile(r"^dep-[a-z0-9]+$")


class HookContractError(ValueError):
    """Redacted, typed failure without returning the sensitive hook URL."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PreparedHook:
    """Secret-bearing object; never write repr, JSON, or its request URL to logs."""
    request: Request = field(repr=False)
    service_id: str = SERVICE_ID
    target_sha: str = ""
    can_execute: bool = False

    def __repr__(self) -> str:
        return (f"PreparedHook(service_id={self.service_id!r}, "
                f"target_sha={self.target_sha!r}, hook_url='[REDACTED]', "
                "can_execute=False)")


def prepare_exact_commit_hook(hook_url: str, target_sha: str, *, service_id: str = SERVICE_ID) -> PreparedHook:
    """Build (but never execute) one exact-SHA POST to the specified service.

    A syntactically correct hook is NOT service ownership proof or authorization.
    The caller must not use this request without independent release admission.
    """
    if type(target_sha) is not str or _SHA.fullmatch(target_sha) is None:
        raise HookContractError("HOOK_TARGET_SHA_INVALID")
    if service_id != SERVICE_ID:
        raise HookContractError("HOOK_SERVICE_ID_INVALID")
    if type(hook_url) is not str or not hook_url:
        raise HookContractError("HOOK_SECRET_MISSING")
    try:
        parsed = urlsplit(hook_url)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError:
        raise HookContractError("HOOK_URL_INVALID") from None
    if (parsed.scheme != "https" or parsed.hostname != "api.render.com"
            or port not in (None, 443) or parsed.username is not None
            or parsed.password is not None or parsed.fragment
            or parsed.path != "/deploy/" + SERVICE_ID):
        raise HookContractError("HOOK_ENDPOINT_MISMATCH")
    if len(query) != 1 or query[0][0] != "key" or not query[0][1]:
        raise HookContractError("HOOK_QUERY_INVALID")
    # Ref is set by the protected controller from independently verified main,
    # never from the secret URL or an untrusted workflow dispatch argument.
    bounded_url = urlunsplit(("https", "api.render.com", parsed.path,
                             urlencode((("key", query[0][1]), ("ref", target_sha))), ""))
    request = Request(bounded_url, method="POST", headers={"Accept": "application/json"})
    return PreparedHook(request=request, target_sha=target_sha)


def classify_hook_ack(http_status: int, body: bytes | str | None) -> dict[str, object]:
    """No release success before independent exact-SHA Render readback.

    200 returns a deployment identifier to reconcile, not a success receipt.
    202/transport ambiguity MUST NEVER trigger a second hook invocation.
    """
    if type(http_status) is not int:
        return {"status": "HOOK_RESPONSE_UNVERIFIED", "retry": False, "deploy_id": "", "can_execute": False}
    if http_status == 202:
        return {"status": "HOOK_QUEUED_RECONCILE_NO_RETRY", "retry": False, "deploy_id": "", "can_execute": False}
    # A timeout, throttling response, or provider-side failure does not prove
    # the request was rejected before the deploy was queued. Under the durable
    # once-only intent, reconcile provider state read-only; NEVER submit again.
    if http_status in {408, 429} or 500 <= http_status <= 599:
        return {"status": "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY",
                "reason": "HOOK_HTTP_" + str(http_status),
                "retry": False, "deploy_id": "", "can_execute": False}
    if http_status != 200:
        return {"status": "HOOK_RESPONSE_HOLD", "reason": "HOOK_HTTP_" + str(http_status),
                "retry": False, "deploy_id": "", "can_execute": False}
    try:
        data = json.loads(body if body is not None else "")
    except (TypeError, UnicodeDecodeError, ValueError):
        return {"status": "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY", "retry": False,
                "deploy_id": "", "can_execute": False}
    if not isinstance(data, dict) or not isinstance(data.get("deploy"), dict):
        return {"status": "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY", "retry": False,
                "deploy_id": "", "can_execute": False}
    deploy_id = data["deploy"].get("id")
    if type(deploy_id) is not str or _DEPLOY_ID.fullmatch(deploy_id) is None:
        return {"status": "HOOK_ACK_AMBIGUOUS_RECONCILE_NO_RETRY", "retry": False,
                "deploy_id": "", "can_execute": False}
    return {"status": "HOOK_ACK_PENDING_EXACT_SHA_READBACK", "retry": False,
            "deploy_id": deploy_id, "can_execute": False}
