"""Continuous product-runtime reliability probes for WOW user-facing expert surfaces.

Safe by construction:
- read-only health/governance/host-contract probes;
- deliberately invalid scorer requests that must fail before probability scoring;
- no wager/order execution;
- no probability substitution;
- no secret material emitted in receipts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request

WOW_ORIGIN = "https://wow-governed-probability-engine.onrender.com"
LLP_GATEWAY_ORIGIN = "https://iczfhsmjrrafhvcpmqhr.supabase.co/functions/v1/wow-llp-action-gateway"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RUNTIME_GENERATION = "V17_ACTIVE"
CAN_EXECUTE = False
SURFACES = ("WOW_BETTING_ENGINE", "LLP_TEAM_BETTING_ENGINE", "KALSHI_WEATHER_MARKET_EXPERT")


def _http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any] | list[Any] | None]:
    headers = {"Accept": "application/json", "User-Agent": "WOW-V17-Product-Runtime-Reliability/1.0"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    req = request.Request(url=url, data=body, method=method.upper(), headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read()
    except error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read()
    except Exception as exc:  # network errors stay typed and secret-free
        return 0, {"_transport_error": type(exc).__name__}
    if not raw:
        return status, None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {"_non_json": True}
    return status, decoded


def _dict(payload: Any) -> dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _authority(payload: dict[str, Any]) -> str | None:
    return payload.get("terminal_authority") or payload.get("global_terminal_authority")


def _assert_invariants(payload: dict[str, Any], failures: list[str], prefix: str) -> None:
    if payload.get("can_execute") is not False:
        failures.append(f"{prefix}_CAN_EXECUTE_INVARIANT_FAILED")
    authority = _authority(payload)
    if authority is not None and authority != TERMINAL_AUTHORITY:
        failures.append(f"{prefix}_TERMINAL_AUTHORITY_MISMATCH")
    generation = payload.get("runtime_generation")
    if generation is not None and generation != RUNTIME_GENERATION:
        failures.append(f"{prefix}_RUNTIME_GENERATION_MISMATCH")


def _surface_receipt(surface: str, checks: dict[str, Any], failures: list[str]) -> dict[str, Any]:
    return {
        "surface": surface,
        "status": "PASS" if not failures else "BLOCKED_WITH_EXACT_REASON",
        "checks": checks,
        "blockers": list(dict.fromkeys(failures)),
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def probe_wow(action_key: str | None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    failures: list[str] = []

    code, payload = _http_json("GET", f"{WOW_ORIGIN}/health/live")
    checks["backend_health_http"] = code
    if code != 200:
        failures.append(f"WOW_BACKEND_HEALTH_HTTP_{code or 'TRANSPORT'}")

    code, payload = _http_json("GET", f"{WOW_ORIGIN}/governance")
    checks["governance_http"] = code
    if code != 200:
        failures.append(f"WOW_GOVERNANCE_HTTP_{code or 'TRANSPORT'}")
    else:
        _assert_invariants(_dict(payload), failures, "WOW_GOVERNANCE")

    if not action_key:
        failures.append("WOW_ACTION_API_KEY_UNAVAILABLE")
        checks["host_contract_http"] = None
        checks["safe_invalid_score_http"] = None
    else:
        code, payload = _http_json("GET", f"{WOW_ORIGIN}/v17/host-contract", token=action_key)
        checks["host_contract_http"] = code
        if code != 200:
            failures.append(f"WOW_HOST_CONTRACT_HTTP_{code or 'TRANSPORT'}")
        else:
            _assert_invariants(_dict(payload), failures, "WOW_HOST_CONTRACT")

        code, payload = _http_json("POST", f"{WOW_ORIGIN}/score-pick-request", token=action_key, payload={})
        checks["safe_invalid_score_http"] = code
        if code != 422:
            failures.append(f"WOW_SAFE_INVALID_SCORE_EXPECTED_422_GOT_{code or 'TRANSPORT'}")
        body = _dict(payload)
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
        if isinstance(detail, dict) and detail.get("can_execute") is True:
            failures.append("WOW_SAFE_INVALID_SCORE_CAN_EXECUTE_TRUE")

    return _surface_receipt("WOW_BETTING_ENGINE", checks, failures)


def probe_llp(action_key: str | None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    failures: list[str] = []

    code, payload = _http_json("GET", f"{LLP_GATEWAY_ORIGIN}/health")
    checks["gateway_health_http"] = code
    if code != 200:
        failures.append(f"LLP_GATEWAY_HEALTH_HTTP_{code or 'TRANSPORT'}")
    else:
        _assert_invariants(_dict(payload), failures, "LLP_GATEWAY_HEALTH")

    if not action_key:
        failures.append("WOW_ACTION_API_KEY_UNAVAILABLE")
        checks["host_contract_http"] = None
        checks["safe_invalid_score_http"] = None
    else:
        code, payload = _http_json("GET", f"{LLP_GATEWAY_ORIGIN}/v17/host-contract", token=action_key)
        checks["host_contract_http"] = code
        if code != 200:
            failures.append(f"LLP_HOST_CONTRACT_HTTP_{code or 'TRANSPORT'}")
        else:
            _assert_invariants(_dict(payload), failures, "LLP_HOST_CONTRACT")

        code, payload = _http_json("POST", f"{LLP_GATEWAY_ORIGIN}/score-team-event", token=action_key, payload={})
        checks["safe_invalid_score_http"] = code
        if code != 422:
            failures.append(f"LLP_SAFE_INVALID_SCORE_EXPECTED_422_GOT_{code or 'TRANSPORT'}")

    return _surface_receipt("LLP_TEAM_BETTING_ENGINE", checks, failures)


def probe_kalshi_weather(action_key: str | None) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    failures: list[str] = []

    code, payload = _http_json("GET", f"{WOW_ORIGIN}/kalshi-weather/v17/governance")
    checks["governance_http"] = code
    governance = _dict(payload)
    if code != 200:
        failures.append(f"KALSHI_WEATHER_GOVERNANCE_HTTP_{code or 'TRANSPORT'}")
    else:
        _assert_invariants(governance, failures, "KALSHI_WEATHER_GOVERNANCE")
        if governance.get("service") != "KALSHI_WEATHER_MARKET_EXPERT":
            failures.append("KALSHI_WEATHER_CONTROLLING_SPECIALIST_MISMATCH")

    if not action_key:
        failures.append("WOW_ACTION_API_KEY_UNAVAILABLE")
        checks["free_sources_http"] = None
        checks["safe_unsupported_lane_http"] = None
    else:
        code, payload = _http_json("GET", f"{WOW_ORIGIN}/kalshi-weather/v17/free-sources", token=action_key)
        checks["free_sources_http"] = code
        if code != 200:
            failures.append(f"KALSHI_WEATHER_FREE_SOURCES_HTTP_{code or 'TRANSPORT'}")
        else:
            _assert_invariants(_dict(payload), failures, "KALSHI_WEATHER_FREE_SOURCES")

        code, payload = _http_json(
            "POST",
            f"{WOW_ORIGIN}/kalshi-weather/v17/analyze",
            token=action_key,
            payload={"lane": "SYNTHETIC_RELIABILITY_PROBE", "ticker": "SYNTHETIC_ONLY"},
        )
        checks["safe_unsupported_lane_http"] = code
        result = _dict(payload)
        if code != 200:
            failures.append(f"KALSHI_WEATHER_SAFE_PROBE_HTTP_{code or 'TRANSPORT'}")
        else:
            if result.get("code") != "WEATHER_LANE_RUNTIME_NOT_CERTIFIED":
                failures.append("KALSHI_WEATHER_SAFE_PROBE_TYPED_FAILURE_MISMATCH")
            if result.get("controlling_specialist") != "KALSHI_WEATHER_MARKET_EXPERT":
                failures.append("KALSHI_WEATHER_SAFE_PROBE_SPECIALIST_MISMATCH")
            if result.get("probability_publishable") is not False:
                failures.append("KALSHI_WEATHER_SAFE_PROBE_PROBABILITY_PUBLISHABLE")
            if result.get("p_yes") is not None or result.get("p_no") is not None:
                failures.append("KALSHI_WEATHER_SAFE_PROBE_PROBABILITY_LEAK")
            if result.get("can_execute") is not False:
                failures.append("KALSHI_WEATHER_SAFE_PROBE_CAN_EXECUTE_INVARIANT_FAILED")

    return _surface_receipt("KALSHI_WEATHER_MARKET_EXPERT", checks, failures)


def editor_contract_state(root: Path) -> dict[str, dict[str, Any]]:
    contract_path = root / "artifacts" / "wow-engine" / "v17" / "custom_engine_alignment_contract.json"
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    rows = payload.get("editor_attestation") if isinstance(payload.get("editor_attestation"), dict) else {}
    out: dict[str, dict[str, Any]] = {}
    for surface in SURFACES:
        row = rows.get(surface) if isinstance(rows.get(surface), dict) else {}
        status = str(row.get("status") or "MISSING")
        required = bool(row.get("required"))
        if surface == "KALSHI_WEATHER_MARKET_EXPERT":
            healthy = status == "IN_PROCESS_SPECIALIST_NO_EXTERNAL_EDITOR_SYNC_REQUIRED"
        else:
            healthy = (not required) or status == "LIVE_EDITOR_SYNC_VERIFIED"
        out[surface] = {
            "required": required,
            "status": status,
            "healthy": healthy,
        }
    return out


def build_receipt(*, action_key: str | None, root: Path) -> dict[str, Any]:
    surfaces = {
        "WOW_BETTING_ENGINE": probe_wow(action_key),
        "LLP_TEAM_BETTING_ENGINE": probe_llp(action_key),
        "KALSHI_WEATHER_MARKET_EXPERT": probe_kalshi_weather(action_key),
    }
    editor = editor_contract_state(root)
    for surface, state in editor.items():
        surfaces[surface]["editor_contract"] = state
        if not state["healthy"]:
            surfaces[surface]["status"] = "BLOCKED_WITH_EXACT_REASON"
            surfaces[surface]["blockers"] = list(dict.fromkeys(
                list(surfaces[surface].get("blockers") or [])
                + [f"{surface}_LIVE_EDITOR_SYNC_NOT_VERIFIED:{state['status']}"]
            ))

    overall = "PASS" if all(row["status"] == "PASS" for row in surfaces.values()) else "BLOCKED_WITH_EXACT_REASON"
    return {
        "schema_version": "WOW_V17_PRODUCT_RUNTIME_RELIABILITY_V1",
        "status": overall,
        "surfaces": surfaces,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[3]))
    args = parser.parse_args()

    receipt = build_receipt(
        action_key=os.environ.get("WOW_ACTION_API_KEY") or None,
        root=Path(args.root),
    )
    Path(args.output).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt["status"],
        "surface_statuses": {k: v["status"] for k, v in receipt["surfaces"].items()},
        "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
