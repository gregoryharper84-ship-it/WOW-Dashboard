"""Safe authenticated contract probe for TheRundown inventory endpoints.

Prints only status codes, JSON type/key/count metadata, and whether the live app
itself observed each endpoint. No cookie/header/token/credential values are
printed or serialized. can_execute=false.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from playwright.sync_api import Response, sync_playwright

from v17.rundown_authenticated_scout import BOARD_URL, TIMEOUT_MS, _login, _wait

TARGETS = {
    "/api/v1/sports": "sports",
    "/api/v2/markets": "markets",
    "/api/v1/affiliates": "affiliates",
}


def _shape(payload):
    if isinstance(payload, list):
        return {"type": "list", "count": len(payload), "item_keys": sorted(payload[0].keys())[:30] if payload and isinstance(payload[0], dict) else []}
    if isinstance(payload, dict):
        out = {"type": "dict", "keys": sorted(payload.keys())[:30]}
        for key in ("sports", "markets", "affiliates", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                out[f"{key}_count"] = len(value)
        return out
    return {"type": type(payload).__name__}


def main() -> int:
    observed: dict[str, list[int]] = {label: [] for label in TARGETS.values()}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1680, "height": 1300}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

        def capture(response: Response) -> None:
            parts = urlsplit(response.url)
            label = TARGETS.get(parts.path)
            if label:
                observed[label].append(int(response.status))

        page.on("response", capture)
        page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        _wait(page)
        ok, blocker = _login(page)
        if not ok:
            print(json.dumps({"status": "AUTH_BLOCKED", "reason_code": blocker, "can_execute": False}, sort_keys=True))
            return 2
        page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        _wait(page)
        page.wait_for_timeout(6000)

        result = {"status": "PROBE_COMPLETE", "can_execute": False, "endpoints": {}}
        for path, label in TARGETS.items():
            response = context.request.get("https://therundown.io" + path, timeout=TIMEOUT_MS, fail_on_status_code=False)
            meta = {
                "context_request_status": int(response.status),
                "app_observed_statuses": observed[label][-5:],
            }
            if response.status == 200:
                try:
                    meta["shape"] = _shape(response.json())
                except Exception:
                    meta["shape"] = {"type": "invalid_json"}
            browser_result = page.evaluate("""async (path) => {
              try {
                const r = await fetch(path, {credentials: 'include', headers: {'accept': 'application/json'}});
                let shape = {type: 'unread'};
                try {
                  const data = await r.json();
                  if (Array.isArray(data)) shape = {type: 'list', count: data.length};
                  else if (data && typeof data === 'object') shape = {type: 'dict', keys: Object.keys(data).sort().slice(0,30)};
                } catch (_) { shape = {type: 'invalid_json'}; }
                return {status: r.status, shape};
              } catch (_) { return {status: 0, shape: {type: 'fetch_error'}}; }
            }""", path)
            meta["browser_fetch_status"] = int(browser_result.get("status") or 0)
            meta["browser_shape"] = browser_result.get("shape")
            result["endpoints"][label] = meta

        print(json.dumps(result, sort_keys=True))
        context.close(); browser.close()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
