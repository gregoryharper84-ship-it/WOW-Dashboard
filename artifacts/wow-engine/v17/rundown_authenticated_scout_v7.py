"""Production-shaped authenticated TheRundown collector transport.

The live odds app's successful event/market request query templates are recovered
from the authenticated page's PerformanceResourceTiming entries and reused only
in memory. Provider-issued key/query values are never logged, serialized, or
persisted. Event market_ids are overridden only when the governed sweep requests
a <=12-ID batch. Sportsbook observations remain evidence only and can_execute is
always false.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from v17 import rundown_authenticated_scout as base
from v17 import rundown_authenticated_scout_v4 as impl

_EVENT_TEMPLATE: dict[str, str] = {}
_MARKET_TEMPLATE: dict[str, str] = {}


def _resource_templates(page) -> tuple[dict[str, str], dict[str, str]]:
    global _EVENT_TEMPLATE, _MARKET_TEMPLATE
    if _EVENT_TEMPLATE and _MARKET_TEMPLATE:
        return _EVENT_TEMPLATE, _MARKET_TEMPLATE
    try:
        urls = page.evaluate("() => performance.getEntriesByType('resource').map(e => e.name)") or []
    except Exception:
        urls = []
    for raw_url in urls:
        try:
            parts = urlsplit(str(raw_url))
            if parts.netloc.lower() != "therundown.io":
                continue
            pieces = [p for p in parts.path.split("/") if p]
            if len(pieces) != 6 or pieces[:3] != ["api", "v2", "sports"]:
                continue
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            if pieces[4] == "events" and not _EVENT_TEMPLATE and "key" in query:
                _EVENT_TEMPLATE = query
            elif pieces[4] == "markets" and not _MARKET_TEMPLATE and "key" in query:
                _MARKET_TEMPLATE = query
        except Exception:
            continue
    return _EVENT_TEMPLATE, _MARKET_TEMPLATE


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


def _fetch(page, path: str, query: dict[str, Any]) -> tuple[int, Any, str | None]:
    encoded = urlencode({k: v for k, v in query.items() if v not in (None, "")})
    url = path + ("?" + encoded if encoded else "")
    last_status = 0
    for attempt in range(base.MAX_RETRIES):
        try:
            result = page.evaluate(
                """async ({url, timeoutMs}) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), timeoutMs);
                    try {
                        const response = await window.fetch(url, {
                            method: 'GET', credentials: 'include', cache: 'no-store',
                            headers: {'accept':'application/json'}, signal: controller.signal,
                        });
                        const text = await response.text();
                        return {status: response.status, text};
                    } catch (error) {
                        return {status:0, error:String(error && error.name ? error.name : error)};
                    } finally { clearTimeout(timer); }
                }""",
                {"url": url, "timeoutMs": base.TIMEOUT_MS},
            )
            last_status = int((result or {}).get("status") or 0)
            if last_status == 200:
                try:
                    return 200, json.loads((result or {}).get("text") or "null"), None
                except Exception:
                    return 200, None, "RUNDOWN_JSON_INVALID"
            if last_status in {401, 429} and attempt + 1 < base.MAX_RETRIES:
                page.wait_for_timeout(2500 if last_status == 401 else min(8000, 1000 * (2 ** attempt)))
                continue
            if last_status == 0:
                if attempt + 1 < base.MAX_RETRIES:
                    time.sleep(min(3.0, 0.5 * (2 ** attempt)))
                    continue
                return 0, None, "RUNDOWN_BROWSER_FETCH_FAILED"
            return last_status, None, f"RUNDOWN_HTTP_{last_status}"
        except Exception as exc:
            if attempt + 1 >= base.MAX_RETRIES:
                return last_status, None, f"RUNDOWN_BROWSER_FETCH_{type(exc).__name__.upper()}"
            time.sleep(min(3.0, 0.5 * (2 ** attempt)))
    return last_status, None, "RUNDOWN_BROWSER_FETCH_FAILED"


def _native_template_json(context, _captured_headers: dict[str, str], path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, str | None]:
    pages = context.pages
    if not pages:
        return 0, None, "RUNDOWN_BROWSER_PAGE_UNAVAILABLE"
    page = pages[0]
    event_template, market_template = _resource_templates(page)

    # The global market catalog is delivered successfully by the app itself but
    # rejects standalone reads; observe that first-party response directly.
    if path == "/api/v2/markets" and not params:
        return _app_observed_json(context, path)

    if "/events/" in path:
        if not event_template or "key" not in event_template:
            return 0, None, "RUNDOWN_NATIVE_EVENT_TEMPLATE_UNAVAILABLE"
        query: dict[str, Any] = dict(event_template)
        supplied = params or {}
        if supplied.get("market_ids") not in (None, ""):
            query["market_ids"] = supplied["market_ids"]
        # Keep the native app values for include/offset/key/hide_closed. Ignore
        # legacy main_line because the live app does not use it.
        return _fetch(page, path, query)

    if "/markets/" in path and path.startswith("/api/v2/sports/"):
        query = dict(market_template) if market_template else {
            k: v for k, v in event_template.items() if k in {"key", "offset"}
        }
        if "key" not in query:
            return 0, None, "RUNDOWN_NATIVE_MARKET_TEMPLATE_UNAVAILABLE"
        return _fetch(page, path, query)

    # Sports and affiliates are normal authenticated first-party JSON resources.
    return _fetch(page, path, {})


def run() -> dict[str, Any]:
    global _EVENT_TEMPLATE, _MARKET_TEMPLATE
    _EVENT_TEMPLATE = {}
    _MARKET_TEMPLATE = {}
    original = impl._request_json
    impl._request_json = _native_template_json
    try:
        payload = impl.run()
    finally:
        impl._request_json = original
        # Explicitly release any provider-issued query material from module state.
        _EVENT_TEMPLATE = {}
        _MARKET_TEMPLATE = {}
    payload["acquisition_transport"] = "PLAYWRIGHT_NATIVE_QUERY_TEMPLATE_BROWSER_FETCH"
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
