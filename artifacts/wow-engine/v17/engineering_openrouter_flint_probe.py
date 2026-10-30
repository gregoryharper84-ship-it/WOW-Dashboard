"""Opt-in, non-writing OpenRouter Flint API canary for WOW V17 engineering.

This is NOT an agent, an engineering dispatcher, a sporting scorer, or
production admission. It sends one fixed, nonsensitive synthetic prompt only.
There are no retries, filesystem writes, git calls or wagering operations.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "recursive/flint-v1.0:free"
PROMPT = "Write a one-sentence test case for a Python even-number predicate. Do not use tools."
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


def receipt(status: str, *, attempted: bool = False, usage: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": status,
        "provider": "openrouter",
        "model": MODEL,
        "network_attempted": attempted,
        "agent_activation": False,
        "production_routing_changed": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }
    if isinstance(usage, dict):
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(field)
            if type(value) is int and value >= 0:
                result[field] = value
    return result


def preflight(env: Mapping[str, str]) -> dict[str, Any]:
    """Report configuration without echoing or evaluating the key contents."""
    if env.get("WOW_FLINT_PROBE_ENABLED") != "true":
        return receipt("DISABLED")
    if not env.get("OPENROUTER_API_KEY", "").strip():
        return receipt("KEY_MISSING")
    return receipt("READY_FOR_ONE_SYNTHETIC_REQUEST")


def classify_http_status(status: int) -> str:
    if status in (401, 403):
        return "AUTH_FAILED"
    if status == 429:
        return "RATE_LIMITED"
    if status == 402:
        return "PAYMENT_REQUIRED"
    if status == 404:
        # Endpoint/model/entitlement cannot be attributed from an untrusted error body.
        return "FLINT_HTTP_404_UNATTRIBUTED"
    if status >= 500:
        return "UPSTREAM_FAILURE"
    return "HTTP_FAILURE"


def validate_completion(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return receipt("OUTPUT_INVALID", attempted=True)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return receipt("OUTPUT_INVALID", attempted=True)
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        return receipt("OUTPUT_INVALID", attempted=True)
    if choices[0].get("finish_reason") != "stop":
        return receipt("FLINT_MODEL_COMPLETION_INCOMPLETE", attempted=True)
    return receipt("SMOKE_PASSED", attempted=True, usage=payload.get("usage"))


def smoke(env: Mapping[str, str], *, opener: Any = urllib.request.urlopen) -> dict[str, Any]:
    gate = preflight(env)
    if gate["status"] != "READY_FOR_ONE_SYNTHETIC_REQUEST":
        return gate
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT}],
        "temperature": 0,
        "max_tokens": 96,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Title": "WOW V17 Engineering Flint Opt-In Canary",
        },
        method="POST",
    )
    # Unredirected credentials are not copied to redirect destinations. Clear
    # the header even if the opener captures Request in a raised exception.
    request.add_unredirected_header("Authorization", "Bearer " + env["OPENROUTER_API_KEY"].strip())
    try:
        with opener(request, timeout=30) as response:
            data = response.read(65537)
    except urllib.error.HTTPError as exc:
        # Do not print HTTP error body; it can contain provider/account context.
        return receipt(classify_http_status(exc.code), attempted=True)
    except (urllib.error.URLError, TimeoutError, OSError):
        return receipt("TRANSPORT_FAILURE", attempted=True)
    finally:
        request.remove_header("Authorization")
    if len(data) > 65536:
        return receipt("OUTPUT_INVALID", attempted=True)
    try:
        return validate_completion(json.loads(data))
    except (ValueError, UnicodeDecodeError):
        return receipt("OUTPUT_INVALID", attempted=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "smoke"))
    args = parser.parse_args()
    result = preflight(os.environ) if args.action == "preflight" else smoke(os.environ)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"DISABLED", "READY_FOR_ONE_SYNTHETIC_REQUEST", "SMOKE_PASSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
