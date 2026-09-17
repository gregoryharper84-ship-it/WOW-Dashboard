"""TheRundown board-primary acquisition adapter for WOW V17 Scout.

This module mirrors the current TheRundown odds board through the documented
Product V2 data contract and serves it to Nightly Multi-Scout in the
Odds-API-v4-shaped interface Scout already understands.

Governance is intentionally narrow:
- sportsbook prices are evidence only;
- no sportsbook implied probability becomes a WOW model probability;
- team/event candidates continue to LLP_TEAM_BETTING_ENGINE;
- prop candidates continue to WOW_PROP_LANE;
- the controlling specialist remains probability authority;
- can_execute is always false.

The adapter is board-primary, not model-primary.  It discovers the provider's
current sports/market/affiliate catalogs at runtime, snapshots the requested
sport/date window, caches that snapshot for the run, and answers Scout's
sports/events/market-inventory/odds reads from the same snapshot.  This avoids
mixing a TheRundown event identity with another provider's event-scoped route.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CAN_EXECUTE = False
BASE_URL = os.environ.get("WOW_RUNDOWN_BASE_URL", "https://therundown.io").rstrip("/")
TIMEOUT_SECONDS = float(os.environ.get("WOW_RUNDOWN_BOARD_TIMEOUT_SECONDS", "25"))
MARKET_CHUNK_SIZE = max(1, int(os.environ.get("WOW_RUNDOWN_BOARD_MARKET_CHUNK_SIZE", "40")))
MAX_DATES = max(1, int(os.environ.get("WOW_RUNDOWN_BOARD_MAX_DATES", "3")))

_SPORT_EVENTS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events$")
_EVENT_MARKETS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events/([^/]+)/markets$")
_EVENT_ODDS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events/([^/]+)/odds$")

_STANDARD_MARKETS = {"1": "h2h", "2": "spreads", "3": "totals"}

# Stable aliases for the sport families that already have V17 specialists.
# Unknown TheRundown sports are still exposed with a deterministic provider key;
# they are not silently dropped merely because a specialist is unavailable.
_SPORT_ALIASES = {
    "nfl": "americanfootball_nfl",
    "ncaaf": "americanfootball_ncaaf",
    "college football": "americanfootball_ncaaf",
    "cfb": "americanfootball_ncaaf",
    "nba": "basketball_nba",
    "wnba": "basketball_wnba",
    "ncaab": "basketball_ncaab",
    "ncaamb": "basketball_ncaab",
    "college basketball": "basketball_ncaab",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
    "ufc": "mma_mixed_martial_arts",
    "mma": "mma_mixed_martial_arts",
    "atp": "tennis_atp",
    "wta": "tennis_wta",
    "mls": "soccer_usa_mls",
    "epl": "soccer_epl",
    "english premier league": "soccer_epl",
    "la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a",
    "bundesliga": "soccer_germany_bundesliga",
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
_sport_key_to_title: dict[str, str] = {}
_event_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
_event_index: dict[tuple[str, str], dict[str, Any]] = {}


def reset_cache() -> None:
    with _lock:
        _catalog_cache.clear()
        _sport_key_to_id.clear()
        _sport_key_to_title.clear()
        _event_cache.clear()
        _event_index.clear()


def enabled() -> bool:
    return os.environ.get("WOW_RUNDOWN_BOARD_PRIMARY_ENABLED", "true").strip().lower() == "true"


def _credential() -> str | None:
    for name in ("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return text


def _sport_key(row: dict[str, Any]) -> str:
    raw_name = row.get("sport_name") or row.get("name") or row.get("title") or row.get("league") or ""
    name = str(raw_name).strip()
    lowered = name.lower()
    if lowered in _SPORT_ALIASES:
        return _SPORT_ALIASES[lowered]
    # Season variants retain the controlling family prefix when recognizable.
    for alias, canonical in _SPORT_ALIASES.items():
        if alias and alias in lowered:
            suffix = _slug(lowered.replace(alias, ""))
            return canonical if not suffix else f"{canonical}_{suffix}"
    sport_id = row.get("sport_id") or row.get("id") or "unknown"
    return f"rundown_{sport_id}_{_slug(name) or 'sport'}"


def _api_get(path: str, params: dict[str, Any] | None = None) -> BoardResult:
    key = _credential()
    if not key:
        return BoardResult(False, status=401, code="RUNDOWN_BOARD_AUTH_UNCONFIGURED")
    query = urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    url = f"{BASE_URL}{path}" + (f"?{query}" if query else "")
    request = Request(
        url,
        headers={
            "X-TheRundown-Key": key,
            "Accept": "application/json",
            "User-Agent": "WOW-V17-Scout-BoardMirror/1.0",
        },
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return BoardResult(True, json.loads(response.read().decode("utf-8")), response.status)
    except HTTPError as exc:
        code = f"RUNDOWN_BOARD_HTTP_{exc.code}"
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            if isinstance(payload, dict):
                detail = payload.get("code") or payload.get("error") or payload.get("message")
                if detail:
                    code = str(detail)
        except Exception:
            pass
        if exc.code in {401, 403}:
            code = "RUNDOWN_BOARD_AUTH_OR_ENTITLEMENT_REJECTED"
        elif exc.code == 429:
            code = "RUNDOWN_BOARD_QUOTA_OR_RATE_LIMIT"
        return BoardResult(False, status=exc.code, code=code)
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return BoardResult(False, code=f"RUNDOWN_BOARD_{type(exc).__name__}")


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        # tolerate one nested data envelope
        data = payload.get("data")
        if isinstance(data, dict):
            return _rows(data, *keys)
    return []


def _catalog(name: str, path: str, *keys: str) -> BoardResult:
    with _lock:
        if name in _catalog_cache:
            return BoardResult(True, _catalog_cache[name], 200)
    result = _api_get(path)
    if not result.ok:
        return result
    values = _rows(result.data, *keys)
    if not values:
        return BoardResult(False, status=result.status, code=f"RUNDOWN_BOARD_{name.upper()}_CATALOG_EMPTY")
    with _lock:
        _catalog_cache[name] = values
    return BoardResult(True, values, result.status)


def sports_catalog() -> BoardResult:
    result = _catalog("sports", "/api/v2/sports", "sports")
    if not result.ok:
        return result
    output: list[dict[str, Any]] = []
    with _lock:
        for row in result.data:
            sport_id = row.get("sport_id") or row.get("id")
            if sport_id is None:
                continue
            key = _sport_key(row)
            title = str(row.get("sport_name") or row.get("name") or row.get("title") or key)
            _sport_key_to_id[key] = str(sport_id)
            _sport_key_to_title[key] = title
            output.append({
                "key": key,
                "title": title,
                "active": row.get("active", row.get("is_active", True)) is not False,
                "_wow_rundown_sport_id": str(sport_id),
                "_wow_market_evidence": _governance_marker(),
            })
    if not output:
        return BoardResult(False, status=result.status, code="RUNDOWN_BOARD_SPORT_MAPPING_EMPTY")
    return BoardResult(True, output, result.status)


def markets_catalog() -> BoardResult:
    return _catalog("markets", "/api/v2/markets", "markets")


def affiliates_catalog() -> BoardResult:
    return _catalog("affiliates", "/api/v2/affiliates", "affiliates")


def _governance_marker() -> dict[str, Any]:
    return {
        "provider": "RUNDOWN_AUTHENTICATED_BOARD",
        "provider_detail": "THERUNDOWN_PRODUCT_V2_BOARD_MIRROR",
        "source_tier": "SPORTSBOOK_FEED_RESEARCH",
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "market_role_evidence_only": True,
        "can_execute": False,
    }


def _market_key(market: dict[str, Any]) -> str:
    market_id = str(market.get("market_id") or market.get("id") or "")
    if market_id in _STANDARD_MARKETS:
        return _STANDARD_MARKETS[market_id]
    raw = market.get("name") or market.get("market_name") or market.get("title") or f"market_{market_id}"
    key = _slug(raw)
    # Prop classification in Nightly Multi-Scout is token based.  Preserve the
    # provider label but make an explicit player-prop prefix when the catalog
    # identifies one without a naturally recognizable token.
    family = _slug(market.get("type") or market.get("category") or market.get("market_type"))
    if "player" in family and not key.startswith("player_"):
        key = f"player_{key}"
    return key or f"rundown_market_{market_id}"


def _market_key_map() -> BoardResult:
    result = markets_catalog()
    if not result.ok:
        return result
    mapping: dict[str, str] = {}
    for market in result.data:
        market_id = market.get("market_id") or market.get("id")
        if market_id is not None:
            mapping[str(market_id)] = _market_key(market)
    return BoardResult(True, mapping, result.status)


def _affiliate_ids() -> BoardResult:
    result = affiliates_catalog()
    if not result.ok:
        return result
    ids = []
    for row in result.data:
        value = row.get("affiliate_id") or row.get("id")
        if value is not None and str(value) != "27":  # retired per provider docs
            ids.append(str(value))
    return BoardResult(True, sorted(set(ids)), result.status)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def _teams(event: dict[str, Any]) -> tuple[str | None, str | None]:
    home = away = None
    teams = event.get("teams") or event.get("teams_normalized") or event.get("participants") or []
    if isinstance(teams, list):
        for row in teams:
            if not isinstance(row, dict):
                continue
            name = row.get("name") or row.get("team_name") or row.get("full_name")
            side = str(row.get("type") or row.get("side") or "").lower()
            if row.get("is_home") is True or side == "home":
                home = home or (str(name) if name else None)
            elif row.get("is_away") is True or side == "away":
                away = away or (str(name) if name else None)
    return home, away


def _participant_name(participant: dict[str, Any], home: str | None, away: str | None) -> str | None:
    name = participant.get("name") or participant.get("full_name") or participant.get("team_name")
    if name:
        return str(name)
    side = str(participant.get("type") or participant.get("side") or "").lower()
    if side == "home":
        return home
    if side == "away":
        return away
    return None


def _translate_event(raw: dict[str, Any], sport_key: str, market_map: dict[str, str]) -> dict[str, Any] | None:
    event_id = raw.get("event_id") or raw.get("id") or raw.get("event_uuid")
    if event_id is None:
        return None
    home, away = _teams(raw)
    books: dict[str, dict[str, Any]] = {}
    for market in raw.get("markets") or []:
        if not isinstance(market, dict):
            continue
        market_id = str(market.get("market_id") or market.get("id") or "")
        market_key = market_map.get(market_id) or _market_key(market)
        for participant in market.get("participants") or []:
            if not isinstance(participant, dict):
                continue
            participant_name = _participant_name(participant, home, away)
            for line in participant.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                selection = line.get("selection") or line.get("name") or line.get("side") or participant_name
                if not selection:
                    continue
                prices = line.get("prices") or {}
                entries = prices.items() if isinstance(prices, dict) else []
                for affiliate_id, container in entries:
                    if isinstance(container, dict):
                        price = _number(container.get("price") or container.get("american") or container.get("american_odds"))
                        title = container.get("affiliate_name") or container.get("sportsbook") or container.get("book_name")
                        updated = container.get("updated_at") or container.get("date_updated")
                        is_main = container.get("is_main_line")
                    else:
                        price = _number(container)
                        title = None
                        updated = None
                        is_main = None
                    if price is None or abs(float(price) - 0.0001) < 1e-9:
                        continue
                    book_key = f"rundown_{_slug(title or affiliate_id)}"
                    book = books.setdefault(book_key, {
                        "key": book_key,
                        "title": str(title or f"affiliate_{affiliate_id}"),
                        "last_update": updated,
                        "markets": {},
                    })
                    bucket = book["markets"].setdefault(market_key, {"key": market_key, "last_update": updated, "outcomes": []})
                    outcome: dict[str, Any] = {"name": str(selection), "price": price}
                    point = _number(line.get("value") or line.get("line") or line.get("point"))
                    if point is not None:
                        outcome["point"] = point
                    description = participant.get("name") or participant.get("full_name")
                    if description and str(description) != str(selection):
                        outcome["description"] = str(description)
                    if is_main is not None:
                        outcome["is_main_line"] = bool(is_main)
                    if not any(
                        existing.get("name") == outcome.get("name")
                        and existing.get("description") == outcome.get("description")
                        and existing.get("point") == outcome.get("point")
                        for existing in bucket["outcomes"]
                    ):
                        bucket["outcomes"].append(outcome)
    return {
        "id": f"rundown-{event_id}",
        "sport_key": sport_key,
        "commence_time": raw.get("event_date") or raw.get("start_time") or raw.get("commence_time"),
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": book["key"],
                "title": book["title"],
                "last_update": book["last_update"],
                "markets": list(book["markets"].values()),
            }
            for book in books.values() if book["markets"]
        ],
        "_wow_market_evidence": _governance_marker(),
    }


def _merge_event(target: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(target)
    by_book = {str(book.get("key")): dict(book) for book in target.get("bookmakers") or [] if isinstance(book, dict)}
    for book in incoming.get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        key = str(book.get("key"))
        if key not in by_book:
            by_book[key] = dict(book)
            continue
        existing = by_book[key]
        market_map = {str(m.get("key")): dict(m) for m in existing.get("markets") or [] if isinstance(m, dict)}
        for market in book.get("markets") or []:
            if not isinstance(market, dict):
                continue
            mk = str(market.get("key"))
            if mk not in market_map:
                market_map[mk] = dict(market)
                continue
            current = market_map[mk]
            outcomes = list(current.get("outcomes") or [])
            for outcome in market.get("outcomes") or []:
                if outcome not in outcomes:
                    outcomes.append(outcome)
            current["outcomes"] = outcomes
            market_map[mk] = current
        existing["markets"] = list(market_map.values())
        by_book[key] = existing
    merged["bookmakers"] = list(by_book.values())
    return merged


def _dates_from_params(params: dict[str, Any] | None) -> list[str]:
    params = params or {}
    start_raw = str(params.get("commenceTimeFrom") or "")
    end_raw = str(params.get("commenceTimeTo") or "")
    try:
        start = datetime.fromisoformat(start_raw.replace("Z", "+00:00")) if start_raw else datetime.now(timezone.utc)
    except ValueError:
        start = datetime.now(timezone.utc)
    try:
        end = datetime.fromisoformat(end_raw.replace("Z", "+00:00")) if end_raw else start + timedelta(hours=36)
    except ValueError:
        end = start + timedelta(hours=36)
    dates: list[str] = []
    cursor = start.date()
    while cursor <= end.date() and len(dates) < MAX_DATES:
        dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return dates or [start.date().isoformat()]


def _sport_id_for_key(sport_key: str) -> BoardResult:
    with _lock:
        if sport_key in _sport_key_to_id:
            return BoardResult(True, _sport_key_to_id[sport_key], 200)
    sports = sports_catalog()
    if not sports.ok:
        return sports
    with _lock:
        sport_id = _sport_key_to_id.get(sport_key)
    if not sport_id:
        return BoardResult(False, status=404, code="RUNDOWN_BOARD_SPORT_NOT_OFFERED")
    return BoardResult(True, sport_id, 200)


def _snapshot(sport_key: str, params: dict[str, Any] | None) -> BoardResult:
    sport_id_result = _sport_id_for_key(sport_key)
    if not sport_id_result.ok:
        return sport_id_result
    market_result = _market_key_map()
    if not market_result.ok:
        return market_result
    affiliate_result = _affiliate_ids()
    if not affiliate_result.ok:
        return affiliate_result
    market_map: dict[str, str] = market_result.data
    market_ids = list(market_map.keys())
    affiliate_ids: list[str] = affiliate_result.data
    dates = _dates_from_params(params)
    cache_key = (sport_key, "|".join(dates), "FULL_BOARD")
    with _lock:
        cached = _event_cache.get(cache_key)
        if cached is not None:
            return BoardResult(True, list(cached["events"]), 200)

    events_by_id: dict[str, dict[str, Any]] = {}
    requests_made = 0
    for slate_date in dates:
        for idx in range(0, len(market_ids), MARKET_CHUNK_SIZE):
            chunk = market_ids[idx:idx + MARKET_CHUNK_SIZE]
            query = {
                "market_ids": ",".join(chunk),
                "affiliate_ids": ",".join(affiliate_ids),
                "main_line": "false",
                "hide_closed": "true",
                "include": "all_periods",
                "offset": "0",
            }
            result = _api_get(f"/api/v2/sports/{sport_id_result.data}/events/{slate_date}", query)
            requests_made += 1
            if not result.ok:
                return BoardResult(False, status=result.status, code=result.code)
            for raw in _rows(result.data, "events"):
                translated = _translate_event(raw, sport_key, market_map)
                if not translated:
                    continue
                event_id = str(translated["id"])
                events_by_id[event_id] = _merge_event(events_by_id[event_id], translated) if event_id in events_by_id else translated

    events = list(events_by_id.values())
    visible_market_keys = sorted({
        str(market.get("key"))
        for event in events
        for book in event.get("bookmakers") or []
        for market in book.get("markets") or []
        if isinstance(market, dict) and market.get("key")
    })
    audit = {
        "provider": "RUNDOWN_AUTHENTICATED_BOARD",
        "status": "COMPLETE" if events else "EMPTY",
        "sports_source": "/api/v2/sports",
        "markets_source": "/api/v2/markets",
        "affiliates_source": "/api/v2/affiliates",
        "sport_id": str(sport_id_result.data),
        "dates": dates,
        "catalog_market_count": len(market_ids),
        "affiliate_count": len(affiliate_ids),
        "event_count": len(events),
        "visible_market_count": len(visible_market_keys),
        "requests_made": requests_made,
        "coverage_complete": True,
        "prediction_authority": False,
        "market_role_evidence_only": True,
        "can_execute": False,
    }
    for event in events:
        event["_wow_rundown_board_audit"] = dict(audit)
        with _lock:
            _event_index[(sport_key, str(event["id"]))] = event
    with _lock:
        _event_cache[cache_key] = {"events": list(events), "audit": audit}
    return BoardResult(True, events, 200)


def _filter_event(event: dict[str, Any], requested_markets: set[str] | None = None) -> dict[str, Any]:
    output = dict(event)
    if not requested_markets:
        return output
    books = []
    for book in event.get("bookmakers") or []:
        if not isinstance(book, dict):
            continue
        selected = [dict(m) for m in book.get("markets") or [] if isinstance(m, dict) and str(m.get("key")) in requested_markets]
        if selected:
            copy = dict(book)
            copy["markets"] = selected
            books.append(copy)
    output["bookmakers"] = books
    return output


def serve(path: str, params: dict[str, Any] | None = None) -> BoardResult | None:
    """Serve a Scout acquisition path from one TheRundown board snapshot.

    ``None`` means this path is outside the board adapter's contract.  A failed
    BoardResult means the path *is* in contract but acquisition/auth/coverage
    failed; callers must not silently label that as a clean empty board.
    """
    if not enabled():
        return None
    if path == "/odds-api/v4/sports":
        return sports_catalog()

    match = _SPORT_EVENTS_RE.match(path)
    if match:
        return _snapshot(match.group(1), params)

    match = _EVENT_MARKETS_RE.match(path)
    if match:
        sport_key, event_id = match.groups()
        with _lock:
            event = _event_index.get((sport_key, event_id))
        if event is None:
            snapshot = _snapshot(sport_key, params)
            if not snapshot.ok:
                return snapshot
            with _lock:
                event = _event_index.get((sport_key, event_id))
        if event is None:
            return BoardResult(False, status=404, code="RUNDOWN_BOARD_EVENT_NOT_IN_SNAPSHOT")
        return BoardResult(True, _filter_event(event), 200)

    match = _EVENT_ODDS_RE.match(path)
    if match:
        sport_key, event_id = match.groups()
        with _lock:
            event = _event_index.get((sport_key, event_id))
        if event is None:
            snapshot = _snapshot(sport_key, params)
            if not snapshot.ok:
                return snapshot
            with _lock:
                event = _event_index.get((sport_key, event_id))
        if event is None:
            return BoardResult(False, status=404, code="RUNDOWN_BOARD_EVENT_NOT_IN_SNAPSHOT")
        requested = {value.strip() for value in str((params or {}).get("markets") or "").split(",") if value.strip()}
        return BoardResult(True, _filter_event(event, requested or None), 200)

    return None


__all__ = ["BoardResult", "enabled", "reset_cache", "serve", "sports_catalog"]
