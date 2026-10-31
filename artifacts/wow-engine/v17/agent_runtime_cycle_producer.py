"""One bounded, authenticated WOW Agent Runtime research-cycle admission.

Run from protected-main GitHub Actions only after #2318 auth has merged,
independent review passed and the distinct runtime bearer is provisioned.
No probability, wager execution or synthetic sporting candidate is created.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timezone
from uuid import UUID

# The production origin is a trusted deployment constant, not caller input.
PRODUCTION_HOST = "wow-governed-probability-engine.onrender.com"


def _verified_origin(origin: str) -> str:
    parsed = urllib.parse.urlsplit(origin)
    if (parsed.scheme != "https" or parsed.hostname != PRODUCTION_HOST
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("PRODUCER_ORIGIN_NOT_APPROVED")
    return "https://" + PRODUCTION_HOST


def _require_deployed_runtime_auth(origin: str, context: ssl.SSLContext) -> None:
    """Safe negative preflight: proves anonymous requests are rejected first.

    The forbidden can_execute=true payload cannot create a run on the older
    vulnerable endpoint (it returned 422). The #2318 endpoint returns 401
    before body validation. We never send a valid anonymous creation request.
    """
    probe = urllib.request.Request(
        origin + "/wow/runs", method="POST",
        data=b'{"run_type":"FULL_MODEL","as_of":"2000-01-01T00:00:00Z","user_timezone":"America/Chicago","can_execute":true}',
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(probe, timeout=10, context=context):
            pass
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return
        raise RuntimeError("PRODUCER_AUTH_PRECONDITION_NOT_DEPLOYED") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("PRODUCER_AUTH_PROBE_TRANSPORT_FAILED") from None
    raise RuntimeError("PRODUCER_AUTH_PRECONDITION_NOT_DEPLOYED")


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
    if not runtime_key or len(runtime_key) < 32:
        raise ValueError("PRODUCER_AUTH_OR_ORIGIN_UNAVAILABLE")
    if not runtime_key.isascii():
        raise ValueError("PRODUCER_CREDENTIAL_INVALID")
    origin = _verified_origin(origin)
    context = ssl.create_default_context()
    _require_deployed_runtime_auth(origin, context)
    key, payload = admission_for_hour(now)
    endpoint = origin + "/wow/runs"
    request = urllib.request.Request(
        endpoint, method="POST", data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + runtime_key,
            "Content-Type": "application/json",
            "Idempotency-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
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
    # The backend #2318 dependency is authoritative for distinct-key scope.
    # Never inject the ordinary scoring Action credential into this workflow.
    token = os.getenv("WOW_AGENT_RUNTIME_API_KEY", "")
    try:
        receipt = admit_cycle(
            now=datetime.now(timezone.utc),
            origin=os.getenv("WOW_ACTION_ORIGIN", ""),
            runtime_key=token,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(receipt, sort_keys=True))
