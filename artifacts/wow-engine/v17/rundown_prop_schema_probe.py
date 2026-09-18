"""Inspect TheRundown prop line shape without emitting auth material."""
from __future__ import annotations

import json
from datetime import timezone

from playwright.sync_api import sync_playwright

from v17 import rundown_authenticated_scout as base
from v17 import rundown_authenticated_scout_v7 as transport

SAFE_KEYS = {
    "name", "description", "type", "side", "designation", "direction",
    "outcome", "outcome_type", "label", "value", "line", "price",
    "is_main_line", "updated_at", "market_id", "period", "scope",
}


def _safe_scalars(obj):
    if not isinstance(obj, dict):
        return {}
    out = {}
    for k, v in obj.items():
        lk = str(k).lower()
        if lk in SAFE_KEYS and isinstance(v, (str, int, float, bool, type(None))):
            out[str(k)] = v
    return out


def main() -> int:
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
            page.wait_for_timeout(5000)
            event_template, _ = transport._resource_templates(page)
            if not event_template or "key" not in event_template:
                print(json.dumps({"status":"RUNDOWN_NATIVE_EVENT_TEMPLATE_UNAVAILABLE","can_execute":False}, sort_keys=True))
                return 2

            # MLB pitcher strikeouts is a stable player O/U family for shape inspection.
            query = dict(event_template)
            query["market_ids"] = "19"
            date = base.utc_now().astimezone(timezone.utc).date().isoformat()
            status, payload, error = transport._fetch(page, f"/api/v2/sports/3/events/{date}", query)
            if error or status != 200 or not isinstance(payload, dict):
                print(json.dumps({"status":"PROP_SCHEMA_FETCH_FAILED","http_status":status,"reason":error,"can_execute":False}, sort_keys=True))
                return 2

            sample = None
            line_shapes = []
            for event in payload.get("events") or []:
                for market in event.get("markets") or []:
                    if not isinstance(market, dict) or int(market.get("market_id") or 0) != 19:
                        continue
                    for participant in market.get("participants") or []:
                        if not isinstance(participant, dict):
                            continue
                        for line in participant.get("lines") or []:
                            if not isinstance(line, dict):
                                continue
                            prices = line.get("prices") or {}
                            price_shapes = []
                            if isinstance(prices, dict):
                                for price_obj in prices.values():
                                    if isinstance(price_obj, dict):
                                        price_shapes.append({
                                            "keys": sorted(str(k) for k in price_obj.keys()),
                                            "safe": _safe_scalars(price_obj),
                                        })
                                    if len(price_shapes) >= 2:
                                        break
                            line_shapes.append({
                                "participant_keys": sorted(str(k) for k in participant.keys()),
                                "participant_safe": _safe_scalars(participant),
                                "line_keys": sorted(str(k) for k in line.keys()),
                                "line_safe": _safe_scalars(line),
                                "price_shapes": price_shapes,
                            })
                            if len(line_shapes) >= 4:
                                break
                        if len(line_shapes) >= 4:
                            break
                    if line_shapes:
                        sample = {
                            "market_keys": sorted(str(k) for k in market.keys()),
                            "market_safe": _safe_scalars(market),
                            "line_shapes": line_shapes,
                        }
                        break
                if sample:
                    break

            print(json.dumps({
                "status": "PROP_SCHEMA_PROBE_COMPLETE" if sample else "PROP_SCHEMA_SAMPLE_UNAVAILABLE",
                "sample": sample,
                "can_execute": False,
                "prediction_authority": False,
            }, sort_keys=True))
            return 0 if sample else 2
        finally:
            transport._EVENT_TEMPLATE = {}
            transport._MARKET_TEMPLATE = {}
            context.close(); browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
