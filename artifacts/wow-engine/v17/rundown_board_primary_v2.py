"""TheRundown board-primary acquisition adapter, v2.

The V2 provider contract limits ``market_ids`` to 12 per request.  Instead of
spraying the global market catalog at every sport/date, this adapter:

1. discovers the provider sports catalog;
2. retrieves the dated event slate using the provider defaults;
3. asks ``/api/v2/events/{event_id}/markets`` which markets are actually
   available for each event;
4. fetches only those active market IDs, in batches of at most 12;
5. merges those prices into one cached board snapshot that Scout uses for its
   event, inventory, and odds reads.

This is acquisition only.  Sportsbook prices remain evidence, never WOW model
probabilities.  Team/event rows remain routed by Nightly Multi-Scout to
LLP_TEAM_BETTING_ENGINE, props remain routed to WOW_PROP_LANE, controlling
specialists remain probability authority, and ``can_execute`` is always false.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CAN_EXECUTE = False
BASE_URL = os.environ.get("WOW_RUNDOWN_BASE_URL", "https://therundown.io").rstrip("/")
TIMEOUT_SECONDS = float(os.environ.get("WOW_RUNDOWN_BOARD_TIMEOUT_SECONDS", "25"))
# Provider V2 hard limit observed in credentialed production smoke: <= 12.
MARKET_CHUNK_SIZE = min(12, max(1, int(os.environ.get("WOW_RUNDOWN_BOARD_MARKET_CHUNK_SIZE", "12"))))
MAX_DATES = max(1, int(os.environ.get("WOW_RUNDOWN_BOARD_MAX_DATES", "3")))
REQUEST_DELAY_SECONDS = max(0.0, float(os.environ.get("WOW_RUNDOWN_BOARD_REQUEST_DELAY_SECONDS", "0.55")))

_SPORT_EVENTS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events$")
_EVENT_MARKETS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events/([^/]+)/markets$")
_EVENT_ODDS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events/([^/]+)/odds$")
_STANDARD_MARKETS = {"1": "h2h", "2": "spreads", "3": "totals", "41": "h2h_live", "42": "spreads_live", "43": "totals_live"}
_SPORT_ALIASES = {
    "nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf",
    "college football": "americanfootball_ncaaf", "cfb": "americanfootball_ncaaf",
    "nba": "basketball_nba", "wnba": "basketball_wnba",
    "ncaab": "basketball_ncaab", "ncaamb": "basketball_ncaab",
    "college basketball": "basketball_ncaab", "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl", "ufc": "mma_mixed_martial_arts",
    "mma": "mma_mixed_martial_arts", "atp": "tennis_atp", "wta": "tennis_wta",
    "mls": "soccer_usa_mls", "epl": "soccer_epl",
    "english premier league": "soccer_epl", "la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a", "bundesliga": "soccer_germany_bundesliga",
    "ligue 1": "soccer_france_ligue_one",
}


@dataclass
class BoardResult:
    ok: bool
    data: Any = None
    status: int | None = None
    code: str | None = None


_lock = threading.RLock()
_catalog_cache: dict[str, Any] = {}
_sport_key_to_id: dict[str, str] = {}
_event_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
_event_index: dict[tuple[str, str], dict[str, Any]] = {}
_last_request_started = 0.0


def reset_cache() -> None:
    global _last_request_started
    with _lock:
        _catalog_cache.clear(); _sport_key_to_id.clear(); _event_cache.clear(); _event_index.clear()
        _last_request_started = 0.0


def enabled() -> bool:
    return os.environ.get("WOW_RUNDOWN_BOARD_PRIMARY_ENABLED", "true").strip().lower() == "true"


def _credential() -> str | None:
    for name in ("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _governance_marker() -> dict[str, Any]:
    return {"provider": "RUNDOWN_AUTHENTICATED_BOARD", "provider_detail": "THERUNDOWN_PRODUCT_V2_ACTIVE_MARKET_MIRROR", "source_tier": "SPORTSBOOK_FEED_RESEARCH", "prediction_authority": False, "exact_line_authority": False, "research_only": True, "market_role_evidence_only": True, "can_execute": False}


def _sport_key(row: dict[str, Any]) -> str:
    name = str(row.get("sport_name") or row.get("name") or row.get("title") or row.get("league") or "").strip()
    lowered = name.lower()
    if lowered in _SPORT_ALIASES:
        return _SPORT_ALIASES[lowered]
    for alias, canonical in _SPORT_ALIASES.items():
        if alias and alias in lowered:
            suffix = _slug(lowered.replace(alias, ""))
            return canonical if not suffix else f"{canonical}_{suffix}"
    sport_id = row.get("sport_id") or row.get("id") or "unknown"
    return f"rundown_{sport_id}_{_slug(name) or 'sport'}"


def _pace() -> None:
    global _last_request_started
    with _lock:
        now = time.monotonic()
        remaining = REQUEST_DELAY_SECONDS - (now - _last_request_started)
        if remaining > 0:
            time.sleep(remaining)
        _last_request_started = time.monotonic()


def _api_get(path: str, params: dict[str, Any] | None = None) -> BoardResult:
    key = _credential()
    if not key:
        return BoardResult(False, status=401, code="RUNDOWN_BOARD_AUTH_UNCONFIGURED")
    _pace()
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    req = Request(url, headers={"X-TheRundown-Key": key, "Accept": "application/json", "User-Agent": "WOW-V17-Scout-BoardMirror/2.0"})
    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            return BoardResult(True, json.loads(response.read().decode("utf-8")), response.status)
    except HTTPError as exc:
        code = f"RUNDOWN_BOARD_HTTP_{exc.code}"
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict):
                detail = payload.get("code") or payload.get("error") or payload.get("message")
                if detail: code = str(detail)
        except Exception:
            pass
        if exc.code in {401, 403}: code = "RUNDOWN_BOARD_AUTH_OR_ENTITLEMENT_REJECTED"
        elif exc.code == 429: code = "RUNDOWN_BOARD_QUOTA_OR_RATE_LIMIT"
        return BoardResult(False, status=exc.code, code=code)
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return BoardResult(False, code=f"RUNDOWN_BOARD_{type(exc).__name__}")


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list): return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list): return [r for r in value if isinstance(r, dict)]
        data = payload.get("data")
        if isinstance(data, dict): return _rows(data, *keys)
    return []


def _catalog(name: str, path: str, *keys: str) -> BoardResult:
    with _lock:
        if name in _catalog_cache: return BoardResult(True, _catalog_cache[name], 200)
    result = _api_get(path)
    if not result.ok: return result
    values = _rows(result.data, *keys)
    if not values: return BoardResult(False, status=result.status, code=f"RUNDOWN_BOARD_{name.upper()}_CATALOG_EMPTY")
    with _lock: _catalog_cache[name] = values
    return BoardResult(True, values, result.status)


def sports_catalog() -> BoardResult:
    result = _catalog("sports", "/api/v2/sports", "sports")
    if not result.ok: return result
    output = []
    with _lock:
        for row in result.data:
            sport_id = row.get("sport_id") or row.get("id")
            if sport_id is None: continue
            key = _sport_key(row); _sport_key_to_id[key] = str(sport_id)
            output.append({"key": key, "title": str(row.get("sport_name") or row.get("name") or row.get("title") or key), "active": row.get("active", row.get("is_active", True)) is not False, "_wow_rundown_sport_id": str(sport_id), "_wow_market_evidence": _governance_marker()})
    return BoardResult(bool(output), output, result.status, None if output else "RUNDOWN_BOARD_SPORT_MAPPING_EMPTY")


def affiliates_catalog() -> BoardResult:
    return _catalog("affiliates", "/api/v2/affiliates", "affiliates")


def _affiliate_ids() -> BoardResult:
    result = affiliates_catalog()
    if not result.ok: return result
    ids = sorted({str(r.get("affiliate_id") or r.get("id")) for r in result.data if (r.get("affiliate_id") or r.get("id")) is not None and str(r.get("affiliate_id") or r.get("id")) != "27"})
    return BoardResult(True, ids, result.status)


def _number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool): return None
    try:
        n = float(value); return int(n) if n.is_integer() else n
    except (TypeError, ValueError): return None


def _teams(event: dict[str, Any]) -> tuple[str | None, str | None]:
    home = away = None
    for row in event.get("teams") or event.get("teams_normalized") or event.get("participants") or []:
        if not isinstance(row, dict): continue
        name = row.get("name") or row.get("team_name") or row.get("full_name")
        side = str(row.get("type") or row.get("side") or "").lower()
        if row.get("is_home") is True or side == "home": home = home or (str(name) if name else None)
        elif row.get("is_away") is True or side == "away": away = away or (str(name) if name else None)
    return home, away


def _market_key(market: dict[str, Any]) -> str:
    market_id = str(market.get("market_id") or market.get("id") or "")
    if market_id in _STANDARD_MARKETS: return _STANDARD_MARKETS[market_id]
    key = _slug(market.get("name") or market.get("market_name") or market.get("title") or f"market_{market_id}")
    family = _slug(market.get("type") or market.get("category") or market.get("market_type"))
    if "player" in family and not key.startswith("player_"): key = f"player_{key}"
    return key or f"rundown_market_{market_id}"


def _market_definitions(payload: Any) -> dict[str, str]:
    mapping = {}
    for market in _rows(payload, "markets"):
        mid = market.get("market_id") or market.get("id")
        if mid is not None: mapping[str(mid)] = _market_key(market)
    return mapping


def _participant_name(participant: dict[str, Any], home: str | None, away: str | None) -> str | None:
    name = participant.get("name") or participant.get("full_name") or participant.get("team_name")
    if name: return str(name)
    side = str(participant.get("type") or participant.get("side") or "").lower()
    return home if side == "home" else away if side == "away" else None


def _translate_event(raw: dict[str, Any], sport_key: str, market_map: dict[str, str]) -> dict[str, Any] | None:
    eid = raw.get("event_id") or raw.get("id") or raw.get("event_uuid")
    if eid is None: return None
    home, away = _teams(raw); books: dict[str, dict[str, Any]] = {}
    for market in raw.get("markets") or []:
        if not isinstance(market, dict): continue
        mid = str(market.get("market_id") or market.get("id") or "")
        mkey = market_map.get(mid) or _market_key(market)
        for participant in market.get("participants") or []:
            if not isinstance(participant, dict): continue
            pname = _participant_name(participant, home, away)
            for line in participant.get("lines") or []:
                if not isinstance(line, dict): continue
                selection = line.get("selection") or line.get("name") or line.get("side") or pname
                if not selection: continue
                prices = line.get("prices") or {}
                for aid, container in (prices.items() if isinstance(prices, dict) else []):
                    if isinstance(container, dict):
                        price = _number(container.get("price") if container.get("price") is not None else container.get("american") or container.get("american_odds")); title = container.get("affiliate_name") or container.get("sportsbook") or container.get("book_name"); updated = container.get("updated_at") or container.get("date_updated")
                    else: price = _number(container); title = None; updated = None
                    if price is None or abs(float(price) - 0.0001) < 1e-9: continue
                    bkey = f"rundown_{_slug(title or aid)}"; book = books.setdefault(bkey, {"key": bkey, "title": str(title or f"affiliate_{aid}"), "last_update": updated, "markets": {}})
                    bucket = book["markets"].setdefault(mkey, {"key": mkey, "last_update": updated, "outcomes": []})
                    outcome: dict[str, Any] = {"name": str(selection), "price": price}
                    point = _number(line.get("value") if line.get("value") is not None else line.get("line") or line.get("point"))
                    if point is not None: outcome["point"] = point
                    desc = participant.get("name") or participant.get("full_name")
                    if desc and str(desc) != str(selection): outcome["description"] = str(desc)
                    if outcome not in bucket["outcomes"]: bucket["outcomes"].append(outcome)
    return {"id": f"rundown-{eid}", "sport_key": sport_key, "commence_time": raw.get("event_date") or raw.get("start_time") or raw.get("commence_time"), "home_team": home, "away_team": away, "bookmakers": [{"key": b["key"], "title": b["title"], "last_update": b["last_update"], "markets": list(b["markets"].values())} for b in books.values() if b["markets"]], "_wow_market_evidence": _governance_marker()}


def _merge_event(target: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(target); by_book = {str(b.get("key")): dict(b) for b in target.get("bookmakers") or [] if isinstance(b, dict)}
    for book in incoming.get("bookmakers") or []:
        if not isinstance(book, dict): continue
        bk = str(book.get("key"))
        if bk not in by_book: by_book[bk] = dict(book); continue
        current = by_book[bk]; by_market = {str(m.get("key")): dict(m) for m in current.get("markets") or [] if isinstance(m, dict)}
        for market in book.get("markets") or []:
            if not isinstance(market, dict): continue
            mk = str(market.get("key"))
            if mk not in by_market: by_market[mk] = dict(market); continue
            dest = by_market[mk]; outcomes = list(dest.get("outcomes") or [])
            for outcome in market.get("outcomes") or []:
                if outcome not in outcomes: outcomes.append(outcome)
            dest["outcomes"] = outcomes; by_market[mk] = dest
        current["markets"] = list(by_market.values()); by_book[bk] = current
    merged["bookmakers"] = list(by_book.values()); return merged


def _dates(params: dict[str, Any] | None) -> list[str]:
    params = params or {}; now = datetime.now(timezone.utc)
    try: start = datetime.fromisoformat(str(params.get("commenceTimeFrom") or "").replace("Z", "+00:00")) if params.get("commenceTimeFrom") else now
    except ValueError: start = now
    try: end = datetime.fromisoformat(str(params.get("commenceTimeTo") or "").replace("Z", "+00:00")) if params.get("commenceTimeTo") else start + timedelta(hours=36)
    except ValueError: end = start + timedelta(hours=36)
    out=[]; cursor=start.date()
    while cursor <= end.date() and len(out) < MAX_DATES: out.append(cursor.isoformat()); cursor += timedelta(days=1)
    return out or [start.date().isoformat()]


def _sport_id(sport_key: str) -> BoardResult:
    with _lock:
        if sport_key in _sport_key_to_id: return BoardResult(True, _sport_key_to_id[sport_key], 200)
    sports = sports_catalog()
    if not sports.ok: return sports
    with _lock: sid = _sport_key_to_id.get(sport_key)
    return BoardResult(True, sid, 200) if sid else BoardResult(False, status=404, code="RUNDOWN_BOARD_SPORT_NOT_OFFERED")


def _core_events(sport_id: str, slate_date: str, affiliate_ids: list[str]) -> BoardResult:
    return _api_get(f"/api/v2/sports/{sport_id}/events/{slate_date}", {"affiliate_ids": ",".join(affiliate_ids), "hide_closed": "true", "include": "all_periods", "offset": "0"})


def _snapshot(sport_key: str, params: dict[str, Any] | None) -> BoardResult:
    sid = _sport_id(sport_key)
    if not sid.ok: return sid
    affiliates = _affiliate_ids()
    if not affiliates.ok: return affiliates
    dates = _dates(params); cache_key=(sport_key,"|".join(dates),"ACTIVE_MARKETS")
    with _lock:
        cached = _event_cache.get(cache_key)
        if cached is not None: return BoardResult(True, list(cached["events"]), 200)

    events_by_id: dict[str, dict[str, Any]] = {}; discovered_event_ids: set[str] = set(); available_market_ids: set[str] = set(); market_map: dict[str,str] = {}; failures=[]; requests=0
    for slate_date in dates:
        core = _core_events(str(sid.data), slate_date, affiliates.data); requests += 1
        if not core.ok: return core
        raw_core = _rows(core.data, "events")
        for raw in raw_core:
            eid = raw.get("event_id") or raw.get("id") or raw.get("event_uuid")
            if eid is None: continue
            discovered_event_ids.add(str(eid))
            definitions = _api_get(f"/api/v2/events/{eid}/markets"); requests += 1
            if not definitions.ok:
                failures.append({"event_id": str(eid), "code": definitions.code, "status": definitions.status}); continue
            defs = _market_definitions(definitions.data); market_map.update(defs); available_market_ids.update(defs)
        active_ids=sorted(available_market_ids, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else x))
        for i in range(0,len(active_ids),MARKET_CHUNK_SIZE):
            chunk=active_ids[i:i+MARKET_CHUNK_SIZE]
            if len(chunk) > 12: return BoardResult(False, code="RUNDOWN_BOARD_INTERNAL_MARKET_BATCH_OVER_12")
            priced=_api_get(f"/api/v2/sports/{sid.data}/events/{slate_date}", {"market_ids": ",".join(chunk), "affiliate_ids": ",".join(affiliates.data), "main_line": "false", "hide_closed": "true", "include": "all_periods", "offset": "0"}); requests += 1
            if not priced.ok: return priced
            for raw in _rows(priced.data,"events"):
                translated=_translate_event(raw,sport_key,market_map)
                if translated:
                    events_by_id[translated["id"]]=_merge_event(events_by_id[translated["id"]],translated) if translated["id"] in events_by_id else translated

    priced_event_ids={str(eid).replace("rundown-","") for eid in events_by_id}
    priced_market_keys={str(m.get("key")) for e in events_by_id.values() for b in e.get("bookmakers") or [] for m in b.get("markets") or [] if isinstance(m,dict) and m.get("key")}
    available_market_keys={market_map[mid] for mid in available_market_ids if mid in market_map}
    missing_events=sorted(discovered_event_ids-priced_event_ids)
    missing_markets=sorted(available_market_keys-priced_market_keys)
    coverage_complete=not failures and not missing_events and not missing_markets
    audit={"provider":"RUNDOWN_AUTHENTICATED_BOARD","status":"COMPLETE" if coverage_complete else "INCOMPLETE","sport_id":str(sid.data),"dates":dates,"board_event_count":len(discovered_event_ids),"normalized_event_count":len(events_by_id),"available_market_count":len(available_market_keys),"priced_market_count":len(priced_market_keys),"missing_event_count":len(missing_events),"missing_market_count":len(missing_markets),"missing_events_sample":missing_events[:10],"missing_markets_sample":missing_markets[:20],"market_discovery_failures":failures[:20],"requests_made":requests,"market_batch_size":MARKET_CHUNK_SIZE,"coverage_complete":coverage_complete,"prediction_authority":False,"market_role_evidence_only":True,"can_execute":False}
    if not coverage_complete:
        return BoardResult(False, data={"audit":audit,"events":list(events_by_id.values())}, status=206, code="RUNDOWN_BOARD_COVERAGE_INCOMPLETE")
    events=list(events_by_id.values())
    for event in events:
        event["_wow_rundown_board_audit"]=dict(audit)
        with _lock: _event_index[(sport_key,str(event["id"]))]=event
    with _lock: _event_cache[cache_key]={"events":list(events),"audit":audit}
    return BoardResult(True,events,200)


def _filter_event(event: dict[str,Any], requested:set[str]|None=None)->dict[str,Any]:
    out=dict(event)
    if not requested: return out
    books=[]
    for book in event.get("bookmakers") or []:
        if not isinstance(book,dict): continue
        markets=[dict(m) for m in book.get("markets") or [] if isinstance(m,dict) and str(m.get("key")) in requested]
        if markets: copy=dict(book); copy["markets"]=markets; books.append(copy)
    out["bookmakers"]=books; return out


def serve(path:str, params:dict[str,Any]|None=None)->BoardResult|None:
    if not enabled(): return None
    if path=="/odds-api/v4/sports": return sports_catalog()
    match=_SPORT_EVENTS_RE.match(path)
    if match: return _snapshot(match.group(1),params)
    match=_EVENT_MARKETS_RE.match(path)
    if match:
        sport,eid=match.groups()
        with _lock: event=_event_index.get((sport,eid))
        if event is None:
            snap=_snapshot(sport,params)
            if not snap.ok: return snap
            with _lock: event=_event_index.get((sport,eid))
        return BoardResult(True,_filter_event(event),200) if event else BoardResult(False,status=404,code="RUNDOWN_BOARD_EVENT_NOT_IN_SNAPSHOT")
    match=_EVENT_ODDS_RE.match(path)
    if match:
        sport,eid=match.groups()
        with _lock: event=_event_index.get((sport,eid))
        if event is None:
            snap=_snapshot(sport,params)
            if not snap.ok: return snap
            with _lock: event=_event_index.get((sport,eid))
        if event is None: return BoardResult(False,status=404,code="RUNDOWN_BOARD_EVENT_NOT_IN_SNAPSHOT")
        requested={v.strip() for v in str((params or {}).get("markets") or "").split(",") if v.strip()}
        return BoardResult(True,_filter_event(event,requested or None),200)
    return None


__all__=["BoardResult","CAN_EXECUTE","MARKET_CHUNK_SIZE","enabled","reset_cache","serve","sports_catalog"]
