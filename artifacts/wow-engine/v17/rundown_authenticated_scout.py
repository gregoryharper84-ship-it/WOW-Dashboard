"""TheRundown-first acquisition for WOW V17 Multi-Scout.

Playwright is used only to establish the user's authorized TheRundown web
session. BrowserContext.request then reads the same first-party JSON resources
used by the odds app. External prices remain evidence only: this module has no
probability, publication, staking, approval, or execution authority.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from playwright.sync_api import APIResponse, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from v17.nightly_multiscout import SCOUT_TEAM, _team_event_candidate, game_scripts, iso, utc_now

CAN_EXECUTE = False
PREDICTION_AUTHORITY = False
RESEARCH_CEILING = "RESEARCH_INTEREST"
BOARD_URL = os.getenv("WOW_RUNDOWN_BOARD_URL", "https://therundown.io/odds/")
LOGIN_URL = os.getenv("WOW_RUNDOWN_LOGIN_URL", "https://auth.therundown.io/login/")
TIMEOUT_MS = int(os.getenv("WOW_RUNDOWN_PLAYWRIGHT_TIMEOUT_MS", "45000"))
HORIZON_HOURS = int(os.getenv("WOW_SCOUT_HORIZON_HOURS", "36"))
REQUEST_DELAY_MS = int(os.getenv("WOW_RUNDOWN_REQUEST_DELAY_MS", "150"))
MAX_MARKETS_PER_REQUEST = min(12, max(1, int(os.getenv("WOW_RUNDOWN_MARKETS_PER_REQUEST", "12"))))
MAX_RETRIES = max(1, int(os.getenv("WOW_RUNDOWN_MAX_RETRIES", "3")))

SPORT_KEY_MAP = {
    "NCAA Football": "americanfootball_ncaaf", "NFL": "americanfootball_nfl",
    "MLB": "baseball_mlb", "NBA": "basketball_nba",
    "NCAA Men's Basketball": "basketball_ncaab", "NHL": "icehockey_nhl",
    "UFC/MMA": "mma_ufc", "WNBA": "basketball_wnba", "MLS": "soccer_mls",
    "EPL": "soccer_epl", "FRA1": "soccer_fra1", "GER1": "soccer_ger1",
    "ESP1": "soccer_esp1", "ITA1": "soccer_ita1",
    "UEFACHAMP": "soccer_uefa_champions_league", "UEFAEURO": "soccer_uefa_euro",
    "FIFA": "soccer_fifa", "JPN1": "soccer_jpn1", "IPL": "cricket_ipl",
    "T20": "cricket_t20", "NBA Preseason": "basketball_nba_preseason",
    "NBA Playoffs": "basketball_nba_playoffs", "NFL Preseason": "americanfootball_nfl_preseason",
    "NFL Playoffs": "americanfootball_nfl_playoffs", "NHL Preseason": "icehockey_nhl_preseason",
    "NHL Playoffs": "icehockey_nhl_playoffs", "MLB Spring Training": "baseball_mlb_spring_training",
    "MLB Playoffs": "baseball_mlb_playoffs", "NBA Summer League": "basketball_nba_summer_league",
    "UEFA Europa League": "soccer_uefa_europa_league", "Liga MX": "soccer_liga_mx",
    "ATP": "tennis_atp", "WTA": "tennis_wta", "PGA": "golf_pga",
    "Formula 1": "motorsport_formula1",
}
NON_SPORT_NAMES = {"Politics"}
CORE_KEY_MAP = {1: "h2h", 2: "spreads", 3: "totals"}


def _first_visible(page: Page, selectors: list[str]):
    for selector in selectors:
        try:
            loc = page.locator(selector)
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _wait(page: Page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(800)


def _is_auth_page(page: Page) -> bool:
    try:
        return "auth.therundown.io" in urlsplit(page.url).netloc.lower() or bool(page.locator('input[type="password"]').count())
    except Exception:
        return False


def _login(page: Page) -> tuple[bool, str | None]:
    email = os.getenv("RUNDOWN_EMAIL", "").strip()
    password = os.getenv("RUNDOWN_PASSWORD", "")
    if not email or not password:
        return False, "RUNDOWN_AUTH_CREDENTIALS_UNCONFIGURED"
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    _wait(page)
    email_input = _first_visible(page, ['input[type="email"]', 'input[name="email"]', 'input[autocomplete="username"]'])
    password_input = _first_visible(page, ['input[type="password"]', 'input[name="password"]', 'input[autocomplete="current-password"]'])
    submit = _first_visible(page, ['button[type="submit"]', 'button:has-text("Sign In")', 'button:has-text("Log In")'])
    if email_input is None or password_input is None or submit is None:
        return False, "RUNDOWN_AUTH_FORM_UNRECOGNISED"
    email_input.fill(email)
    password_input.fill(password)
    submit.click()
    try:
        page.wait_for_url(lambda u: "auth.therundown.io" not in u, timeout=TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pass
    _wait(page)
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        body = ""
    if any(token in body for token in ("verification code", "authenticator", "captcha", "verify you are human", "one-time code")):
        return False, "RUNDOWN_AUTH_REPAIR_REQUIRED"
    return (False, "RUNDOWN_AUTH_FAILED") if _is_auth_page(page) else (True, None)


def _sleep() -> None:
    if REQUEST_DELAY_MS:
        time.sleep(REQUEST_DELAY_MS / 1000.0)


def _api_json(context, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any, str | None]:
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = "https://therundown.io" + path + ("?" + query if query else "")
    last_status = 0
    for attempt in range(MAX_RETRIES):
        try:
            response: APIResponse = context.request.get(url, timeout=TIMEOUT_MS, fail_on_status_code=False)
            last_status = int(response.status)
            if last_status == 200:
                try:
                    return last_status, response.json(), None
                except Exception:
                    return last_status, None, "RUNDOWN_JSON_INVALID"
            if last_status == 429 and attempt + 1 < MAX_RETRIES:
                time.sleep(min(8.0, 2 ** attempt))
                continue
            return last_status, None, f"RUNDOWN_HTTP_{last_status}"
        except Exception as exc:
            if attempt + 1 >= MAX_RETRIES:
                return last_status, None, f"RUNDOWN_REQUEST_{type(exc).__name__.upper()}"
            time.sleep(min(4.0, 0.5 * (2 ** attempt)))
    return last_status, None, "RUNDOWN_REQUEST_FAILED"


def _chunked(values: list[int], size: int) -> list[list[int]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _sport_key(name: str, sport_id: int) -> str:
    if name in SPORT_KEY_MAP:
        return SPORT_KEY_MAP[name]
    slug = "_".join("".join(ch.lower() if ch.isalnum() else " " for ch in name).split())
    return f"therundown_{slug or sport_id}"


def _team_names(event: dict[str, Any]) -> tuple[str | None, str | None, dict[int, str]]:
    home = away = None
    names: dict[int, str] = {}
    for team in event.get("teams") or []:
        if not isinstance(team, dict):
            continue
        name, tid = team.get("name"), team.get("team_id")
        if tid is not None and name:
            try:
                names[int(tid)] = str(name)
            except Exception:
                pass
        if team.get("is_home") is True and name:
            home = str(name)
        if team.get("is_away") is True and name:
            away = str(name)
    return home, away, names


def _market_catalog(payload: Any) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for row in payload if isinstance(payload, list) else []:
        if isinstance(row, dict) and row.get("id") is not None:
            try:
                out[int(row["id"])] = row
            except Exception:
                pass
    return out


def _available_ids(payload: Any, sport_id: int, catalog: dict[int, dict[str, Any]]) -> list[int]:
    entries = payload.get(str(sport_id)) if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    ids = []
    for row in entries:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        try:
            mid = int(row["id"])
        except Exception:
            continue
        if catalog.get(mid, {}).get("live") is not True:
            ids.append(mid)
    return sorted(set(ids))


def _affiliate_map(payload: Any) -> dict[str, str]:
    entries = payload.get("affiliates") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return {}
    out: dict[str, str] = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("affiliate_id", row.get("id"))
        name = row.get("affiliate_name") or row.get("name") or row.get("title")
        if raw_id is not None:
            out[str(raw_id)] = str(name or f"affiliate_{raw_id}")
    return out


def _price_rows(event: dict[str, Any], catalog: dict[int, dict[str, Any]], affiliates: dict[str, str]) -> list[dict[str, Any]]:
    _, _, team_names = _team_names(event)
    rows: list[dict[str, Any]] = []
    for market in event.get("markets") or []:
        if not isinstance(market, dict) or market.get("market_id") is None:
            continue
        try:
            mid = int(market["market_id"])
        except Exception:
            continue
        meta = catalog.get(mid, {})
        market_name = str(meta.get("name") or market.get("name") or f"market_{mid}")
        market_class = str(meta.get("class") or "")
        market_family = str(meta.get("family") or "")
        is_prop = market_class == "prop" or market_family == "player_ou"
        market_key = CORE_KEY_MAP.get(mid, market_name)
        for participant in market.get("participants") or []:
            if not isinstance(participant, dict):
                continue
            pid = participant.get("id")
            pname = participant.get("name") or participant.get("description")
            if not pname and pid is not None:
                try:
                    pname = team_names.get(int(pid))
                except Exception:
                    pass
            pname = str(pname or pid or "UNKNOWN_PARTICIPANT")
            for line in participant.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                point = line.get("value")
                prices = line.get("prices") or {}
                if not isinstance(prices, dict):
                    continue
                for affiliate_id, price_obj in prices.items():
                    if not isinstance(price_obj, dict) or price_obj.get("is_main_line") is not True:
                        continue
                    price = price_obj.get("price")
                    if price in (None, 0.0001, "0.0001"):
                        continue
                    book = affiliates.get(str(affiliate_id), f"affiliate_{affiliate_id}")
                    rows.append({
                        "bookmaker": book, "bookmaker_title": book,
                        "bookmaker_last_update": price_obj.get("updated_at"),
                        "market_key": market_key, "market_last_update": price_obj.get("updated_at"),
                        "outcome_name": pname, "description": pname if is_prop else None,
                        "price": price, "point": point, "link": None,
                        "source_provider": "THERUNDOWN_AUTHENTICATED_BOARD", "source_tier": "primary_board",
                        "provider_market_id": mid, "provider_participant_id": pid,
                        "provider_affiliate_id": str(affiliate_id), "provider_price_id": price_obj.get("id"),
                        "market_class": market_class, "market_family": market_family,
                        "market_scope": meta.get("scope"), "is_prop": is_prop,
                        "prediction_authority": False, "exact_line_authority": False,
                        "research_only": True, "can_execute": False,
                    })
    return rows


def _merge_events(target: dict[str, dict[str, Any]], payload: Any) -> None:
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return
    for incoming in events:
        if not isinstance(incoming, dict) or not incoming.get("event_id"):
            continue
        eid = str(incoming["event_id"])
        current = target.setdefault(eid, {k: v for k, v in incoming.items() if k != "markets"})
        current.setdefault("markets", [])
        seen = {(m.get("market_id"), m.get("id")) for m in current["markets"] if isinstance(m, dict)}
        for market in incoming.get("markets") or []:
            if not isinstance(market, dict):
                continue
            key = (market.get("market_id"), market.get("id"))
            if key not in seen:
                current["markets"].append(market)
                seen.add(key)


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = (row.get("provider_market_id"), row.get("provider_participant_id"), row.get("point"), row.get("provider_affiliate_id"), row.get("price"))
        out[key] = row
    return list(out.values())


def _date_strings(start: datetime, end: datetime) -> list[str]:
    dates, day = [], start.date()
    while day <= end.date():
        dates.append(day.isoformat())
        day += timedelta(days=1)
    return dates


def _blocked(reason: str, coverage: list[dict[str, Any]], blockers: list[dict[str, Any]], http_status: int | None = None) -> dict[str, Any]:
    blocker = {"scope": "therundown_authenticated_board", "status": "BLOCKED_SOURCE_ACQUISITION", "reason_code": reason, "http_status": http_status}
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1", "status": "BLOCKED_SOURCE_ACQUISITION",
        "reason_code": reason, "source_http_status": http_status, "generated_at": iso(utc_now()),
        "provider": "THERUNDOWN_AUTHENTICATED_BOARD", "can_execute": False,
        "prediction_authority": False, "research_ceiling": RESEARCH_CEILING,
        "model_handoff_ready": False, "coverage": coverage, "source_blockers": [*blockers, blocker],
        "model_handoff": {"prop_candidates": [], "team_event_candidates": []},
    }


def run() -> dict[str, Any]:
    started, end = utc_now(), utc_now() + timedelta(hours=HORIZON_HOURS)
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
        page.set_default_timeout(TIMEOUT_MS)
        try:
            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            _wait(page)
            ok, reason = _login(page)
            if not ok:
                return _blocked(reason or "RUNDOWN_AUTH_FAILED", coverage, blockers)
            page.goto(BOARD_URL, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            _wait(page)
            if _is_auth_page(page):
                return _blocked("RUNDOWN_AUTH_FAILED", coverage, blockers)

            s_status, s_payload, s_error = _api_json(context, "/api/v1/sports")
            m_status, m_payload, m_error = _api_json(context, "/api/v2/markets")
            a_status, a_payload, _ = _api_json(context, "/api/v1/affiliates")
            if s_error or not isinstance(s_payload, dict) or not isinstance(s_payload.get("sports"), list):
                return _blocked(s_error or "RUNDOWN_SPORT_INVENTORY_INVALID", coverage, blockers, s_status)
            if m_error or not isinstance(m_payload, list):
                return _blocked(m_error or "RUNDOWN_MARKET_CATALOG_INVALID", coverage, blockers, m_status)
            catalog = _market_catalog(m_payload)
            affiliates = _affiliate_map(a_payload) if a_status == 200 else {}
            sports = [row for row in s_payload["sports"] if isinstance(row, dict) and row.get("sport_id") is not None]
            coverage.append({"scope": "provider_inventory", "status": "SCANNED", "sports_discovered": len(sports), "market_definitions": len(catalog), "affiliates_discovered": len(affiliates)})

            for sport in sports:
                sport_id, sport_name = int(sport["sport_id"]), str(sport.get("sport_name") or sport["sport_id"])
                if sport_name in NON_SPORT_NAMES:
                    coverage.append({"scope": "sport", "sport_id": sport_id, "sport": sport_name, "status": "NON_SPORT_INVENTORY_EXCLUDED"})
                    continue
                skey = _sport_key(sport_name, sport_id)
                for date in _date_strings(started, end):
                    base_path = f"/api/v2/sports/{sport_id}/events/{date}"
                    e_status, e_payload, e_error = _api_json(context, base_path, {"main_line": "true", "hide_closed": "true"})
                    _sleep()
                    if e_error:
                        blocker = {"scope": "sport_date", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "EVENT_FETCH_FAILED", "reason_code": e_error, "http_status": e_status}
                        coverage.append(blocker); blockers.append(blocker)
                        continue
                    base_events = (e_payload.get("events") if isinstance(e_payload, dict) else None) or []
                    relevant = []
                    for event in base_events:
                        when = _parse_dt(event.get("event_date") if isinstance(event, dict) else None)
                        if when is not None and started <= when <= end:
                            relevant.append(event)
                    coverage.append({"scope": "sport_date", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "SCANNED", "events_returned": len(base_events), "upcoming_events": len(relevant)})
                    if not relevant:
                        continue

                    merged: dict[str, dict[str, Any]] = {}
                    _merge_events(merged, {"events": relevant})
                    market_path = f"/api/v2/sports/{sport_id}/markets/{date}"
                    av_status, av_payload, av_error = _api_json(context, market_path)
                    _sleep()
                    if av_error:
                        blocker = {"scope": "sport_date_market_inventory", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "MARKET_INVENTORY_FAILED", "reason_code": av_error, "http_status": av_status}
                        coverage.append(blocker); blockers.append(blocker)
                        for eid in merged:
                            event_blockers.setdefault((sport_id, eid), []).append(blocker)
                    else:
                        market_ids = _available_ids(av_payload, sport_id, catalog)
                        batches = _chunked(market_ids, MAX_MARKETS_PER_REQUEST)
                        coverage.append({"scope": "sport_date_market_inventory", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "status": "SCANNED", "available_pregame_markets": len(market_ids), "batch_count": len(batches)})
                        for batch in batches:
                            b_status, b_payload, b_error = _api_json(context, base_path, {"market_ids": ",".join(str(mid) for mid in batch), "main_line": "true", "hide_closed": "true"})
                            _sleep()
                            if b_error:
                                blocker = {"scope": "sport_date_market_batch", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "date": date, "market_ids": batch, "status": "ODDS_FETCH_FAILED", "reason_code": b_error, "http_status": b_status}
                                coverage.append(blocker); blockers.append(blocker)
                                for eid in merged:
                                    event_blockers.setdefault((sport_id, eid), []).append(blocker)
                                continue
                            _merge_events(merged, b_payload)

                    for eid, event in merged.items():
                        key = (sport_id, eid)
                        events_all[key] = event
                        rows = _dedupe_rows(_price_rows(event, catalog, affiliates))
                        rows_by_event[key] = rows
                        bookmakers.update(str(row["bookmaker"]) for row in rows if row.get("bookmaker"))
                        coverage.append({"scope": "event", "sport_id": sport_id, "sport": sport_name, "sport_key": skey, "event_id": eid, "status": "PARTIAL_SOURCE_BLOCKED" if event_blockers.get(key) else "SCANNED", "market_rows": len(rows), "prop_rows": sum(1 for row in rows if row.get("is_prop")), "team_rows": sum(1 for row in rows if not row.get("is_prop"))})
        finally:
            context.close(); browser.close()

    sports_by_id = {int(row["sport_id"]): str(row.get("sport_name") or row["sport_id"]) for row in sports}
    team_candidates: list[dict[str, Any]] = []
    prop_candidates: list[dict[str, Any]] = []
    for (sport_id, eid), event in events_all.items():
        sport_name = sports_by_id.get(sport_id, str(sport_id))
        skey = _sport_key(sport_name, sport_id)
        home, away, _ = _team_names(event)
        identity = {"official_event_id": eid, "sport_key": skey, "sport_title": sport_name, "commence_time": event.get("event_date"), "home_team": home, "away_team": away}
        rows = rows_by_event.get((sport_id, eid), [])
        team_rows = [row for row in rows if not row.get("is_prop")]
        prop_rows = [row for row in rows if row.get("is_prop")]
        eblock = event_blockers.get((sport_id, eid), [])
        scripts = game_scripts(skey)
        team_candidates.append(_team_event_candidate(identity, scripts, team_rows, eblock))
        for row in prop_rows:
            prop_candidates.append({**identity, "route": "WOW_PROP_LANE", "discovery_status": "DISCOVERY_ONLY", "research_ceiling": RESEARCH_CEILING, "market_evidence": row, "market_evidence_status": "AVAILABLE", "game_script_hypotheses": scripts, "game_scripts_are_evidence_only": True, "scout_team": list(SCOUT_TEAM), "contrarian_review_required": True, "canonicalization_required": True})

    status = "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS" if blockers else "DISCOVERY_COMPLETE"
    return {
        "schema_version": "wow.v17.nightly_multiscout.v1", "status": status,
        "generated_at": iso(utc_now()), "window": {"from": iso(started), "to": iso(end)},
        "provider": "THERUNDOWN_AUTHENTICATED_BOARD",
        "acquisition_budget": {"market_ids_per_request": MAX_MARKETS_PER_REQUEST, "request_delay_ms": REQUEST_DELAY_MS, "main_line_only": True},
        "sportsbook_coverage": {"mode": "ALL_MAIN_LINES_RETURNED_BY_AUTHENTICATED_THERUNDOWN_BOARD", "bookmakers_seen": sorted(bookmakers), "bookmaker_count": len(bookmakers), "note": "Sportsbook prices are market evidence only; no sportsbook probability is a WOW model probability."},
        "scout_team": list(SCOUT_TEAM), "coverage": coverage, "source_blockers": blockers,
        "model_handoff": {"parallel_discovery_router_required": True, "slate_integrity_required": True, "deduplication_required": True, "no_silent_drop": True, "prop_candidates": prop_candidates, "team_event_candidates": team_candidates},
        "governance": {"sportsbook_implied_probability_is_model_probability": False, "scout_consensus_is_model_probability": False, "upset_alert_requires_governed_llp_probability": True, "market_evidence_is_separate_from_sporting_probability": True, "v17_terminal_reducer_is_terminal_authority": True, "can_execute": False},
        "can_execute": False, "prediction_authority": False, "research_ceiling": RESEARCH_CEILING,
        "model_handoff_ready": bool(team_candidates or prop_candidates) or not blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", required=True); args = parser.parse_args()
    payload = run(); path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    handoff = payload.get("model_handoff") or {}
    print(json.dumps({"status": payload.get("status"), "provider": payload.get("provider"), "team_event_candidates": len(handoff.get("team_event_candidates") or []), "prop_candidates": len(handoff.get("prop_candidates") or []), "source_blockers": len(payload.get("source_blockers") or []), "bookmakers": (payload.get("sportsbook_coverage") or {}).get("bookmaker_count", 0), "can_execute": False}, sort_keys=True))
    return 0 if payload.get("status") in {"DISCOVERY_COMPLETE", "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"} and payload.get("model_handoff_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
