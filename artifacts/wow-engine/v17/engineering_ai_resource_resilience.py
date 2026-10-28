"""CISA-aligned, read-only AI resource pressure receipts for the V17 dispatcher.

Observed provider failures and metered token usage are distinct evidence sources.
This code does not dispatch a provider, approve spending, change writer leases,
change sporting probabilities, or authorize an action.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping

FAILURE_CODES = frozenset({
    "OPENAI_API_BILLING_REQUIRED", "OPENAI_API_QUOTA_EXCEEDED",
    "OPENAI_API_RATE_LIMITED", "OPENAI_API_UPSTREAM_UNAVAILABLE",
    "ANTHROPIC_API_BILLING_REQUIRED", "ANTHROPIC_API_CREDIT_EXHAUSTED",
    "ANTHROPIC_API_RATE_LIMITED", "ANTHROPIC_API_UPSTREAM_UNAVAILABLE",
    "GEMINI_API_QUOTA_EXHAUSTED", "GEMINI_API_RATE_LIMITED",
    "GEMINI_API_UPSTREAM_UNAVAILABLE",
})
ALLOWED_PROVIDERS = frozenset({"openai", "anthropic", "gemini", "cursor"})
PRESSURE_LEVELS = ((90, "CRITICAL"), (80, "WARNING"), (70, "INFORMATIONAL"))


def _parse_timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def _observed_failure(reason: Any) -> str | None:
    # Never serialize arbitrary worker stderr, the raw reason, or credentials.
    if not isinstance(reason, str):
        return None
    for piece in reason.split(":"):
        if piece in FAILURE_CODES:
            return piece
    return None


def assess_resource_pressure(
    route: Mapping[str, Any],
    *,
    usage: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Only fresh, numeric, same-provider usage can produce an advisory band.

    Usage is intentionally advisory until its provenance is independently
    attested. Missing data is UNKNOWN, never treated as zero token consumption.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if (
        not isinstance(route, Mapping)
        or route.get("can_execute") is not False
        or route.get("terminal_authority") != "V17_TERMINAL_REDUCER"
        or route.get("action") not in {"DISPATCH", "SURVIVAL", "NO_ACTION"}
    ):
        raise ValueError("untrusted V17 route receipt")

    action = route["action"]
    provider = route.get("provider")
    if (
        (action == "DISPATCH" and provider not in ALLOWED_PROVIDERS)
        or (action == "SURVIVAL" and provider != "deterministic")
        or (action == "NO_ACTION" and provider != "none")
    ):
        raise ValueError("route provider/action mismatch")

    observed_failure = _observed_failure(route.get("reason"))
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "route_action": action,
        "selected_provider": provider,
        "observed_provider_failure_code": observed_failure,
        "usage_status": "UNAVAILABLE",
        "usage_provenance": "NONE",
        "pressure_band": "UNKNOWN",
        "token_utilization_pct": None,
        "advisory_action": "PRESERVE_EXISTING_DISPATCH",
        "may_override_dispatch": False,
        "may_reset_circuit_breaker": False,
        "may_release_writer_lease": False,
        "may_approve_spend": False,
        "probability_authority": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }

    if usage is None:
        return receipt
    if not isinstance(usage, Mapping):
        receipt["usage_status"] = "INVALID"
        return receipt
    if action != "DISPATCH" or usage.get("provider") != provider:
        receipt["usage_status"] = "PROVIDER_MISMATCH"
        return receipt

    used, limit = usage.get("used_tokens"), usage.get("limit_tokens")
    captured = _parse_timestamp(usage.get("captured_at"))
    if (
        type(used) is not int or type(limit) is not int
        or used < 0 or limit <= 0 or captured is None
    ):
        receipt["usage_status"] = "INVALID"
        return receipt
    age = now.astimezone(timezone.utc) - captured
    if age < timedelta(seconds=-60) or age > timedelta(minutes=15):
        receipt["usage_status"] = "STALE_OR_FUTURE"
        return receipt

    ratio = used * 100.0 / limit
    band = next((name for threshold, name in PRESSURE_LEVELS if ratio >= threshold), "NORMAL")
    receipt.update({
        "usage_status": "FRESH_REPORTED_NOT_ATTESTED",
        "usage_provenance": "CALLER_SUPPLIED_METER",
        "pressure_band": band,
        "token_utilization_pct": round(ratio, 2),
        "advisory_action": (
            "DEFER_OPTIONAL_WORK_RETAIN_P0_RECOVERY"
            if band in {"WARNING", "CRITICAL"}
            else "PRESERVE_EXISTING_DISPATCH"
        ),
    })
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route-receipt", type=Path, required=True)
    parser.add_argument("--usage-receipt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    route = json.loads(args.route_receipt.read_text(encoding="utf-8"))
    usage = (json.loads(args.usage_receipt.read_text(encoding="utf-8"))
             if args.usage_receipt else None)
    result = assess_resource_pressure(route, usage=usage)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"AI resource pressure: {result['pressure_band']}; usage: {result['usage_status']}")


if __name__ == "__main__":
    main()
