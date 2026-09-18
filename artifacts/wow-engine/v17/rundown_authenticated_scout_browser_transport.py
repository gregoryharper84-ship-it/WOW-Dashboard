"""Authenticated browser transport shim for TheRundown Scout acquisition.

Keeps the validated normalization/routing code in rundown_authenticated_scout.py
unchanged while forcing first-party reads through the logged-in browser page.
Sportsbook/external probabilities remain evidence only. can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from v17 import rundown_authenticated_scout as base


def _api_json_browser(context, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, str | None]:
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = "https://therundown.io" + path + ("?" + query if query else "")
    pages = context.pages
    if not pages:
        return 0, None, "RUNDOWN_BROWSER_PAGE_UNAVAILABLE"
    page = pages[0]
    last_status = 0
    for attempt in range(base.MAX_RETRIES):
        try:
            result = page.evaluate(
                """async ({url, timeoutMs}) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), timeoutMs);
                    try {
                        const response = await fetch(url, {
                            method: 'GET',
                            credentials: 'include',
                            headers: {'accept': 'application/json'},
                            signal: controller.signal,
                        });
                        const text = await response.text();
                        let payload = null;
                        let parseError = false;
                        if (text) {
                            try { payload = JSON.parse(text); } catch (_) { parseError = true; }
                        }
                        return {status: response.status, payload, parseError};
                    } finally {
                        clearTimeout(timer);
                    }
                }""",
                {"url": url, "timeoutMs": base.TIMEOUT_MS},
            )
            last_status = int(result.get("status") or 0)
            if last_status == 200:
                if result.get("parseError"):
                    return last_status, None, "RUNDOWN_JSON_INVALID"
                return last_status, result.get("payload"), None
            if last_status == 429 and attempt + 1 < base.MAX_RETRIES:
                time.sleep(min(8.0, 2 ** attempt))
                continue
            return last_status, None, f"RUNDOWN_HTTP_{last_status}"
        except Exception as exc:
            if attempt + 1 >= base.MAX_RETRIES:
                return last_status, None, f"RUNDOWN_BROWSER_FETCH_{type(exc).__name__.upper()}"
            time.sleep(min(4.0, 0.5 * (2 ** attempt)))
    return last_status, None, "RUNDOWN_BROWSER_FETCH_FAILED"


def run() -> dict[str, Any]:
    original = base._api_json
    base._api_json = _api_json_browser
    try:
        payload = base.run()
    finally:
        base._api_json = original
    payload.setdefault("acquisition_transport", "PLAYWRIGHT_BROWSER_FETCH")
    payload.setdefault("can_execute", False)
    payload.setdefault("prediction_authority", False)
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
