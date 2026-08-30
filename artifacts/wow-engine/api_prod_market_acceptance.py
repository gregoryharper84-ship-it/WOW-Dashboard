"""Startup-only live acceptance wrapper for the governed prop API.

This module reuses ``api_prod_market.app`` and adds two narrow production
controls:

1. a startup self-acceptance probe that authenticates through the real
   /score-prop boundary and proves acquisition fail-closed behavior without
   emitting a probability; and
2. a settlement boundary that derives MORE/LESS hit and push truth from the
   frozen ``wow_predictions`` row instead of trusting caller-supplied outcome
   math.

Neither control can enable execution. ``can_execute`` remains false.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from fastapi import Depends, HTTPException

import api_prod_market as market_api
from ledger import record_outcome
from prop_settlement import settlement_self_acceptance

app = market_api.app

_logger = logging.getLogger("wow.prop.acceptance")
_background_tasks: set[asyncio.Task] = set()


# The inherited legacy /settle route accepted a caller-provided hit boolean.
# Production must not trust settlement math supplied by the caller. Remove that
# route from this production wrapper and replace it below with a boundary that
# derives hit/push from the immutable prediction direction and line.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if not (
        getattr(route, "path", None) == "/settle"
        and "POST" in (getattr(route, "methods", set()) or set())
    )
]


def _as_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_SETTLEMENT_VALUE_INVALID",
                "field": field,
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc
    if not parsed.is_finite():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_SETTLEMENT_VALUE_INVALID",
                "field": field,
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return parsed


def _derive_prop_settlement(direction: str, line: Any, actual_stat: Any) -> dict[str, Any]:
    side = str(direction or "").strip().upper()
    if side not in {"MORE", "LESS"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROP_SETTLEMENT_DIRECTION_INVALID",
                "direction": side or None,
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    frozen_line = _as_decimal(line, "line")
    actual = _as_decimal(actual_stat, "actual_stat")
    push = actual == frozen_line
    if push:
        hit = None
    elif side == "MORE":
        hit = actual > frozen_line
    else:
        hit = actual < frozen_line

    return {
        "direction": side,
        "line": float(frozen_line),
        "actual_stat": float(actual),
        "hit": hit,
        "push": push,
    }


def _load_prediction_for_settlement(prediction_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(str(prediction_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_PREDICTION_ID_INVALID",
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    try:
        result = (
            market_api.prod.get_client()
            .table("wow_predictions")
            .select("prediction_id,event_id,event_start_time,sport,stat_type,line,direction,source_snapshot_id")
            .eq("prediction_id", str(prediction_id))
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PROP_PREDICTION_LEDGER_UNAVAILABLE",
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    rows = result.data or []
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PROP_PREDICTION_NOT_FOUND",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return dict(rows[0])


def _assert_not_already_settled(prediction_id: str) -> None:
    try:
        result = (
            market_api.prod.get_client()
            .table("wow_outcomes")
            .select("outcome_id,prediction_id")
            .eq("prediction_id", str(prediction_id))
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PROP_OUTCOME_LEDGER_UNAVAILABLE",
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    if result.data:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROP_PREDICTION_ALREADY_SETTLED",
                "probability_publishable": False,
                "can_execute": False,
            },
        )


@app.post(
    "/settle",
    dependencies=[Depends(market_api.prod._require_action_api_key)],
    operation_id="settleWowProp",
)
def settle_prop(prediction_id: str, official_result: str, actual_stat: float):
    """Settle one governed prop from frozen prediction identity and actual stat.

    The caller supplies only the official realized statistic and its source
    label. Direction, line, hit and push are backend-owned derivations.
    """
    prediction = _load_prediction_for_settlement(prediction_id)
    _assert_not_already_settled(prediction_id)
    derived = _derive_prop_settlement(
        prediction.get("direction"), prediction.get("line"), actual_stat
    )
    settlement_timestamp = datetime.now(timezone.utc).isoformat()
    persisted = record_outcome(
        prediction_id,
        official_result=official_result,
        actual_stat=derived["actual_stat"],
        hit=derived["hit"],
        push=derived["push"],
        settlement_timestamp=settlement_timestamp,
    )
    return {
        "ok": True,
        "prediction_id": prediction_id,
        "event_id": prediction.get("event_id"),
        "sport": prediction.get("sport"),
        "stat_type": prediction.get("stat_type"),
        "direction": derived["direction"],
        "line": derived["line"],
        "actual_stat": derived["actual_stat"],
        "hit": derived["hit"],
        "push": derived["push"],
        "settlement_math": "PROVEN_BACKEND_DERIVED",
        "outcome": persisted,
        "can_execute": False,
    }


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

    settlement_math = "PROVEN" if settlement_self_acceptance() else "FAILED"
    if settlement_math != "PROVEN":
        _logger.error("WOW_PROP_SELF_ACCEPTANCE result=FAIL settlement_math=FAILED can_execute=false")
        return
    _logger.warning(
        "WOW_PROP_SELF_ACCEPTANCE result=PASS directions=MORE,LESS auth=PASS "
        "acquisition_fail_closed=PASS specialist_invoked=false zero_probability_leak=true "
        "settlement_math=PROVEN model_path=NOT_PROVEN_IN_THIS_PROBE can_execute=false"
    )


@app.on_event("startup")
async def _schedule_prop_live_self_acceptance() -> None:
    if os.getenv("WOW_PROP_SELF_ACCEPTANCE", "0") != "1":
        return
    task = asyncio.create_task(_run_prop_live_self_acceptance())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
