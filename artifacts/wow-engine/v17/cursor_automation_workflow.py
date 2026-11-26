"""Trusted, manual entrypoint inside the existing provider dispatcher."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

try:
    from .cursor_automation_pilot import (
        _SLOT_KEY, PilotHold, VerificationAdmission, launch_verification_pilot,
    )
    from .cursor_cloud_handoff import CURSOR_API, HandoffBlocked, api_json
    from .engineering_remote_fallback import (
        LEDGER, REPOSITORY, GitHub, ProviderUnavailable, assert_no_local_writer,
        assert_no_remote_writer,
    )
except ImportError:
    from cursor_automation_pilot import (
        _SLOT_KEY, PilotHold, VerificationAdmission, launch_verification_pilot,
    )
    from cursor_cloud_handoff import CURSOR_API, HandoffBlocked, api_json
    from engineering_remote_fallback import (
        LEDGER, REPOSITORY, GitHub, ProviderUnavailable, assert_no_local_writer,
        assert_no_remote_writer,
    )


_AUTOMATION_ID = UUID("53ad6328-e828-11f1-8a40-564e3474c41f")
_TERMINAL_OR_IDLE = frozenset({"IDLE", "ARCHIVED"})


def _budget(raw: str, now: datetime) -> tuple[str, datetime]:
    try:
        data = json.loads(raw)
        checked = datetime.fromisoformat(data["checked_at"])
        expires = datetime.fromisoformat(data["expires_at"])
        receipt = data["receipt_id"]
        safe = (
            isinstance(receipt, str) and bool(receipt)
            and data["automation_id"] == str(_AUTOMATION_ID)
            and data["model"] == "Composer 2.5"
            and data["authenticated_account_readback"] is True
            and data["included_usage_available"] is True
            and type(data["max_additional_spend_cents"]) is int
            and data["max_additional_spend_cents"] == 0
            and data["paid_overages_disabled"] is True
            and checked <= now < expires <= checked + timedelta(hours=1)
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        safe = False
    if not safe:
        raise PilotHold("CURSOR_AUTOMATION_BUDGET_UNVERIFIED")
    return receipt, expires


def _inventory(key: str) -> None:
    try:
        inventory = api_json(f"{CURSOR_API}/v1/agents?limit=100", token=key, basic=True)
    except HandoffBlocked:
        raise PilotHold("CURSOR_AUTOMATION_INVENTORY_UNVERIFIED") from None
    if (not isinstance(inventory, dict) or inventory.get("nextCursor")
            or not isinstance(inventory.get("items"), list)):
        raise PilotHold("CURSOR_AUTOMATION_INVENTORY_UNVERIFIED")
    for item in inventory["items"]:
        status = item.get("status") if isinstance(item, dict) else None
        if not isinstance(status, str) or status not in _TERMINAL_OR_IDLE:
            raise PilotHold("CURSOR_AUTOMATION_AGENT_ACTIVE_OR_UNVERIFIED")


def _admit(env) -> VerificationAdmission:
    if (
        env.get("GITHUB_REPOSITORY") != REPOSITORY
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_ACTOR") != REPOSITORY.split("/", 1)[0]
        or env.get("GITHUB_RUN_ATTEMPT") != "1"
    ):
        raise PilotHold("CURSOR_AUTOMATION_CALLER_UNVERIFIED")
    receipt, expires = _budget(env.get("CURSOR_VERIFICATION_BUDGET_EVIDENCE", ""),
                               datetime.now(timezone.utc))
    gh_token = env.get("GH_TOKEN")
    inventory_key = env.get("CURSOR_INVENTORY_KEY")
    if not isinstance(gh_token, str) or not gh_token or not inventory_key:
        raise PilotHold("CURSOR_AUTOMATION_CREDENTIAL_OR_TARGET_UNAVAILABLE")
    github = GitHub(gh_token)
    try:
        comments = github.pages(LEDGER)
        assert_no_local_writer(comments)
        assert_no_remote_writer(comments, get_run=lambda run_id: github.request(
            f"/repos/{REPOSITORY}/actions/runs/{run_id}"
        ))
    except ProviderUnavailable:
        raise PilotHold("CURSOR_AUTOMATION_WRITER_FENCE_UNVERIFIED") from None
    _inventory(inventory_key)
    try:
        main = github.request(f"/repos/{REPOSITORY}/commits/main")
        main_sha = env["GITHUB_SHA"]
        if not isinstance(main, dict) or main.get("sha") != main_sha:
            raise ValueError
        run_id = int(env["GITHUB_RUN_ID"])
    except (KeyError, TypeError, ValueError, ProviderUnavailable):
        raise PilotHold("CURSOR_AUTOMATION_ADMISSION_UNVERIFIED") from None
    return VerificationAdmission(run_id, main_sha, main_sha, receipt, expires, True, True, True)


def _redis_client(dsn: str):
    try:
        from redis import Redis

        address = urlsplit(dsn)
        if address.scheme != "rediss" or not address.hostname or address.query or address.fragment:
            raise ValueError
        return Redis.from_url(
            dsn, ssl_cert_reqs="required", ssl_check_hostname=True,
            socket_connect_timeout=5, socket_timeout=5,
        )
    except Exception:
        raise PilotHold("CURSOR_AUTOMATION_SLOT_UNVERIFIED") from None


def run(env) -> dict:
    mode = env.get("CURSOR_VERIFICATION_MODE")
    if mode not in {"preflight", "launch"}:
        raise PilotHold("CURSOR_AUTOMATION_MODE_UNSUPPORTED")
    if mode == "launch" and env.get("CURSOR_VERIFICATION_ENABLED") != "true":
        raise PilotHold("CURSOR_AUTOMATION_LAUNCH_DISABLED")
    if env.get("CURSOR_VERIFICATION_DURABLE_STORE_VERIFIED") != "true":
        raise PilotHold("CURSOR_AUTOMATION_SLOT_DURABILITY_UNVERIFIED")
    admission = _admit(env)
    dsn = env.get("CURSOR_VERIFICATION_REDIS_URL", "")
    key = env.get("CURSOR_VERIFICATION_WEBHOOK_KEY", "")
    if not key:
        raise PilotHold("CURSOR_AUTOMATION_CREDENTIAL_OR_TARGET_UNAVAILABLE")
    with _redis_client(dsn) as store:
        if mode == "launch":
            return launch_verification_pilot(
                admission, automation_id=_AUTOMATION_ID, webhook_key=key, redis_client=store,
            )
        try:
            available = store.exists(_SLOT_KEY) == 0
        except Exception:
            raise PilotHold("CURSOR_AUTOMATION_SLOT_UNVERIFIED") from None
        if not available:
            raise PilotHold("CURSOR_AUTOMATION_SLOT_HELD_OR_ALREADY_USED")
    return {
        "status": "CURSOR_AUTOMATION_PREFLIGHT_READY",
        "dispatcher_run_id": admission.reservation_run_id,
        "main_sha": admission.main_sha,
        "budget_receipt_id": admission.budget_receipt_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        receipt = run(os.environ)
    except PilotHold as error:
        receipt = {"status": "SAFE_HOLD", "code": str(error)}
    receipt.update(can_execute=False, production_verified=False)
    args.receipt.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return int(receipt["status"] == "SAFE_HOLD")


if __name__ == "__main__":
    raise SystemExit(main())
