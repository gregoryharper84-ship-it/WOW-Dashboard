"""Hybrid authenticated transport for TheRundown Scout full-slate acquisition.

Uses browser fetch first and the same BrowserContext.request fallback that the
credentialed transport probe proved returns HTTP 200. No credential values are
logged or serialized. Governance remains discovery-only, can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from v17 import rundown_authenticated_scout as base
from v17 import rundown_authenticated_scout_v2 as impl


def _hybrid_json(page, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, str | None]:
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    relative_url = path + ("?" + query if query else "")
    absolute_url = "https://therundown.io" + relative_url
    last_status = 0

    for attempt in range(base.MAX_RETRIES):
        try:
            browser_result = page.evaluate(
                """async ({url, timeoutMs}) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), timeoutMs);
                    try {
                        const response = await fetch(url, {
                            method: 'GET', credentials: 'include',
                            headers: {'accept': 'application/json'}, signal: controller.signal,
                        });
                        const text = await response.text();
                        let payload = null, parseError = false;
                        if (text) {
                            try { payload = JSON.parse(text); } catch (_) { parseError = true; }
                        }
                        return {status: response.status, payload, parseError};
                    } finally { clearTimeout(timer); }
                }""",
                {"url": relative_url, "timeoutMs": base.TIMEOUT_MS},
            )
            last_status = int(browser_result.get("status") or 0)
            if last_status == 200:
                if browser_result.get("parseError"):
                    return last_status, None, "RUNDOWN_JSON_INVALID"
                return last_status, browser_result.get("payload"), None
        except Exception:
            last_status = 0

        # The live probe proved BrowserContext.request can inherit the established
        # session even when a direct page fetch is not yet ready. Give the app a
        # readiness window before this fallback.
        page.wait_for_timeout(6000 if attempt == 0 else 1500)
        try:
            response = page.context.request.get(
                absolute_url, timeout=base.TIMEOUT_MS, fail_on_status_code=False,
            )
            last_status = int(response.status)
            if last_status == 200:
                try:
                    return last_status, response.json(), None
                except Exception:
                    return last_status, None, "RUNDOWN_JSON_INVALID"
        except Exception as exc:
            if attempt + 1 >= base.MAX_RETRIES:
                return last_status, None, f"RUNDOWN_SESSION_REQUEST_{type(exc).__name__.upper()}"

        if last_status in {401, 429} and attempt + 1 < base.MAX_RETRIES:
            page.wait_for_timeout(6000 if last_status == 401 else min(8000, (2 ** attempt) * 1000))
            continue
        if last_status:
            return last_status, None, f"RUNDOWN_HTTP_{last_status}"
        time.sleep(min(4.0, 0.5 * (2 ** attempt)))

    return last_status, None, f"RUNDOWN_HTTP_{last_status}" if last_status else "RUNDOWN_SESSION_TRANSPORT_FAILED"


def run() -> dict[str, Any]:
    original = impl._page_json
    impl._page_json = _hybrid_json
    try:
        payload = impl.run()
    finally:
        impl._page_json = original
    payload["acquisition_transport"] = "PLAYWRIGHT_HYBRID_AUTHENTICATED_SESSION"
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
        "status": payload.get("status"), "provider": payload.get("provider"),
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
