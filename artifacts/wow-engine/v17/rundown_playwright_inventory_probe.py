"""Sanitized authenticated TheRundown inventory probe for V17 Scout development.

This is a diagnostic-only companion to ``rundown_playwright_capture``. It logs
only market-data responses from ``therundown.io`` and explicitly excludes auth,
payment, token, cookie, header and user-profile payloads. The purpose is to pin
the provider's player-prop schema before production normalisation is added.

No probability, ranking, staking or execution authority exists here.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from playwright.sync_api import BrowserContext, Page, Request, Response, sync_playwright

from v17.rundown_playwright_capture import (
    BOARD_URL,
    CAN_EXECUTE,
    PREDICTION_AUTHORITY,
    RESEARCH_CEILING,
    TIMEOUT_MS,
    _is_auth_page,
    _is_marketing_landing,
    _login,
    _wait_after_navigation,
)

_ALLOWED_PATHS = (
    re.compile(r"^/api/v1/sports$"),
    re.compile(r"^/api/v2/markets$"),
    re.compile(r"^/api/v2/sports/\d+/(?:events|openers|markets)/\d{4}-\d{2}-\d{2}$"),
)
_EVENT_PATH_RE = re.compile(r"^/api/v2/sports/\d+/events/\d{4}-\d{2}-\d{2}$")
# Only applied as defence in depth to approved market-data responses. `scope`
# is intentionally not redacted because it is provider market taxonomy, not an
# OAuth scope, on these whitelisted paths.
_SENSITIVE_KEY_RE = re.compile(
    r"token|authorization|cookie|password|secret|email|user|payment|subscription|refresh",
    re.I,
)
_PROP_PRIORITIES = {
    1: ("passing_yards", "rushing_yards", "player_receiving_yards"),
    2: ("passing_yards", "rushing_yards", "player_receiving_yards"),
    3: ("pitcher_strikeouts", "pitching_outs", "hits"),
    8: ("points", "player_rebounds", "player_assists"),
}
_PILOT_AFFILIATE_IDS = "3,19,22,23"
_TRANSPORT_HEADERS = {
    "cookie",
    "host",
    "content-length",
    "accept-encoding",
    "connection",
}


def _allowed_response(response: Response) -> bool:
    parts = urlsplit(response.url)
    if parts.netloc.lower() != "therundown.io":
        return False
    if response.status != 200:
        return False
    ctype = (response.headers.get("content-type") or "").lower()
    if "json" not in ctype:
        return False
    return any(pattern.match(parts.path) for pattern in _ALLOWED_PATHS)


def _redact(node: Any, depth: int = 0) -> Any:
    """Defence-in-depth redaction even though only market-data paths are allowed."""
    if depth > 14:
        return "<depth-limit>"
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if _SENSITIVE_KEY_RE.search(str(key)):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = _redact(value, depth + 1)
        return out
    if isinstance(node, list):
        return [_redact(value, depth + 1) for value in node]
    if isinstance(node, (str, int, float, bool)) or node is None:
        return node
    return str(node)


def _ephemeral_api_headers(request: Request) -> dict[str, str]:
    """Copy native app request context in memory only, never browser cookies.

    The app may place request authorization in a non-``x-*`` header. Therefore
    the replay keeps all non-transport headers except browser-managed ``sec-*``
    headers. Values are never serialized, logged, or returned by this probe.
    BrowserContext.request shares the browser cookie jar, so Cookie is neither
    needed nor allowed here.
    """
    try:
        headers = request.all_headers()
    except Exception:
        headers = request.headers
    copied: dict[str, str] = {}
    for raw_name, raw_value in headers.items():
        name = str(raw_name).lower()
        if name in _TRANSPORT_HEADERS or name.startswith("sec-"):
            continue
        copied[name] = str(raw_value)
    return copied


def _available_prop_market_ids(payloads: dict[str, Any], sport_id: int, date: str) -> list[int]:
    catalog_raw = payloads.get("/api/v2/markets") or []
    catalog = {
        int(item["id"]): item
        for item in catalog_raw
        if isinstance(item, dict) and item.get("id") is not None
    }
    available = payloads.get(f"/api/v2/sports/{sport_id}/markets/{date}") or {}
    entries = available.get(str(sport_id)) if isinstance(available, dict) else None
    if not isinstance(entries, list):
        return []
    available_ids = {
        int(item["id"])
        for item in entries
        if isinstance(item, dict) and item.get("id") is not None
    }
    by_name = {
        str(item.get("name")): market_id
        for market_id, item in catalog.items()
        if market_id in available_ids
        and item.get("class") == "prop"
        and item.get("family") == "player_ou"
        and item.get("live") is False
    }
    selected: list[int] = []
    for name in _PROP_PRIORITIES.get(sport_id, ()):
        market_id = by_name.get(name)
        if market_id is not None:
            selected.append(market_id)
    return selected[:3]


def _bounded_prop_pilot(
    context: BrowserContext,
    payloads: dict[str, Any],
    ephemeral_headers: dict[str, str],
) -> dict[str, Any]:
    """Fetch a tiny same-session prop sample to pin the live player-line schema.

    This deliberately requests only up to three player O/U markets for four
    sports and four known books. The app's own request context is reused only in
    memory. Header values are never persisted or printed.
    """
    date = datetime.now(timezone.utc).date().isoformat()
    results: dict[str, Any] = {}
    if not ephemeral_headers:
        return {
            str(sport_id): {
                "status": "APP_REQUEST_AUTH_CONTEXT_UNAVAILABLE",
                "http_status": None,
                "market_ids": _available_prop_market_ids(payloads, sport_id, date),
                "affiliate_ids": [3, 19, 22, 23],
                "body": None,
            }
            for sport_id in _PROP_PRIORITIES
        }

    for sport_id in _PROP_PRIORITIES:
        market_ids = _available_prop_market_ids(payloads, sport_id, date)
        if not market_ids:
            results[str(sport_id)] = {
                "status": "NO_AVAILABLE_PLAYER_PROP_MARKETS",
                "market_ids": [],
                "events": [],
            }
            continue
        query = urlencode({
            "market_ids": ",".join(str(v) for v in market_ids),
            "affiliate_ids": _PILOT_AFFILIATE_IDS,
            "main_line": "true",
        })
        url = f"https://therundown.io/api/v2/sports/{sport_id}/events/{date}?{query}"
        try:
            response = context.request.get(
                url,
                headers=ephemeral_headers,
                timeout=TIMEOUT_MS,
                fail_on_status_code=False,
            )
            status = int(response.status)
            body = response.json() if status == 200 else None
        except Exception:
            status = 0
            body = None
        results[str(sport_id)] = {
            "status": "OK" if status == 200 and isinstance(body, dict) else "HTTP_BLOCKED",
            "http_status": status,
            "market_ids": market_ids,
            "affiliate_ids": [3, 19, 22, 23],
            "body": _redact(body) if status == 200 and isinstance(body, dict) else None,
        }
    return results


def probe(output: str | Path) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    prop_pilot: dict[str, Any] = {}
    # Sensitive values may live here transiently. This mapping is intentionally
    # never included in the result object or any print statement.
    ephemeral_headers: dict[str, str] = {}
    # Header names alone are safe diagnostics; values are never persisted.
    app_event_request_header_names: list[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1680, "height": 1300}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

        def on_request(request: Request) -> None:
            nonlocal app_event_request_header_names
            parts = urlsplit(request.url)
            if parts.netloc.lower() != "therundown.io" or not _EVENT_PATH_RE.match(parts.path):
                return
            # The application's own event requests normally include market_ids.
            # Capture the first native request before the synthetic pilot begins;
            # once populated, never overwrite the context.
            if ephemeral_headers:
                return
            captured = _ephemeral_api_headers(request)
            if not captured:
                return
            ephemeral_headers.update(captured)
            app_event_request_header_names = sorted(captured)

        def on_response(response: Response) -> None:
            if not _allowed_response(response):
                return
            path = urlsplit(response.url).path
            if path in payloads:
                return
            try:
                payloads[path] = _redact(response.json())
            except Exception:
                return

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            _wait_after_navigation(page)
            ok, blocker = _login(page)
            if not ok:
                result = {
                    "schema": "wow.v17.rundown.authenticated-inventory-probe.v4",
                    "status": "DISCOVERY_BLOCKED",
                    "reason_code": blocker,
                    "can_execute": False,
                    "prediction_authority": False,
                    "research_ceiling": RESEARCH_CEILING,
                    "payloads": {},
                    "prop_pilot": {},
                    "app_event_request_header_names": [],
                }
                Path(output).write_text(json.dumps(result, indent=2, sort_keys=True))
                return result

            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            _wait_after_navigation(page)
            if _is_auth_page(page):
                blocker = "RUNDOWN_AUTH_FAILED"
            elif _is_marketing_landing(page):
                blocker = "RUNDOWN_ACCOUNT_ODDS_SURFACE_UNAVAILABLE"
            else:
                blocker = None
            page.wait_for_timeout(8000)
            if blocker is None:
                prop_pilot = _bounded_prop_pilot(context, payloads, ephemeral_headers)
        finally:
            # Explicitly drop the transient auth material before closing the
            # browser context. It is never serialized.
            ephemeral_headers.clear()
            context.close()
            browser.close()

    result = {
        "schema": "wow.v17.rundown.authenticated-inventory-probe.v4",
        "status": "DISCOVERY_COMPLETE" if not blocker and payloads else "DISCOVERY_BLOCKED",
        "reason_code": blocker or (None if payloads else "RUNDOWN_AUTHENTICATED_INVENTORY_EMPTY"),
        "can_execute": CAN_EXECUTE,
        "prediction_authority": PREDICTION_AUTHORITY,
        "research_ceiling": RESEARCH_CEILING,
        "captured_path_count": len(payloads),
        "captured_paths": sorted(payloads),
        "app_event_request_header_names": app_event_request_header_names,
        "payloads": payloads,
        "prop_pilot": prop_pilot,
    }
    Path(output).write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = probe(args.output)
    pilot = result.get("prop_pilot") or {}
    print(json.dumps({
        "status": result["status"],
        "reason_code": result["reason_code"],
        "captured_path_count": result.get("captured_path_count", 0),
        "app_event_request_header_names": result.get("app_event_request_header_names", []),
        "prop_pilot_statuses": {k: v.get("status") for k, v in pilot.items()},
        "can_execute": result["can_execute"],
    }, sort_keys=True))
    return 0 if result["status"] == "DISCOVERY_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
