"""Sanitized authenticated TheRundown sweep-size probe.

The app's own successful first-party request query is captured in memory and
reused for discovery. Query values (including the provider-issued key) are never
logged, serialized, or persisted. can_execute=false.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit

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
            const response = await fetch(url, {credentials:'include', headers:{accept:'application/json'}, cache:'no-store', signal:controller.signal});
            const text = await response.text();
            return {status:response.status, text};
          } catch (error) {
            return {status:0, error:String(error && error.name ? error.name : error)};
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
        "event_status_counts": {},
        "native_event_status": None,
        "native_event_query_keys": [],
        "native_market_status": None,
        "native_market_query_keys": [],
        "template_same_path_status": None,
        "template_cross_sport_status": None,
        "can_execute": False,
        "prediction_authority": False,
    }
    status_counts: Counter[int] = Counter()
    native_event = {"status": None, "path": None, "query": {}}
    native_market = {"status": None, "path": None, "query": {}}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width":1680,"height":1300}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(base.TIMEOUT_MS)

        def observe_response(response):
            try:
                parts = urlsplit(response.url)
                if parts.netloc.lower() != "therundown.io":
                    return
                pieces = [p for p in parts.path.split("/") if p]
                if len(pieces) == 6 and pieces[:3] == ["api", "v2", "sports"] and pieces[4] == "events" and native_event["path"] is None:
                    native_event["status"] = int(response.status)
                    native_event["path"] = parts.path
                    native_event["query"] = dict(parse_qsl(parts.query, keep_blank_values=True))
                elif len(pieces) == 6 and pieces[:3] == ["api", "v2", "sports"] and pieces[4] == "markets" and native_market["path"] is None:
                    native_market["status"] = int(response.status)
                    native_market["path"] = parts.path
                    native_market["query"] = dict(parse_qsl(parts.query, keep_blank_values=True))
            except Exception:
                return

        page.on("response", observe_response)
        try:
            page.goto(base.BOARD_URL, wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
            base._wait(page)
            ok, reason = base._login(page)
            if not ok:
                print(json.dumps({"status": reason or "RUNDOWN_AUTH_FAILED", "can_execute": False}, sort_keys=True))
                return 2
            page.goto(base.BOARD_URL, wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
            base._wait(page)
            page.wait_for_timeout(5000)

            status, payload = browser_json(page, "/api/v1/sports")
            if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("sports"), list):
                print(json.dumps({"status":"RUNDOWN_SPORT_INVENTORY_FAILED","http_status":status,"can_execute":False}, sort_keys=True))
                return 2
            sports = [r for r in payload["sports"] if isinstance(r, dict) and r.get("sport_id") is not None]
            summary["sports_discovered"] = len(sports)
            summary["native_event_status"] = native_event["status"]
            summary["native_event_query_keys"] = sorted(native_event["query"].keys())
            summary["native_market_status"] = native_market["status"]
            summary["native_market_query_keys"] = sorted(native_market["query"].keys())

            if not native_event["path"] or native_event["status"] != 200 or "key" not in native_event["query"]:
                print(json.dumps({**summary, "status":"RUNDOWN_NATIVE_EVENT_TEMPLATE_UNAVAILABLE"}, sort_keys=True))
                return 2

            # Re-fetch the exact native path/query as a control. Values stay only
            # in memory and are never included in the printed summary.
            same_status, _ = browser_json(page, native_event["path"], native_event["query"])
            summary["template_same_path_status"] = same_status

            first_sport = next((r for r in sports if str(r.get("sport_name") or "") not in base.NON_SPORT_NAMES), None)
            if first_sport is not None:
                test_path = f"/api/v2/sports/{int(first_sport['sport_id'])}/events/{start.date().isoformat()}"
                cross_status, _ = browser_json(page, test_path, native_event["query"])
                summary["template_cross_sport_status"] = cross_status

            event_template = dict(native_event["query"])
            # The event template controls authentication, timezone/offset, include,
            # market_ids and hide_closed. We do not invent or persist any values.
            market_template = dict(native_market["query"]) if native_market["query"] else {
                k: v for k, v in event_template.items() if k in {"key", "offset", "include", "hide_closed"}
            }

            for sport in sports:
                sport_id = int(sport["sport_id"])
                sport_name = str(sport.get("sport_name") or sport_id)
                if sport_name in base.NON_SPORT_NAMES:
                    summary["non_sports_excluded"] += 1
                    continue
                for date in base._date_strings(start, end):
                    summary["sport_dates_checked"] += 1
                    ep = f"/api/v2/sports/{sport_id}/events/{date}"
                    status, events_payload = browser_json(page, ep, event_template)
                    status_counts[status] += 1
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
                    status, markets_payload = browser_json(page, mp, market_template)
                    if status != 200:
                        summary["http_failures"] += 1
                        continue
                    entries = markets_payload.get(str(sport_id)) if isinstance(markets_payload, dict) else None
                    ids = {int(r["id"]) for r in (entries or []) if isinstance(r, dict) and r.get("id") is not None}
                    summary["available_market_ids"] += len(ids)
                    summary["market_batches_at_12"] += (len(ids) + 11) // 12
        finally:
            context.close(); browser.close()

    summary["event_status_counts"] = {str(k): v for k, v in sorted(status_counts.items())}
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
