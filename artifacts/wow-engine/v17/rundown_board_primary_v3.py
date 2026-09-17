"""Rate-aware wrapper for the V17 TheRundown board-primary adapter.

TheRundown documents tier-specific per-second ceilings and a ``Retry-After``
header on burst throttles.  This wrapper keeps the v2 board-normalization logic
unchanged while replacing its HTTP primitive with one that:

* spaces requests at a conservative one-request-per-second baseline;
* retries short burst 429s using ``Retry-After`` with bounded exponential
  backoff;
* distinguishes daily/monthly data-point exhaustion from burst throttling;
* carries usage metadata in typed failure payloads without exposing the key;
* never changes probability/execution authority (``can_execute=false``).

The conservative baseline is deliberately safe for every documented API tier.
Higher-tier optimization can later use returned rate-limit metadata without
changing the Scout acquisition contract.
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from v17 import rundown_board_primary_v2 as impl

# Safe for the documented Free-tier 1 req/sec ceiling and therefore every paid
# tier.  A small margin avoids boundary jitter across hosted runners.
impl.REQUEST_DELAY_SECONDS = max(
    float(os.environ.get("WOW_RUNDOWN_BOARD_REQUEST_DELAY_SECONDS", "1.10")),
    1.02,
)

MAX_BURST_RETRIES = max(0, int(os.environ.get("WOW_RUNDOWN_BOARD_BURST_RETRIES", "4")))
MAX_RETRY_AFTER_SECONDS = max(1.0, float(os.environ.get("WOW_RUNDOWN_BOARD_MAX_RETRY_AFTER_SECONDS", "15")))


def _header_int(headers: Any, name: str) -> int | None:
    try:
        value = headers.get(name)
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError, AttributeError):
        return None


def _usage(headers: Any) -> dict[str, Any]:
    if headers is None:
        return {}
    result: dict[str, Any] = {}
    for header, key in (
        ("X-Datapoints", "datapoints"),
        ("X-Datapoints-Remaining", "datapoints_remaining"),
        ("X-Datapoints-Reset", "datapoints_reset"),
        ("X-Datapoints-Monthly-Reset", "datapoints_monthly_reset"),
    ):
        value = headers.get(header)
        if value not in (None, ""):
            result[key] = value
    return result


def _decode_error(exc: HTTPError) -> tuple[dict[str, Any], str]:
    try:
        raw = exc.read().decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            payload = {"raw": str(payload)[:500]}
    except Exception:
        payload = {}
    message = str(payload.get("error") or payload.get("message") or payload.get("code") or "")
    return payload, message


def _rate_aware_api_get(path: str, params: dict[str, Any] | None = None) -> impl.BoardResult:
    key = impl._credential()
    if not key:
        return impl.BoardResult(False, status=401, code="RUNDOWN_BOARD_AUTH_UNCONFIGURED")

    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = f"{impl.BASE_URL}{path}" + (f"?{query}" if query else "")

    for attempt in range(MAX_BURST_RETRIES + 1):
        impl._pace()
        request = Request(
            url,
            headers={
                "X-TheRundown-Key": key,
                "Accept": "application/json",
                "User-Agent": "WOW-V17-Scout-BoardMirror/3.0",
            },
        )
        try:
            with urlopen(request, timeout=impl.TIMEOUT_SECONDS) as response:
                data = json.loads(response.read().decode("utf-8"))
                return impl.BoardResult(True, data, response.status)
        except HTTPError as exc:
            payload, message = _decode_error(exc)
            lower = message.lower()
            usage = _usage(exc.headers)

            if exc.code in {401, 403}:
                return impl.BoardResult(
                    False,
                    data={"provider_message": message[:300], "usage": usage},
                    status=exc.code,
                    code="RUNDOWN_BOARD_AUTH_OR_ENTITLEMENT_REJECTED",
                )

            if exc.code == 429:
                if "daily" in lower and ("limit" in lower or "cap" in lower):
                    return impl.BoardResult(
                        False,
                        data={"provider_message": message[:300], "usage": usage},
                        status=429,
                        code="RUNDOWN_BOARD_DAILY_DATAPOINT_CAP_REACHED",
                    )
                if "monthly" in lower and ("limit" in lower or "cap" in lower):
                    return impl.BoardResult(
                        False,
                        data={"provider_message": message[:300], "usage": usage},
                        status=429,
                        code="RUNDOWN_BOARD_MONTHLY_DATAPOINT_CAP_REACHED",
                    )

                # Short burst throttle: provider documents Retry-After, usually 1.
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(retry_after) if retry_after not in (None, "") else float(2 ** attempt)
                except ValueError:
                    wait = float(2 ** attempt)
                wait = min(MAX_RETRY_AFTER_SECONDS, max(1.05, wait))
                wait += random.uniform(0.05, 0.20)
                if attempt < MAX_BURST_RETRIES:
                    time.sleep(wait)
                    continue
                return impl.BoardResult(
                    False,
                    data={"provider_message": message[:300], "usage": usage, "retry_after": retry_after},
                    status=429,
                    code="RUNDOWN_BOARD_BURST_RATE_LIMIT_RETRIES_EXHAUSTED",
                )

            code = str(payload.get("code") or payload.get("error") or payload.get("message") or f"RUNDOWN_BOARD_HTTP_{exc.code}")
            return impl.BoardResult(
                False,
                data={"provider_message": message[:300], "usage": usage},
                status=exc.code,
                code=code,
            )
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            return impl.BoardResult(False, code=f"RUNDOWN_BOARD_{type(exc).__name__}")

    return impl.BoardResult(False, status=429, code="RUNDOWN_BOARD_BURST_RATE_LIMIT_RETRIES_EXHAUSTED")


# Install the rate-aware primitive into the implementation module so all v2
# functions use it through their module globals.
impl._api_get = _rate_aware_api_get

# Re-export the stable adapter surface.
BoardResult = impl.BoardResult
CAN_EXECUTE = impl.CAN_EXECUTE
MARKET_CHUNK_SIZE = impl.MARKET_CHUNK_SIZE
REQUEST_DELAY_SECONDS = impl.REQUEST_DELAY_SECONDS
enabled = impl.enabled
reset_cache = impl.reset_cache
serve = impl.serve
sports_catalog = impl.sports_catalog

__all__ = [
    "BoardResult",
    "CAN_EXECUTE",
    "MARKET_CHUNK_SIZE",
    "REQUEST_DELAY_SECONDS",
    "enabled",
    "reset_cache",
    "serve",
    "sports_catalog",
]
