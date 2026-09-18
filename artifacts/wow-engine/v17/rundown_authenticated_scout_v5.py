"""Authenticated TheRundown transport for the WOW V17 Scout sweep.

v4 owns the validated login/readiness/full-slate normalization flow. Standard
first-party reads use BrowserContext.request with the current authenticated
cookie jar. The global /api/v2/markets catalog is different: live contract
probing proves direct requests return 401 while the authenticated app itself
receives HTTP 200. That catalog is therefore captured from the app's own network
response during a same-session reload. No secret values are logged or serialized.
Sportsbook data remains evidence only and can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from playwright.sync_api import APIResponse, TimeoutError as PlaywrightTimeoutError

from v17 import rundown_authenticated_scout as base
from v17 import rundown_authenticated_scout_v4 as impl


def _app_observed_json(context, path: str) -> tuple[int, Any, str | None]:
    pages = context.pages
    if not pages:
        return 0, None, "RUNDOWN_BROWSER_PAGE_UNAVAILABLE"
    page = pages[0]
    try:
        with page.expect_response(
            lambda response: urlsplit(response.url).netloc.lower() == "therundown.io"
            and urlsplit(response.url).path == path,
            timeout=base.TIMEOUT_MS,
        ) as observed:
            page.reload(wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
        response = observed.value
        base._wait(page)
        status = int(response.status)
        if status != 200:
            return status, None, f"RUNDOWN_APP_HTTP_{status}"
        try:
            return status, response.json(), None
        except Exception:
            return status, None, "RUNDOWN_APP_JSON_INVALID"
    except PlaywrightTimeoutError:
        return 0, None, "RUNDOWN_APP_RESPONSE_NOT_OBSERVED"
    except Exception as exc:
        return 0, None, f"RUNDOWN_APP_RESPONSE_{type(exc).__name__.upper()}"


def _current_session_json(context, _captured_headers: dict[str, str], path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, str | None]:
    # Contract probe: /api/v2/markets is intentionally available to the live app
    # but not to a standalone context/browser fetch, even in the same session.
    if path == "/api/v2/markets" and not params:
        return _app_observed_json(context, path)

    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = "https://therundown.io" + path + ("?" + query if query else "")
    last_status = 0
    for attempt in range(base.MAX_RETRIES):
        try:
            response: APIResponse = context.request.get(
                url,
                headers={"accept": "application/json"},
                timeout=base.TIMEOUT_MS,
                fail_on_status_code=False,
            )
            last_status = int(response.status)
            if last_status == 200:
                try:
                    return last_status, response.json(), None
                except Exception:
                    return last_status, None, "RUNDOWN_JSON_INVALID"
            if last_status in {401, 429} and attempt + 1 < base.MAX_RETRIES:
                time.sleep(6.0 if last_status == 401 else min(8.0, 2 ** attempt))
                continue
            return last_status, None, f"RUNDOWN_HTTP_{last_status}"
        except Exception as exc:
            if attempt + 1 >= base.MAX_RETRIES:
                return last_status, None, f"RUNDOWN_REQUEST_{type(exc).__name__.upper()}"
            time.sleep(min(4.0, 0.5 * (2 ** attempt)))
    return last_status, None, "RUNDOWN_REQUEST_FAILED"


def run() -> dict[str, Any]:
    original = impl._request_json
    impl._request_json = _current_session_json
    try:
        payload = impl.run()
    finally:
        impl._request_json = original
    payload["acquisition_transport"] = "PLAYWRIGHT_APP_NETWORK_PLUS_CURRENT_SESSION"
    payload["can_execute"] = False
    payload["prediction_authority"] = False
    return payload


def main() -> int:
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
        "transport": payload.get("acquisition_transport"),
        "team_event_candidates": len(handoff.get("team_event_candidates") or []),
        "prop_candidates": len(handoff.get("prop_candidates") or []),
        "source_blockers": len(payload.get("source_blockers") or []),
        "bookmakers": (payload.get("sportsbook_coverage") or {}).get("bookmaker_count", 0),
        "can_execute": False,
    }, sort_keys=True))
    return 0 if payload.get("status") in {"DISCOVERY_COMPLETE", "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"} and payload.get("model_handoff_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
