"""Sanitized authenticated TheRundown sweep-size probe.

Counts inventory only; it does not emit odds, account identifiers, credentials,
cookies, tokens, or candidate selections. can_execute=false.
"""
from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

from v17 import rundown_authenticated_scout as base


def browser_json(page, path: str, params=None):
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = path + ("?" + query if query else "")
    result = page.evaluate(
        """async ({url}) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 20000);
          try {
            const response = await fetch(url, {credentials:'include', headers:{accept:'application/json'}, signal:controller.signal});
            const text = await response.text();
            return {status:response.status, text};
          } finally { clearTimeout(timer); }
        }""",
        {"url": url},
    )
    status = int(result.get("status") or 0)
    if status != 200:
        return status, None
    try:
        return status, json.loads(result.get("text") or "null")
    except Exception:
        return status, None


def main() -> int:
    start = base.utc_now()
    end = start + timedelta(hours=base.HORIZON_HOURS)
    summary = {
        "status": "PROBE_COMPLETE",
        "sports_discovered": 0,
        "non_sports_excluded": 0,
        "sport_dates_checked": 0,
        "sport_dates_with_upcoming_events": 0,
        "upcoming_events": 0,
        "available_market_ids": 0,
        "market_batches_at_12": 0,
        "http_failures": 0,
        "can_execute": False,
        "prediction_authority": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width":1680,"height":1300}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(base.TIMEOUT_MS)
        try:
            page.goto(base.BOARD_URL, wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
            base._wait(page)
            ok, reason = base._login(page)
            if not ok:
                print(json.dumps({"status": reason or "RUNDOWN_AUTH_FAILED", "can_execute": False}, sort_keys=True))
                return 2
            page.goto(base.BOARD_URL, wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
            base._wait(page)
            page.wait_for_timeout(2500)
            status, payload = browser_json(page, "/api/v1/sports")
            if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("sports"), list):
                print(json.dumps({"status":"RUNDOWN_SPORT_INVENTORY_FAILED","http_status":status,"can_execute":False}, sort_keys=True))
                return 2
            sports = [r for r in payload["sports"] if isinstance(r, dict) and r.get("sport_id") is not None]
            summary["sports_discovered"] = len(sports)
            for sport in sports:
                sport_id = int(sport["sport_id"])
                sport_name = str(sport.get("sport_name") or sport_id)
                if sport_name in base.NON_SPORT_NAMES:
                    summary["non_sports_excluded"] += 1
                    continue
                for date in base._date_strings(start, end):
                    summary["sport_dates_checked"] += 1
                    ep = f"/api/v2/sports/{sport_id}/events/{date}"
                    status, events_payload = browser_json(page, ep, {"main_line":"true","hide_closed":"true"})
                    if status != 200 or not isinstance(events_payload, dict):
                        summary["http_failures"] += 1
                        continue
                    relevant = []
                    for event in events_payload.get("events") or []:
                        when = base._parse_dt(event.get("event_date") if isinstance(event, dict) else None)
                        if when is not None and start <= when <= end:
                            relevant.append(event)
                    if not relevant:
                        continue
                    summary["sport_dates_with_upcoming_events"] += 1
                    summary["upcoming_events"] += len(relevant)
                    mp = f"/api/v2/sports/{sport_id}/markets/{date}"
                    status, markets_payload = browser_json(page, mp)
                    if status != 200:
                        summary["http_failures"] += 1
                        continue
                    entries = markets_payload.get(str(sport_id)) if isinstance(markets_payload, dict) else None
                    ids = {int(r["id"]) for r in (entries or []) if isinstance(r, dict) and r.get("id") is not None}
                    summary["available_market_ids"] += len(ids)
                    summary["market_batches_at_12"] += (len(ids) + 11) // 12
        finally:
            context.close(); browser.close()
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
