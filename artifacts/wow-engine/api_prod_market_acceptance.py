"""Startup-only live acceptance wrapper for the governed prop API.

This module reuses ``api_prod_market.app`` unchanged and adds one background
self-acceptance probe. The probe authenticates through the real /score-prop
boundary with the existing WOW_ACTION_API_KEY, submits symmetric whole-number
MORE/LESS requests against a deliberately nonexistent evidence snapshot, and
requires the endpoint to fail closed before specialist/model invocation.

It never logs credentials or response bodies, never writes a prediction, never
publishes a probability, and never changes ``can_execute=false``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

import api_prod_market as market_api

app = market_api.app

_logger = logging.getLogger("wow.prop.acceptance")
_background_tasks: set[asyncio.Task] = set()


def _probe_payload(direction: str) -> dict[str, Any]:
    return {
        "event_id": "WOW:PROP:LIVE:SELF_ACCEPTANCE:NO_EVIDENCE",
        "event_start_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "sport": "MLB",
        "player": "WOW Live Self Acceptance",
        "stat_type": "STRIKEOUTS",
        "line": 4.0,
        "direction": direction,
        "source_snapshot_id": str(uuid.uuid4()),
        "money_lane_status": "PAYOUT_UNRESOLVED",
    }


def _result_body(response: httpx.Response) -> dict[str, Any] | None:
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    return detail if isinstance(detail, dict) else body


def _numeric_probability_paths(value: Any, prefix: str = "") -> list[str]:
    """Find numeric probability-shaped fields while excluding booleans."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if (
                "probability" in str(key).lower()
                and isinstance(child, (int, float))
                and not isinstance(child, bool)
            ):
                found.append(path)
            found.extend(_numeric_probability_paths(child, path))
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            found.extend(_numeric_probability_paths(child, f"{prefix}[{idx}]"))
    return found


def _validate_probe_response(response: httpx.Response) -> tuple[bool, str, list[str]]:
    body = _result_body(response)
    if body is None:
        return False, "INVALID_BODY", []

    code = str(body.get("code", "MISSING_CODE"))
    leaked = _numeric_probability_paths(body)
    ok = (
        response.status_code == 422
        and code == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
        and body.get("failure_class") == "RUN_INVALID_ACQUISITION_INCOMPLETE"
        and body.get("specialist_invoked") is False
        and body.get("probability_publishable") is False
        and body.get("can_execute") is False
        and not leaked
    )
    return ok, code, leaked


async def _run_prop_live_self_acceptance() -> None:
    key = os.getenv("WOW_ACTION_API_KEY")
    port = os.getenv("PORT")
    if not key or not port:
        _logger.error("WOW_PROP_SELF_ACCEPTANCE result=FAIL reason=RUNTIME_AUTH_OR_PORT_MISSING")
        return

    url = f"http://127.0.0.1:{port}/score-prop"
    for direction in ("MORE", "LESS"):
        last_status: int | None = None
        last_code = "NO_RESPONSE"
        last_leaks: list[str] = []
        passed = False

        for attempt in range(1, 6):
            await asyncio.sleep(2.0 if attempt == 1 else 1.0)
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {key}",
                            "X-WOW-Model-Identity": "WOW_BETTING_ENGINE",
                        },
                        json=_probe_payload(direction),
                    )
                last_status = response.status_code
                passed, last_code, last_leaks = _validate_probe_response(response)
                if passed:
                    _logger.warning(
                        "WOW_PROP_SELF_ACCEPTANCE result=PASS direction=%s status=422 code=%s "
                        "auth=PASS acquisition_fail_closed=PASS specialist_invoked=false "
                        "probability_publishable=false can_execute=false numeric_probability_fields=0",
                        direction,
                        last_code,
                    )
                    break
            except Exception as exc:
                last_code = type(exc).__name__

        if not passed:
            _logger.error(
                "WOW_PROP_SELF_ACCEPTANCE result=FAIL direction=%s status=%s code=%s "
                "numeric_probability_fields=%s",
                direction,
                last_status,
                last_code,
                len(last_leaks),
            )
            return

    _logger.warning(
        "WOW_PROP_SELF_ACCEPTANCE result=PASS directions=MORE,LESS auth=PASS "
        "acquisition_fail_closed=PASS specialist_invoked=false zero_probability_leak=true "
        "settlement_math=NOT_PROVEN model_path=NOT_PROVEN can_execute=false"
    )


@app.on_event("startup")
async def _schedule_prop_live_self_acceptance() -> None:
    if os.getenv("WOW_PROP_SELF_ACCEPTANCE", "0") != "1":
        return
    task = asyncio.create_task(_run_prop_live_self_acceptance())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
