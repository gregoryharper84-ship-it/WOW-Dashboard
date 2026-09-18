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
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Response, sync_playwright

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
_SENSITIVE_KEY_RE = re.compile(
    r"token|authorization|cookie|password|secret|email|user|payment|subscription|refresh|scope",
    re.I,
)


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
    if depth > 12:
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


def probe(output: str | Path) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1680, "height": 1300}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

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

        page.on("response", on_response)
        try:
            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            _wait_after_navigation(page)
            ok, blocker = _login(page)
            if not ok:
                result = {
                    "schema": "wow.v17.rundown.authenticated-inventory-probe.v1",
                    "status": "DISCOVERY_BLOCKED",
                    "reason_code": blocker,
                    "can_execute": False,
                    "prediction_authority": False,
                    "research_ceiling": RESEARCH_CEILING,
                    "payloads": {},
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
        finally:
            context.close()
            browser.close()

    result = {
        "schema": "wow.v17.rundown.authenticated-inventory-probe.v1",
        "status": "DISCOVERY_COMPLETE" if not blocker and payloads else "DISCOVERY_BLOCKED",
        "reason_code": blocker or (None if payloads else "RUNDOWN_AUTHENTICATED_INVENTORY_EMPTY"),
        "can_execute": CAN_EXECUTE,
        "prediction_authority": PREDICTION_AUTHORITY,
        "research_ceiling": RESEARCH_CEILING,
        "captured_path_count": len(payloads),
        "captured_paths": sorted(payloads),
        "payloads": payloads,
    }
    Path(output).write_text(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = probe(args.output)
    print(json.dumps({
        "status": result["status"],
        "reason_code": result["reason_code"],
        "captured_path_count": result.get("captured_path_count", 0),
        "captured_paths": result.get("captured_paths", []),
        "can_execute": result["can_execute"],
    }, sort_keys=True))
    return 0 if result["status"] == "DISCOVERY_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
