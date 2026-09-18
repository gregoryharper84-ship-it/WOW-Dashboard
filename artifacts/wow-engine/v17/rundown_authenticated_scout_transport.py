"""Auth-aware transport shim for the TheRundown full-slate Scout.

This keeps the collector's normalization/routing contract unchanged while
making first-party JSON requests tolerant of the short post-login propagation
window observed in GitHub Actions. No credentials, cookies, or response bodies
are logged here. can_execute remains false in the underlying collector.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import APIResponse

from v17 import rundown_authenticated_scout as scout

RETRYABLE_AUTH_STATUSES = {401, 403}
AUTH_RETRY_ATTEMPTS = max(2, int(os.getenv("WOW_RUNDOWN_AUTH_RETRY_ATTEMPTS", "4")))
AUTH_RETRY_DELAY_MS = max(250, int(os.getenv("WOW_RUNDOWN_AUTH_RETRY_DELAY_MS", "1250")))


def _auth_aware_api_json(context, path: str, params: dict[str, Any] | None = None):
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = "https://therundown.io" + path + ("?" + query if query else "")
    last_status = 0
    last_code = "RUNDOWN_REQUEST_FAILED"

    for attempt in range(AUTH_RETRY_ATTEMPTS):
        try:
            response: APIResponse = context.request.get(
                url,
                timeout=scout.TIMEOUT_MS,
                fail_on_status_code=False,
            )
            last_status = int(response.status)
            if last_status == 200:
                try:
                    return last_status, response.json(), None
                except Exception:
                    return last_status, None, "RUNDOWN_JSON_INVALID"

            if last_status in RETRYABLE_AUTH_STATUSES and attempt + 1 < AUTH_RETRY_ATTEMPTS:
                time.sleep((AUTH_RETRY_DELAY_MS / 1000.0) * (attempt + 1))
                continue
            if last_status == 429 and attempt + 1 < AUTH_RETRY_ATTEMPTS:
                time.sleep(min(8.0, 2 ** attempt))
                continue
            last_code = f"RUNDOWN_HTTP_{last_status}"
            return last_status, None, last_code
        except Exception as exc:
            last_code = f"RUNDOWN_REQUEST_{type(exc).__name__.upper()}"
            if attempt + 1 < AUTH_RETRY_ATTEMPTS:
                time.sleep(min(4.0, 0.5 * (2 ** attempt)))
                continue
            return last_status, None, last_code

    return last_status, None, last_code


def run():
    original = scout._api_json
    scout._api_json = _auth_aware_api_json
    try:
        return scout.run()
    finally:
        scout._api_json = original


def main() -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    payload = run()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    handoff = payload.get("model_handoff") or {}
    print(json.dumps({
        "status": payload.get("status"),
        "provider": payload.get("provider"),
        "team_event_candidates": len(handoff.get("team_event_candidates") or []),
        "prop_candidates": len(handoff.get("prop_candidates") or []),
        "source_blockers": len(payload.get("source_blockers") or []),
        "bookmakers": (payload.get("sportsbook_coverage") or {}).get("bookmaker_count", 0),
        "can_execute": False,
    }, sort_keys=True))
    return 0 if payload.get("status") in {"DISCOVERY_COMPLETE", "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"} and payload.get("model_handoff_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
