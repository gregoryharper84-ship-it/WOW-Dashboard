"""Probe public TheRundown sport/date routes for V17 Scout acquisition.

Read-only diagnostic. Does not authenticate, place bets, or assign probabilities.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

SPORT_SLUGS = (
    "mlb",
    "nfl",
    "ncaaf",
    "nba",
    "wnba",
    "ncaab",
    "nhl",
    "ufc",
)
ODDS_RE = re.compile(r"(?<!\d)[+-]\d{3,4}(?!\d)")


def _safe(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def _odds_rows(page) -> list[str]:
    selectors = [
        '[class*="oddsRow"]',
        '[class*="OddsPreviewSection_oddsRow"]',
        '[data-testid*="odds-row"]',
        '[data-testid*="event-row"]',
        'main tr',
    ]
    seen: set[str] = set()
    out: list[str] = []
    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = min(loc.count(), 500)
        except Exception:
            continue
        for i in range(count):
            try:
                n = loc.nth(i)
                if not n.is_visible():
                    continue
                text = " ".join(n.inner_text(timeout=2000).split())
            except Exception:
                continue
            if not text or text in seen:
                continue
            if selector == "main tr" and not ODDS_RE.search(text):
                continue
            seen.add(text)
            out.append(text[:1200])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()

    result = {
        "schema": "wow.v17.rundown.route-probe.v1",
        "date": args.date,
        "can_execute": False,
        "prediction_authority": False,
        "root_odds_links": [],
        "routes": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1200}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(30000)

        page.goto("https://therundown.io/odds/", wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        links = page.locator('a[href*="/odds/"]')
        for i in range(min(links.count(), 200)):
            try:
                href = links.nth(i).get_attribute("href")
            except Exception:
                continue
            if not href:
                continue
            if href.startswith("/"):
                href = "https://therundown.io" + href
            safe = _safe(href)
            if safe not in result["root_odds_links"]:
                result["root_odds_links"].append(safe)

        for sport in SPORT_SLUGS:
            url = f"https://therundown.io/odds/{sport}/{args.date}"
            row = {"sport": sport, "requested_url": url}
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1200)
                row["http_status"] = response.status if response else None
                row["final_url"] = _safe(page.url)
                row["title"] = page.title()
                odds = _odds_rows(page)
                row["row_count"] = len(odds)
                row["sample_rows"] = odds[:5]
                row["sign_in_form"] = bool(page.locator('input[type="password"]').count())
            except Exception as exc:
                row["error"] = type(exc).__name__
            result["routes"].append(row)

        context.close()
        browser.close()

    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps({
        "date": args.date,
        "root_odds_links": len(result["root_odds_links"]),
        "routes": [
            {"sport": r["sport"], "status": r.get("http_status"), "rows": r.get("row_count"), "final_url": r.get("final_url"), "sign_in_form": r.get("sign_in_form")}
            for r in result["routes"]
        ],
        "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
