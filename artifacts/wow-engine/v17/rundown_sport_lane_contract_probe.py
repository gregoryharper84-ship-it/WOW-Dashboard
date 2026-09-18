"""Safe probe for one authenticated TheRundown sport/date lane.

Emits endpoint status codes and JSON shape/count metadata only. No credentials,
headers, cookie values, or token values are serialized. can_execute=false.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from v17.rundown_authenticated_scout import BOARD_URL, TIMEOUT_MS, _login, _wait


def _shape(payload):
    if isinstance(payload, list):
        return {"type": "list", "count": len(payload), "item_keys": sorted(payload[0].keys())[:30] if payload and isinstance(payload[0], dict) else []}
    if isinstance(payload, dict):
        out = {"type": "dict", "keys": sorted(payload.keys())[:30]}
        for key in ("events", "markets", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                out[f"{key}_count"] = len(value)
            elif isinstance(value, dict):
                out[f"{key}_keys"] = sorted(value.keys())[:20]
        return out
    return {"type": type(payload).__name__}


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1680, "height": 1300}, locale="en-US")
        page = context.new_page(); page.set_default_timeout(TIMEOUT_MS)
        page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS); _wait(page)
        ok, blocker = _login(page)
        if not ok:
            print(json.dumps({"status": "AUTH_BLOCKED", "reason_code": blocker, "can_execute": False}, sort_keys=True)); return 2
        page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS); _wait(page); page.wait_for_timeout(6000)

        sports_response = context.request.get("https://therundown.io/api/v1/sports", timeout=TIMEOUT_MS, fail_on_status_code=False)
        if sports_response.status != 200:
            print(json.dumps({"status": "SPORTS_BLOCKED", "http_status": int(sports_response.status), "can_execute": False}, sort_keys=True)); return 2
        sports = sports_response.json().get("sports", [])
        preferred = ["MLB", "NFL", "NCAA Football", "WNBA"]
        sport = None
        for name in preferred:
            sport = next((row for row in sports if isinstance(row, dict) and row.get("sport_name") == name), None)
            if sport:
                break
        if not sport:
            sport = next((row for row in sports if isinstance(row, dict) and row.get("sport_id") is not None), None)
        if not sport:
            print(json.dumps({"status": "NO_SPORT", "can_execute": False}, sort_keys=True)); return 2

        sid = int(sport["sport_id"]); name = str(sport.get("sport_name") or sid)
        date = datetime.now(timezone.utc).date().isoformat()
        endpoints = {
            "events": f"/api/v2/sports/{sid}/events/{date}?main_line=true&hide_closed=true",
            "market_inventory": f"/api/v2/sports/{sid}/markets/{date}",
        }
        result = {"status": "PROBE_COMPLETE", "can_execute": False, "sport_name": name, "sport_id": sid, "date": date, "endpoints": {}}
        for label, path in endpoints.items():
            response = context.request.get("https://therundown.io" + path, timeout=TIMEOUT_MS, fail_on_status_code=False)
            meta = {"context_request_status": int(response.status)}
            if response.status == 200:
                try: meta["shape"] = _shape(response.json())
                except Exception: meta["shape"] = {"type": "invalid_json"}
            browser_result = page.evaluate("""async (path) => {
              try {
                const r = await fetch(path, {credentials:'include', headers:{'accept':'application/json'}});
                let shape={type:'unread'};
                try { const d=await r.json(); if(Array.isArray(d)) shape={type:'list',count:d.length}; else if(d&&typeof d==='object') shape={type:'dict',keys:Object.keys(d).sort().slice(0,30)}; } catch(_){shape={type:'invalid_json'}}
                return {status:r.status,shape};
              } catch(_){return {status:0,shape:{type:'fetch_error'}}}
            }""", path)
            meta["browser_fetch_status"] = int(browser_result.get("status") or 0); meta["browser_shape"] = browser_result.get("shape")
            result["endpoints"][label] = meta
        print(json.dumps(result, sort_keys=True))
        context.close(); browser.close(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
