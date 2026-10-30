"""External, read-only health wakeup for the isolated Kalshi Weather shadow API.

A successful probe proves the API, Supabase read and collector wiring were
reachable at the probe time. It does NOT prove a scheduled collector pass ran,
settlements are complete, model calibration, or trade eligibility.

This script deliberately sends GET requests only. It carries no bearer key,
changes no state, and does not trigger a second weather collector.
"""
from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Mapping
from typing import Any
from urllib.request import Request, urlopen

BASE_URL = "https://wow-kalshi-weather-v2.onrender.com"
READ_LIMIT_BYTES = 16384


class ProbeError(RuntimeError):
    """A fixed code, never a raw response body or exception text."""


def read_json(path: str) -> dict[str, Any]:
    if path not in ("/health", "/collector/readiness"):
        raise ProbeError("PROBE_PATH_NOT_ALLOWLISTED")
    req = Request(
        BASE_URL + path,
        headers={"Accept": "application/json", "User-Agent": "wow-weather-external-wake/1"},
        method="GET",
    )
    with urlopen(req, timeout=80) as response:
        if response.status != 200:
            raise ProbeError("PROBE_HTTP_STATUS_UNEXPECTED")
        payload = response.read(READ_LIMIT_BYTES + 1)
        if len(payload) > READ_LIMIT_BYTES:
            raise ProbeError("PROBE_RESPONSE_TOO_LARGE")
        if not response.geturl().startswith(BASE_URL + "/"):
            raise ProbeError("PROBE_RESPONSE_ORIGIN_CHANGED")
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProbeError("PROBE_NON_JSON_RESPONSE") from exc
    if not isinstance(value, dict):
        raise ProbeError("PROBE_RESPONSE_SHAPE_INVALID")
    return value


def verify(health: Mapping[str, Any], readiness: Mapping[str, Any]) -> None:
    if health.get("service") != "KALSHI_WEATHER_V2" or health.get("status") != "ok":
        raise ProbeError("WEATHER_API_IDENTITY_UNVERIFIED")
    if health.get("database_status") != "AVAILABLE" or health.get("database_diagnostic") != "DATABASE_READ_VERIFIED":
        raise ProbeError("WEATHER_DATABASE_READ_NOT_VERIFIED")
    if readiness.get("mode") != "SHADOW" or readiness.get("trigger_installed") is not True or readiness.get("startup_enabled") is not True:
        raise ProbeError("WEATHER_COLLECTOR_NOT_CONFIGURED")
    if any(v.get("can_execute") is not False or v.get("probability_publishable") is not False for v in (health, readiness)):
        raise ProbeError("WEATHER_PROBABILITY_SAFETY_INVALID")


def run(
    *,
    reader: Callable[[str], dict[str, Any]] = read_json,
    pause: Callable[[float], None] = time.sleep,
    attempts: int = 3,
) -> bool:
    if attempts < 1 or attempts > 3:
        raise ValueError("attempts must be 1..3")
    for attempt in range(1, attempts + 1):
        try:
            health = reader("/health")
            readiness = reader("/collector/readiness")
            verify(health, readiness)
            print("WARM_HEALTH_VERIFIED: isolated API, Supabase ledger read and collector attachment; independent probability and trade execution remain disabled")
            return True
        except Exception as exc:
            # Never log response bodies, URL query strings or credential values.
            code = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
            print(f"WEATHER_WAKE_ATTEMPT_FAILED:{attempt}:{code}", file=sys.stderr)
            if attempt < attempts:
                pause(8.0)
    print("WEATHER_WAKE_FAILED: inspect Render deploy, runtime logs and Supabase evidence freshness", file=sys.stderr)
    return False


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
