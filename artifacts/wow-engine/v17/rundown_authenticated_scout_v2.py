"""TheRundown authenticated full-slate Scout collector v2.

Uses the exact logged-in Playwright Page for same-origin first-party JSON reads,
matching the transport probe that returned HTTP 200. Normalization and routing
reuse the validated V17 Scout helpers. Market data remains evidence only and
can_execute=false.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import Page, sync_playwright

from v17 import rundown_authenticated_scout as base


def _page_json(page: Page, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, str | None]:
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    relative_url = path + ("?" + query if query else "")
    last_status = 0
    for attempt in range(base.MAX_RETRIES):
        try:
            result = page.evaluate(
                """async ({url, timeoutMs}) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), timeoutMs);
                    try {
                        const response = await fetch(url, {
                            method: 'GET', credentials: 'include',
                            headers: {'accept': 'application/json'}, signal: controller.signal,
                        });
                        const text = await response.text();
                        let payload = null, parseError = false;
                        if (text) {
                            try { payload = JSON.parse(text); } catch (_) { parseError = true; }
                        }
                        return {status: response.status, payload, parseError};
                    } finally { clearTimeout(timer); }
                }""",
                {"url": relative_url, "timeoutMs": base.TIMEOUT_MS},
            )
            last_status = int(result.get("status") or 0)
            if last_status == 200:
                if result.get("parseError"):
                    return last_status, None, "RUNDOWN_JSON_INVALID"
                return last_status, result.get("payload"), None
            if last_status in {401, 429} and attempt + 1 < base.MAX_RETRIES:
                page.wait_for_timeout(6000 if last_status == 401 else min(8000, (2 ** attempt) * 1000))
                continue
            return last_status, None, f"RUNDOWN_HTTP_{last_status}"
        except Exception as exc:
            if attempt + 1 >= base.MAX_RETRIES:
                return last_status, None, f"RUNDOWN_BROWSER_FETCH_{type(exc).__name__.upper()}"
            time.sleep(min(4.0, 0.5 * (2 ** attempt)))
    return last_status, None, "RUNDOWN_BROWSER_FETCH_FAILED"


def run() -> dict[str, Any]:
    started = base.utc_now()
    end = started + timedelta(hours=base.HORIZON_HOURS)
    coverage: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    bookmakers: set[str] = set()
    events_all: dict[tuple[int, str], dict[str, Any]] = {}
    event_blockers: dict[tuple[int, str], list[dict[str, Any]]] = {}
    rows_by_event: dict[tuple[int, str], list[dict[str, Any]]] = {}
    sports: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1680, "height": 1300}, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(base.TIMEOUT_MS)
        try:
            page.goto(base.BOARD_URL, wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
            base._wait(page)
            ok, reason = base._login(page)
            if not ok:
                return base._blocked(reason or "RUNDOWN_AUTH_FAILED", coverage, blockers)
            page.goto(base.BOARD_URL, wait_until="domcontentloaded", timeout=base.TIMEOUT_MS)
            base._wait(page)
            page.wait_for_timeout(6000)
            if base._is_auth_page(page):
                return base._blocked("RUNDOWN_AUTH_FAILED", coverage, blockers)

            s_status, s_payload, s_error = _page_json(page, "/api/v1/sports")
            m_status, m_payload, m_error = _page_json(page, "/api/v2/markets")
            a_status, a_payload, _ = _page_json(page, "/api/v1/affiliates")
            if s_error or not isinstance(s_payload, dict) or not isinstance(s_payload.get("sports"), list):
                return base._blocked(s_error or "RUNDOWN_SPORT_INVENTORY_INVALID", coverage, blockers, s_status)
            if m_error or not isinstance(m_payload, list):
                return base._blocked(m_error or "RUNDOWN_MARKET_CATALOG_INVALID", coverage, blockers, m_status)

            catalog = base._market_catalog(m_payload)
            affiliates = base._affiliate_map(a_payload) if a_status == 200 else {}
            sports = [row for row in s_payload["sports"] if isinstance(row, dict) and row.get("sport_id") is not None]
            coverage.append({
                "scope": "provider_inventory", "status": "SCANNED",
                "sports_discovered": len(sports), "market_definitions": len(catalog),
                "affiliates_discovered": len(affiliates), "transport": "PLAYWRIGHT_EXACT_PAGE_FETCH",
            })

            for sport in sports:
                sport_id = int(sport["sport_id"])
                sport_name = str(sport.get("sport_name") or sport_id)
                if sport_name in base.NON_SPORT_NAMES:
                    coverage.append({"scope": "sport", "sport_id": sport_id, "sport": sport_name, "status": "NON_SPORT_INVENTORY_EXCLUDED"})
                    continue
                skey = base._sport_key(sport_name, sport_id)
                for date in base._date_strings(started, end):
                    base_path = f"/api/v2/sports/{sport_id}/events/{date}"
                    e_status, e_payload, e_error = _page_json(page, base_path, {"main_line": "true", "hide_closed": "true"})
                    base._sleep()
                    if e_error:
                        blocker = {"scope": "sport_date", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "EVENT_FETCH_FAILED", "reason_code": e_error, "http_status": e_status}
                        coverage.append(blocker); blockers.append(blocker)
                        continue

                    base_events = (e_payload.get("events") if isinstance(e_payload, dict) else None) or []
                    relevant: list[dict[str, Any]] = []
                    for event in base_events:
                        when = base._parse_dt(event.get("event_date") if isinstance(event, dict) else None)
                        if when is not None and started <= when <= end:
                            relevant.append(event)
                    coverage.append({"scope": "sport_date", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "SCANNED", "events_returned": len(base_events), "upcoming_events": len(relevant)})
                    if not relevant:
                        continue

                    merged: dict[str, dict[str, Any]] = {}
                    base._merge_events(merged, {"events": relevant})
                    market_path = f"/api/v2/sports/{sport_id}/markets/{date}"
                    av_status, av_payload, av_error = _page_json(page, market_path)
                    base._sleep()
                    if av_error:
                        blocker = {"scope": "sport_date_market_inventory", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "MARKET_INVENTORY_FAILED", "reason_code": av_error, "http_status": av_status}
                        coverage.append(blocker); blockers.append(blocker)
                        for eid in merged:
                            event_blockers.setdefault((sport_id, eid), []).append(blocker)
                    else:
                        market_ids = base._available_ids(av_payload, sport_id, catalog)
                        batches = base._chunked(market_ids, base.MAX_MARKETS_PER_REQUEST)
                        coverage.append({"scope": "sport_date_market_inventory", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "SCANNED", "available_pregame_markets": len(market_ids), "batch_count": len(batches)})
                        for batch in batches:
                            b_status, b_payload, b_error = _page_json(page, base_path, {"market_ids": ",".join(str(mid) for mid in batch), "main_line": "true", "hide_closed": "true"})
                            base._sleep()
                            if b_error:
                                blocker = {"scope": "sport_date_market_batch", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "market_ids": batch, "status": "ODDS_FETCH_FAILED", "reason_code": b_error, "http_status": b_status}
                                coverage.append(blocker); blockers.append(blocker)
                                for eid in merged:
                                    event_blockers.setdefault((sport_id, eid), []).append(blocker)
                                continue
                            base._merge_events(merged, b_payload)

                    for eid, event in merged.items():
                        key = (sport_id, eid)
                        events_all[key] = event
                        rows = base._dedupe_rows(base._price_rows(event, catalog, affiliates))
                        rows_by_event[key] = rows
                        bookmakers.update(str(row["bookmaker"]) for row in rows if row.get("bookmaker"))
                        coverage.append({
                            "scope": "event", "sport_id": sport_id, "sport": sport_name,
                            "sport_key": skey, "event_id": eid,
                            "status": "PARTIAL_SOURCE_BLOCKED" if event_blockers.get(key) else "SCANNED",
                            "market_rows": len(rows),
                            "prop_rows": sum(1 for row in rows if row.get("is_prop")),
                            "team_rows": sum(1 for row in rows if not row.get("is_prop")),
                        })
        finally:
            context.close(); browser.close()

    sports_by_id = {int(row["sport_id"]): str(row.get("sport_name") or row["sport_id"]) for row in sports}
    team_candidates: list[dict[str, Any]] = []
    prop_candidates: list[dict[str, Any]] = []
    for (sport_id, eid), event in events_all.items():
        sport_name = sports_by_id.get(sport_id, str(sport_id))
        skey = base._sport_key(sport_name, sport_id)
        home, away, _ = base._team_names(event)
        identity = {
            "official_event_id": eid, "sport_key": skey, "sport_title": sport_name,
            "commence_time": event.get("event_date"), "home_team": home, "away_team": away,
        }
        rows = rows_by_event.get((sport_id, eid), [])
        team_rows = [row for row in rows if not row.get("is_prop")]
        prop_rows = [row for row in rows if row.get("is_prop")]
        eblock = event_blockers.get((sport_id, eid), [])
        scripts = base.game_scripts(skey)
        team_candidates.append(base._team_event_candidate(identity, scripts, team_rows, eblock))
        for row in prop_rows:
            prop_candidates.append({
                **identity, "route": "WOW_PROP_LANE", "discovery_status": "DISCOVERY_ONLY",
                "research_ceiling": base.RESEARCH_CEILING, "market_evidence": row,
                "market_evidence_status": "AVAILABLE", "game_script_hypotheses": scripts,
                "game_scripts_are_evidence_only": True, "scout_team": list(base.SCOUT_TEAM),
                "contrarian_review_required": True, "canonicalization_required": True,
            })

    status = "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS" if blockers else "DISCOVERY_COMPLETE"
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1",
        "status": status, "generated_at": base.iso(base.utc_now()),
        "window": {"from": base.iso(started), "to": base.iso(end)},
        "provider": "THERUNDOWN_AUTHENTICATED_BOARD",
        "acquisition_transport": "PLAYWRIGHT_EXACT_PAGE_FETCH",
        "acquisition_budget": {"market_ids_per_request": base.MAX_MARKETS_PER_REQUEST, "request_delay_ms": base.REQUEST_DELAY_MS, "main_line_only": True},
        "sportsbook_coverage": {
            "mode": "ALL_MAIN_LINES_RETURNED_BY_AUTHENTICATED_THERUNDOWN_BOARD",
            "bookmakers_seen": sorted(bookmakers), "bookmaker_count": len(bookmakers),
            "note": "Sportsbook prices are market evidence only; no sportsbook probability is a WOW model probability.",
        },
        "scout_team": list(base.SCOUT_TEAM), "coverage": coverage, "source_blockers": blockers,
        "model_handoff": {
            "parallel_discovery_router_required": True, "slate_integrity_required": True,
            "deduplication_required": True, "no_silent_drop": True,
            "prop_candidates": prop_candidates, "team_event_candidates": team_candidates,
        },
        "governance": {
            "sportsbook_implied_probability_is_model_probability": False,
            "scout_consensus_is_model_probability": False,
            "upset_alert_requires_governed_llp_probability": True,
            "market_evidence_is_separate_from_sporting_probability": True,
            "v17_terminal_reducer_is_terminal_authority": True, "can_execute": False,
        },
        "can_execute": False, "prediction_authority": False,
        "research_ceiling": base.RESEARCH_CEILING,
        "model_handoff_ready": bool(team_candidates or prop_candidates) or not blockers,
    }


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
        "status": payload.get("status"), "provider": payload.get("provider"),
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
