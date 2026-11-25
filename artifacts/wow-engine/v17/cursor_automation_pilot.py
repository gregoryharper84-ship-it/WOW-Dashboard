"""Dispatcher-side transport for one read-only Engineering Verification pilot.

Only the existing dispatcher may call this adapter, after authenticating its
admission inputs and checking its existing provider and writer fences. This
module has no workflow trigger and consumes no Cursor usage when imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID


_SLOT_KEY = "wow:v17:engineering:cursor-automation-pilot:single-launch"
_WEBHOOK_ROOT = "https://api2.cursor.sh/automations/webhook/"
_SHA = re.compile(r"[0-9a-f]{40}")
_AGENT_ID = re.compile(r"bc-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
_MAX_RESPONSE_BYTES = 16384


@dataclass(frozen=True)
class VerificationAdmission:
    """Inputs supplied by the trusted dispatcher, never by the webhook body."""

    reservation_run_id: int
    main_sha: str
    current_main_sha: str
    budget_receipt_id: str
    budget_valid_until: datetime
    included_usage_available: bool
    paid_overages_disabled: bool
    remote_writers_clear: bool


class PilotHold(Exception):
    """A stable failure code safe to include in operator-facing receipts."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(request: Request) -> str:
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            if response.status != 200:
                raise PilotHold("CURSOR_AUTOMATION_RESPONSE_UNVERIFIED")
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise PilotHold("CURSOR_AUTOMATION_RESPONSE_UNVERIFIED")
            body = json.loads(raw)
    except HTTPError as error:
        raise PilotHold(f"CURSOR_AUTOMATION_HTTP_{error.code}") from None
    except (URLError, OSError, ValueError):
        raise PilotHold("CURSOR_AUTOMATION_RESPONSE_UNVERIFIED") from None
    agent_id = body.get("backgroundComposerId") if isinstance(body, dict) else None
    if not isinstance(agent_id, str) or not _AGENT_ID.fullmatch(agent_id):
        raise PilotHold("CURSOR_AUTOMATION_AGENT_ID_UNVERIFIED")
    return agent_id


def launch_verification_pilot(
    admission: VerificationAdmission,
    *,
    automation_id: UUID,
    webhook_key: str,
    redis_client,
) -> dict:
    """Reserve durably before one POST. Never retry or clear an ambiguous launch."""
    if (
        not isinstance(automation_id, UUID)
        or not isinstance(webhook_key, str)
        or not re.fullmatch(r"[!-~]{1,4096}", webhook_key)
    ):
        raise PilotHold("CURSOR_AUTOMATION_CREDENTIAL_OR_TARGET_UNAVAILABLE")
    if (
        type(admission.reservation_run_id) is not int
        or admission.reservation_run_id < 1
        or not _SHA.fullmatch(admission.main_sha)
        or admission.main_sha != admission.current_main_sha
        or admission.remote_writers_clear is not True
    ):
        raise PilotHold("CURSOR_AUTOMATION_ADMISSION_UNVERIFIED")
    if (
        not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", admission.budget_receipt_id)
        or admission.budget_valid_until.tzinfo is None
        or admission.budget_valid_until <= datetime.now(timezone.utc)
        or admission.included_usage_available is not True
        or admission.paid_overages_disabled is not True
    ):
        raise PilotHold("CURSOR_AUTOMATION_BUDGET_UNVERIFIED")

    reservation = {
        "task_id": f"cursor-verification-smoke:{admission.reservation_run_id}",
        "expires_at": admission.budget_valid_until.astimezone(timezone.utc).isoformat(),
        "lane": "ENGINEERING_VERIFICATION",
        "mode": "READ_ONLY_SMOKE",
        "dispatcher_run_id": admission.reservation_run_id,
        "main_sha": admission.main_sha,
        "budget_receipt_id": admission.budget_receipt_id,
        "can_execute": False,
    }
    request = Request(
        _WEBHOOK_ROOT + str(automation_id),
        data=json.dumps({
            **reservation,
            "context": (
                "Read-only Engineering Verification smoke. Verify the dispatcher "
                "receipt and exact main SHA, then read GitHub, Render and Supabase "
                "evidence. Report missing credentials or evidence as blocked. "
                "Do not mutate any system or launch another job."
            ),
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {webhook_key}",
        },
        method="POST",
    )
    try:
        reserved = redis_client.set(
            _SLOT_KEY, json.dumps(reservation, sort_keys=True), nx=True,
        )
    except Exception:
        raise PilotHold("CURSOR_AUTOMATION_SLOT_UNVERIFIED") from None
    if reserved is not True:
        raise PilotHold("CURSOR_AUTOMATION_SLOT_HELD_OR_ALREADY_USED")
    if admission.budget_valid_until <= datetime.now(timezone.utc):
        raise PilotHold("CURSOR_AUTOMATION_BUDGET_EXPIRED_AFTER_RESERVATION")

    # The persistent slot intentionally survives success, timeout and cancellation.
    # One finished Cursor run may be followed by another run on the same agent.
    agent_id = _post(request)
    return {
        **reservation,
        "status": "CURSOR_AUTOMATION_LAUNCH_ACKNOWLEDGED",
        "agent_id": agent_id,
        "production_verified": False,
    }
