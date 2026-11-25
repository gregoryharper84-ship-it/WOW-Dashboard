"""Controlled cutover: retire native Render auto-deploy on both production services.

WOW-V17-DEPLOY-RELIABILITY-P0 / #2422. After this PATCH, the sole production
writer is ``wow-v17-required-checks-deploy.yml``. Blueprint ``render.yaml``
already declares ``autoDeployTrigger: off``; live services must match.

Writes are limited to PATCH ``/v1/services/<id>`` with only:
``{"autoDeployTrigger": "off"}``.

Render rejects setting both ``autoDeploy`` and ``autoDeployTrigger`` in one
request, so the cutover never sends the deprecated ``autoDeploy`` field.

Never POSTs a deploy, never mutates env vars/plans, never certifies models.
``can_execute=false``.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from v17.lean_production_readback import (
    CANONICAL_AUTO_DEPLOY_TRIGGER,
    REPO,
    SERVICES,
    SOLE_DEPLOY_WRITER,
    native_auto_deploy_retired,
    service_identity_ok,
)

# Render Update Service: do not send autoDeploy together with autoDeployTrigger.
CUTOVER_PAYLOAD = {"autoDeployTrigger": CANONICAL_AUTO_DEPLOY_TRIGGER}

Fetch = Callable[[str, str, Any], Any]


class CutoverHold(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def plan_cutover(services: dict[str, Any]) -> dict[str, Any]:
    """Pure decision: which roles still need the native writer retired."""
    if not isinstance(services, dict):
        raise CutoverHold("SERVICE_READBACK_INVALID")
    pending: list[str] = []
    for role in SERVICES:
        service = services.get(role)
        if not service_identity_ok(role, service):
            raise CutoverHold("RENDER_SINGLE_WRITER_SERVICE_CONFIGURATION_MISMATCH:" + role)
        # autoDeployTrigger is the authoritative native-writer switch.
        if service.get("autoDeployTrigger") != CANONICAL_AUTO_DEPLOY_TRIGGER:
            pending.append(role)
    return {
        "status": "CUTOVER_NEEDED" if pending else "CUTOVER_ALREADY_COMPLETE",
        "pending": pending,
        "sole_deploy_writer": SOLE_DEPLOY_WRITER,
        "target": CUTOVER_PAYLOAD,
        "can_execute": False,
    }


def run(*, fetch: Fetch, apply: bool) -> dict[str, Any]:
    render = "https://api.render.com/v1/services/"
    before: dict[str, Any] = {}
    for role, service_id in SERVICES.items():
        before[role] = fetch("GET", render + urllib.parse.quote(service_id, safe=""), None)
    decision = plan_cutover(before)
    if decision["status"] == "CUTOVER_ALREADY_COMPLETE" or not apply:
        return {
            **decision,
            "applied": False,
            "before_triggers": {
                role: (before[role] or {}).get("autoDeployTrigger") for role in SERVICES
            },
            "native_auto_deploy_retired": native_auto_deploy_retired(before),
            "production_verified": False,
        }
    patched: dict[str, str] = {}
    for role in decision["pending"]:
        sid = urllib.parse.quote(SERVICES[role], safe="")
        updated = fetch("PATCH", render + sid, CUTOVER_PAYLOAD)
        patched[role] = str((updated or {}).get("autoDeployTrigger") or "")
    after: dict[str, Any] = {}
    for role, service_id in SERVICES.items():
        after[role] = fetch("GET", render + urllib.parse.quote(service_id, safe=""), None)
        if not service_identity_ok(role, after[role]):
            raise CutoverHold("RENDER_SINGLE_WRITER_SERVICE_CONFIGURATION_MISMATCH:" + role)
        if after[role].get("autoDeployTrigger") != CANONICAL_AUTO_DEPLOY_TRIGGER:
            raise CutoverHold("CUTOVER_VERIFY_TRIGGER_NOT_OFF:" + role)
    return {
        "status": "CUTOVER_APPLIED_AND_VERIFIED",
        "pending": [],
        "patched": patched,
        "sole_deploy_writer": SOLE_DEPLOY_WRITER,
        "target": CUTOVER_PAYLOAD,
        "before_triggers": {
            role: (before[role] or {}).get("autoDeployTrigger") for role in SERVICES
        },
        "after_triggers": {
            role: (after[role] or {}).get("autoDeployTrigger") for role in SERVICES
        },
        "native_auto_deploy_retired": native_auto_deploy_retired(after),
        "applied": True,
        "production_verified": False,
        "can_execute": False,
    }


def main() -> int:
    receipt = Path(os.environ.get("CUTOVER_RECEIPT", "wow-v17-single-writer-cutover.json"))
    apply = os.environ.get("APPLY_CUTOVER", "false").lower() == "true"
    wrote = {"patch": False}
    try:
        if os.environ.get("WOW_CAN_EXECUTE") != "false" or os.environ.get("WOW_DRY_RUN_ONLY") != "true":
            raise CutoverHold("EXECUTION_INVARIANT_MISMATCH")
        if os.environ.get("GITHUB_REPOSITORY") != REPO:
            raise CutoverHold("REPOSITORY_MISMATCH")
        token = os.environ.get("RENDER_API_KEY") or ""
        if not token:
            raise CutoverHold("API_CREDENTIAL_MISSING")
        headers = {"Accept": "application/json", "Authorization": "Bearer " + token}

        def fetch(method: str, url: str, body: Any) -> Any:
            allowed = method == "GET" or (
                method == "PATCH"
                and url.startswith("https://api.render.com/v1/services/")
                and url.rstrip("/").count("/") == 5  # https://api.render.com/v1/services/<id>
            )
            if not allowed:
                raise CutoverHold("WRITE_TARGET_NOT_ALLOWED")
            if method == "PATCH":
                if body != CUTOVER_PAYLOAD:
                    raise CutoverHold("CUTOVER_PAYLOAD_NOT_ALLOWED")
                wrote["patch"] = True
            data = json.dumps(body).encode("utf-8") if body is not None else None
            request = urllib.request.Request(url, data=data, method=method, headers={
                **headers,
                **({"Content-Type": "application/json"} if data else {}),
            })
            with _OPENER.open(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}

        outcome = run(fetch=fetch, apply=apply)
        code = 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, CutoverHold) else "CUTOVER_SOURCE_UNAVAILABLE_OR_INVALID"
        outcome = {
            "status": "CUTOVER_HOLD",
            "reason": reason,
            "applied": wrote["patch"],
            "production_verified": False,
            "can_execute": False,
        }
        code = 1
    receipt.write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(
                f"single-writer-cutover: {outcome.get('status')} "
                f"applied={outcome.get('applied')} can_execute=false\n"
            )
            if outcome.get("status") == "CUTOVER_HOLD":
                handle.write(f"::error::CUTOVER_HOLD:{outcome.get('reason')}\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
