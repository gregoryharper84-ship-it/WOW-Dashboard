"""One bounded, authenticated WOW Agent Runtime research-cycle admission.

Run from protected-main GitHub Actions only after #2318 auth has merged,
independent review passed and the distinct runtime bearer is provisioned.
No probability, wager execution or synthetic sporting candidate is created.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import UUID


def admission_for_hour(now: datetime) -> tuple[str, dict]:
    if now.tzinfo is None:
        raise ValueError("PRODUCER_TIME_NOT_AWARE")
    slot = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    request = {
        "run_type": "FULL_MODEL",
        "as_of": slot.isoformat().replace("+00:00", "Z"),
        "user_timezone": "America/Chicago",
        "lanes": ["TEAM_EVENT_PROBABILITY"],
        "sports": ["MLB", "NFL", "NCAAF", "NBA", "WNBA", "NCAAB", "NHL"],
        "candidate_inputs": [],
        "discovery_enabled": True,
        "can_execute": False,
    }
    return "wow-agent-prod-" + slot.strftime("%Y%m%d%H"), request


def admit_cycle(*, now: datetime, origin: str, runtime_key: str) -> dict:
    if not origin.startswith("https://") or not runtime_key or len(runtime_key) < 32:
        raise ValueError("PRODUCER_AUTH_OR_ORIGIN_UNAVAILABLE")
    if not runtime_key.isascii():
        raise ValueError("PRODUCER_CREDENTIAL_INVALID")
    key, payload = admission_for_hour(now)
    endpoint = origin.rstrip("/") + "/wow/runs"
    request = urllib.request.Request(
        endpoint, method="POST", data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + runtime_key,
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("PRODUCER_INGRESS_HTTP_" + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("PRODUCER_INGRESS_TRANSPORT_FAILED") from None
    if status != 202 or not isinstance(body, dict):
        raise RuntimeError("PRODUCER_INGRESS_RESPONSE_INVALID")
    try:
        run_id = str(UUID(str(body["run_id"])))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise RuntimeError("PRODUCER_RUN_RECEIPT_INVALID") from None
    if body.get("ok") is not True or body.get("can_execute") is not False:
        raise RuntimeError("PRODUCER_RUN_RECEIPT_INVALID")
    return {
        "run_id": run_id, "status": body.get("status"), "reused": body.get("reused"),
        "idempotency_key": key, "can_execute": False,
        "job_execution_verified": False,
        "model_probability_verified": False,
    }


if __name__ == "__main__":
    if os.getenv("WOW_CAN_EXECUTE") != "false" or os.getenv("WOW_DRY_RUN_ONLY") != "true":
        raise SystemExit("PRODUCER_SAFETY_ENV_INVALID")
    if os.getenv("GITHUB_REF") != "refs/heads/main":
        raise SystemExit("PRODUCER_PROTECTED_MAIN_REQUIRED")
    token = os.getenv("WOW_AGENT_RUNTIME_API_KEY", "")
    if token and token == os.getenv("WOW_ACTION_API_KEY", "__unset__"):
        raise SystemExit("PRODUCER_CREDENTIAL_SCOPE_INVALID")
    try:
        receipt = admit_cycle(
            now=datetime.now(timezone.utc),
            origin=os.getenv("WOW_ACTION_ORIGIN", ""),
            runtime_key=token,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(receipt, sort_keys=True))
