"""Sanitized, non-authoritative diagnostics for governed Claude CI failures.

Only the trusted action's JSON execution file is read.  Never print the model
result, provider response body, tokens, workspace, endpoint, file path or secrets.
This helper observes failures; it never mints review or release receipts.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

MAX_BYTES = 1_048_576
MAX_EVENTS = 2048
HTTP_CLASS = {
    400: "REQUEST_REJECTED",
    401: "AUTHENTICATION_REJECTED",
    403: "PERMISSION_OR_WORKSPACE_REJECTED",
    408: "PROVIDER_TIMEOUT",
    409: "PROVIDER_CONFLICT",
    429: "QUOTA_OR_RATE_LIMIT",
    500: "PROVIDER_SERVER_FAILURE",
    502: "PROVIDER_SERVER_FAILURE",
    503: "PROVIDER_CAPACITY",
    504: "PROVIDER_TIMEOUT",
    529: "PROVIDER_CAPACITY",
}


def _entries(raw: str) -> list[Mapping[str, Any]]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, Mapping):
        parsed = [parsed]
    if isinstance(parsed, list):
        return [x for x in parsed[:MAX_EVENTS] if isinstance(x, Mapping)]
    out: list[Mapping[str, Any]] = []
    for line in raw.splitlines()[:MAX_EVENTS]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, Mapping):
            out.append(item)
    return out


def inspect_execution_file(path: str, *, runner_temp: str) -> str:
    """Return only a typed reason; never expose content outside the sandbox."""
    if not path or not runner_temp:
        return "EXECUTION_FILE_NOT_PROVIDED"
    try:
        root = Path(runner_temp).resolve(strict=True)
        candidate = Path(path).resolve(strict=True)
        if not candidate.is_relative_to(root) or not candidate.is_file():
            return "EXECUTION_FILE_NOT_TRUSTED"
        if candidate.stat().st_size > MAX_BYTES:
            return "EXECUTION_FILE_OVERSIZE"
        records = _entries(candidate.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return "EXECUTION_FILE_UNREADABLE"
    for obj in reversed(records):
        if obj.get("type") != "result":
            continue
        if obj.get("is_error") is not True:
            return "SDK_RESULT_NOT_MARKED_ERROR"
        status = obj.get("api_error_status")
        # Per SDK this is an integer or null, NEVER coerce a model string.
        if type(status) is int and 400 <= status <= 599:
            return f"PROVIDER_HTTP_{status}_{HTTP_CLASS.get(status, 'HTTP_REJECTED')}"
        return "SDK_RESULT_IS_ERROR_HTTP_UNAVAILABLE"
    return "SDK_RESULT_EVENT_MISSING"


def main() -> int:
    attempt = os.environ.get("CLAUDE_ATTEMPT", "")
    # The label is an allowlist, not echoed from provider or prompt text.
    label = attempt if attempt in {"API_KEY", "OAUTH"} else "UNKNOWN"
    reason = inspect_execution_file(
        os.environ.get("CLAUDE_EXECUTION_FILE", ""),
        runner_temp=os.environ.get("RUNNER_TEMP", ""),
    )
    print(f"::warning title=WOW Claude diagnostic::{label}:{reason}")
    # This diagnostic is never a gate or a successful provider result.
    return 0


if __name__ == "__main__":
    sys.exit(main())
