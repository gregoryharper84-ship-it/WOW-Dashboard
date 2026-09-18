"""Probe public TheRundown sport/date routes for V17 Scout acquisition.

Read-only diagnostic. Does not authenticate, place bets, or assign probabilities.
The purpose is to distinguish the real sport/date board from TheRundown's global
marketing OddsPreviewSection, which is deliberately excluded from row capture.
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


def _real_board_rows(page) -> list[str]:
    """Capture odds-like rows while explicitly excluding marketing preview DOM."""
    selectors = [
        '[data-testid*="odds-row"]',
        '[data-testid*="event-row"]',
        '[class*="oddsRow"]:not([class*="OddsPreviewSection"])',
        'main tr',
        'main [role="row"]',
    ]
    seen: set[str] = set()
    out: list[str] = []
    for selector in selectors:
        loc = page.locator(selector)
        try:
            count = min(loc.count(), 1000)
        except Exception:
            continue
        for i in range(count):
            try:
                node = loc.nth(i)
                if not node.is_visible():
                    continue
                cls = node.get_attribute("class") or ""
                if "OddsPreviewSection" in cls:
                    continue
                if node.locator('xpath=ancestor::*[contains(@class,"OddsPreviewSection")]').count():
                    continue
                text = " ".join(node.inner_text(timeout=2000).split())
            except Exception:
                continue
            if not text or text in seen or not ODDS_RE.search(text):
                continue
            seen.add(text)
            out.append(text[:1800])
    return out


def _dom_candidates(page) -> list[dict]:
    script = r"""
    () => {
      const out = [];
      const nodes = Array.from(document.querySelectorAll('main *, section, table, [role="table"], [role="grid"]'));
      for (const el of nodes) {
        const cls = String(el.className || '');
        if (cls.includes('OddsPreviewSection')) continue;
        if (el.closest('[class*="OddsPreviewSection"]')) continue;
        const text = (el.innerText || '').replace(/\s+/g, ' ').trim();
        if (!text || text.length < 20) continue;
        if (!/(moneyline|money line|spread|total|odds|sportsbook|draftkings|fanduel|betmgm|caesars|[+-]\d{3,4})/i.test(text)) continue;
        out.push({
          tag: el.tagName,
          id: el.id || null,
          className: cls.slice(0, 300),
          testid: el.getAttribute('data-testid'),
          role: el.getAttribute('role'),
          text: text.slice(0, 900)
        });
        if (out.length >= 120) break;
      }
      return out;
    }
    """
    try:
        return page.evaluate(script)
    except Exception:
        return []


def _script_sources(page) -> list[str]:
    values: list[str] = []
    try:
        loc = page.locator("script[src]")
        for i in range(min(loc.count(), 200)):
            src = loc.nth(i).get_attribute("src")
            if src:
                safe = _safe(src)
                if safe not in values:
                    values.append(safe)
    except Exception:
        pass
    return values


def _iframe_sources(page) -> list[str]:
    values: list[str] = []
    try:
        loc = page.locator("iframe")
        for i in range(min(loc.count(), 50)):
            src = loc.nth(i).get_attribute("src")
            if src:
                values.append(_safe(src))
    except Exception:
        pass
    return values


def _response_metadata(response) -> dict | None:
    """Sanitized fetch/XHR/JSON metadata; query strings are never recorded."""
    try:
        request = response.request
        resource_type = request.resource_type
        ctype = (response.headers.get("content-type") or "").lower()
        if resource_type not in {"xhr", "fetch"} and "json" not in ctype:
            return None
        item = {
            "url": _safe(response.url),
            "host": urlsplit(response.url).netloc.lower(),
            "status": response.status,
            "resource_type": resource_type,
            "content_type": ctype.split(";")[0],
        }
        if "json" in ctype:
            try:
                payload = response.json()
                item["payload_type"] = type(payload).__name__
                if isinstance(payload, dict):
                    item["top_level_keys"] = sorted(str(k) for k in payload.keys())[:100]
                    for key in ("events", "data", "markets", "odds", "sports", "results", "games"):
                        value = payload.get(key)
                        if isinstance(value, list):
                            item[f"{key}_count"] = len(value)
                        elif isinstance(value, dict):
                            item[f"{key}_keys"] = sorted(str(k) for k in value.keys())[:60]
                elif isinstance(payload, list):
                    item["length"] = len(payload)
                    if payload and isinstance(payload[0], dict):
                        item["item_keys"] = sorted(str(k) for k in payload[0].keys())[:100]
            except Exception:
                item["json_parse"] = "FAILED"
        return item
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat())
    args = ap.parse_args()

    result = {
        "schema": "wow.v17.rundown.route-probe.v2",
        "date": args.date,
        "can_execute": False,
        "prediction_authority": False,
        "root_odds_links": [],
        "routes": [],
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1400}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(30000)

        page.goto("https://therundown.io/odds/", wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            pass
        links = page.locator('a[href*="/odds/"]')
        for i in range(min(links.count(), 300)):
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
            network: list[dict] = []

            def on_response(response):
                meta = _response_metadata(response)
                if meta and meta not in network and len(network) < 250:
                    network.append(meta)

            page.on("response", on_response)
            url = f"https://therundown.io/odds/{sport}/{args.date}"
            row = {"sport": sport, "requested_url": url}
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(1500)
                row["http_status"] = response.status if response else None
                row["final_url"] = _safe(page.url)
                row["title"] = page.title()
                odds = _real_board_rows(page)
                row["real_board_row_count"] = len(odds)
                row["sample_real_board_rows"] = odds[:12]
                row["marketing_preview_row_count"] = page.locator('[class*="OddsPreviewSection_oddsRow"]').count()
                row["sign_in_form"] = bool(page.locator('input[type="password"]').count())
                row["iframes"] = _iframe_sources(page)
                row["dom_candidates"] = _dom_candidates(page)[:60]
                row["scripts"] = _script_sources(page)[-40:]
                row["network"] = network[:150]
                try:
                    row["body_text_sample"] = " ".join(page.locator("body").inner_text(timeout=5000).split())[:5000]
                except Exception:
                    row["body_text_sample"] = ""
            except Exception as exc:
                row["error"] = type(exc).__name__
            finally:
                page.remove_listener("response", on_response)
            result["routes"].append(row)

        context.close()
        browser.close()

    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True))
    print(json.dumps({
        "date": args.date,
        "root_odds_links": len(result["root_odds_links"]),
        "routes": [
            {
                "sport": r["sport"],
                "status": r.get("http_status"),
                "real_rows": r.get("real_board_row_count"),
                "preview_rows": r.get("marketing_preview_row_count"),
                "xhr_fetch": len(r.get("network") or []),
                "dom_candidates": len(r.get("dom_candidates") or []),
                "iframes": r.get("iframes"),
                "sign_in_form": r.get("sign_in_form"),
            }
            for r in result["routes"]
        ],
        "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
