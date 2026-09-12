from __future__ import annotations

import os
import re
import secrets
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from github_actions_oidc import GitHubOIDCValidationError, verify_github_actions_oidc

app = FastAPI(title="WOW Odds Acquisition Router", version="1.0.0")

PRIMARY_URL = os.environ.get("WOW_PRIMARY_ODDS_PROXY_URL", "https://wow-odds-proxy.onrender.com").rstrip("/")
OPTIC_BASE = "https://api.opticodds.com/api/v3"
FAILOVER_STATUSES = {401, 403, 429, 500, 502, 503, 504}
EVENT_PREFIX = "optic__"

LEAGUE_MAP = {
    "baseball_mlb": "mlb",
    "americanfootball_nfl": "nfl",
    "americanfootball_ncaaf": "ncaaf",
    "basketball_nba": "nba",
    "basketball_wnba": "wnba",
    "basketball_ncaab": "ncaab",
    "icehockey_nhl": "nhl",
}

SPORT_TITLES = {
    "baseball_mlb": "MLB",
    "americanfootball_nfl": "NFL",
    "americanfootball_ncaaf": "NCAAF",
    "basketball_nba": "NBA",
    "basketball_wnba": "WNBA",
    "basketball_ncaab": "NCAAB",
    "icehockey_nhl": "NHL",
}

CORE_TO_OPTIC = {
    "h2h": "moneyline",
    "spreads": "point_spread",
    "totals": "total_points",
}


def _auth(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={"code": "ODDS_ROUTER_AUTH_REQUIRED", "can_execute": False})
    token = authorization[7:]
    configured = os.environ.get("WOW_ODDS_ROUTER_ACTION_KEY")
    if configured and secrets.compare_digest(token, configured):
        return authorization
    try:
        verify_github_actions_oidc(token)
        return authorization
    except GitHubOIDCValidationError as exc:
        raise HTTPException(status_code=401, detail={"code": "ODDS_ROUTER_AUTH_INVALID", "can_execute": False}) from exc


def _optic_key() -> str | None:
    value = os.environ.get("OPTICODDS_API_KEY")
    return value.strip() if value and value.strip() else None


def _sportsbooks() -> list[str]:
    raw = os.environ.get("OPTICODDS_SPORTSBOOKS", "DraftKings,BetMGM,Caesars,Fanatics,ESPN BET")
    return [v.strip() for v in raw.split(",") if v.strip()][:5]


def _should_failover(response: httpx.Response) -> bool:
    return response.status_code in FAILOVER_STATUSES


def _primary_get(path: str, request: Request, authorization: str) -> httpx.Response:
    headers = {"Authorization": authorization, "Accept": "application/json"}
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        return client.get(f"{PRIMARY_URL}{path}", params=list(request.query_params.multi_items()), headers=headers)


def _optic_get(path: str, params: list[tuple[str, str]] | dict[str, Any]) -> httpx.Response:
    key = _optic_key()
    if not key:
        raise HTTPException(status_code=503, detail={"code": "OPTICODDS_API_KEY_UNCONFIGURED", "can_execute": False})
    headers = {"X-Api-Key": key, "Accept": "application/json"}
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        return client.get(f"{OPTIC_BASE}{path}", params=params, headers=headers)


def _passthrough(response: httpx.Response, provider: str) -> JSONResponse:
    try:
        payload = response.json()
    except ValueError:
        payload = {"ok": False, "code": "ODDS_PROVIDER_NON_JSON", "can_execute": False}
    headers = {"x-wow-odds-provider": provider, "x-wow-odds-failover": "false" if provider == "THE_ODDS_API" else "true"}
    return JSONResponse(payload, status_code=response.status_code, headers=headers)


def _team_name(item: Any) -> str | None:
    if isinstance(item, list) and item and isinstance(item[0], dict):
        return item[0].get("name")
    return None


def _fixture_to_event(fixture: dict[str, Any], sport_key: str) -> dict[str, Any]:
    return {
        "id": f"{EVENT_PREFIX}{fixture.get('id')}",
        "commence_time": fixture.get("start_date"),
        "home_team": fixture.get("home_team_display") or _team_name(fixture.get("home_competitors")),
        "away_team": fixture.get("away_team_display") or _team_name(fixture.get("away_competitors")),
        "sport_key": sport_key,
        "source_provider": "OPTICODDS",
        "provider_event_id": fixture.get("id"),
    }


def _optic_market_to_wow(market_id: str, sport_key: str) -> str:
    key = str(market_id or "").lower()
    if key == "moneyline":
        return "h2h"
    if key in {"point_spread", "spread", "run_line", "puck_line"}:
        return "spreads"
    if key in {"total_points", "total_runs", "total_goals", "game_total", "total"}:
        return "totals"
    return re.sub(r"[^a-z0-9_]+", "_", key).strip("_")


def _wow_market_to_optic(market: str, sport_key: str) -> str:
    if market == "h2h":
        return "moneyline"
    if market == "spreads":
        return {"baseball_mlb": "run_line", "icehockey_nhl": "puck_line"}.get(sport_key, "point_spread")
    if market == "totals":
        return {"baseball_mlb": "total_runs", "icehockey_nhl": "total_goals"}.get(sport_key, "total_points")
    return market


def _normalize_optic_odds(payload: Any, sport_key: str, fixture_id: str) -> dict[str, Any]:
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    fixture = next((x for x in rows if isinstance(x, dict) and str(x.get("id")) == fixture_id), rows[0] if rows else {})
    books: dict[str, dict[str, Any]] = {}
    for odd in fixture.get("odds", []) if isinstance(fixture, dict) else []:
        if not isinstance(odd, dict):
            continue
        sportsbook = str(odd.get("sportsbook") or "OPTICODDS")
        slug = re.sub(r"[^a-z0-9]+", "_", sportsbook.lower()).strip("_") or "opticodds"
        book = books.setdefault(slug, {"key": slug, "title": sportsbook, "last_update": None, "markets": {}})
        market_key = _optic_market_to_wow(str(odd.get("market_id") or odd.get("market") or ""), sport_key)
        market = book["markets"].setdefault(market_key, {"key": market_key, "last_update": None, "outcomes": []})
        selection = odd.get("selection") or odd.get("name")
        description = odd.get("name") if odd.get("player_id") else None
        market["outcomes"].append({
            "name": selection,
            "description": description,
            "price": odd.get("price"),
            "point": odd.get("points"),
            "source_provider": "OPTICODDS",
            "provider_market_id": odd.get("market_id"),
            "provider_odd_id": odd.get("id"),
        })
    bookmakers = []
    for book in books.values():
        book["markets"] = list(book["markets"].values())
        bookmakers.append(book)
    return {
        "id": f"{EVENT_PREFIX}{fixture_id}",
        "commence_time": fixture.get("start_date") if isinstance(fixture, dict) else None,
        "home_team": fixture.get("home_team_display") if isinstance(fixture, dict) else None,
        "away_team": fixture.get("away_team_display") if isinstance(fixture, dict) else None,
        "bookmakers": bookmakers,
        "source_provider": "OPTICODDS",
        "provider_event_id": fixture_id,
    }


@app.get("/odds-api/health")
def health():
    return {
        "status": "ok",
        "service": "WOW_ODDS_ACQUISITION_ROUTER",
        "primary_provider": "THE_ODDS_API",
        "backup_provider": "OPTICODDS",
        "backup_configured": bool(_optic_key()),
        "provider_authority": "EVIDENCE_ONLY",
        "can_execute": False,
    }


@app.get("/odds-api/v4/sports")
def sports(request: Request, authorization: str = Depends(_auth)):
    primary = _primary_get("/odds-api/v4/sports", request, authorization)
    if primary.status_code < 400 or not _should_failover(primary) or not _optic_key():
        return _passthrough(primary, "THE_ODDS_API")
    payload = [{"key": k, "title": SPORT_TITLES[k], "active": True, "source_provider": "OPTICODDS"} for k in LEAGUE_MAP]
    return JSONResponse(payload, headers={"x-wow-odds-provider": "OPTICODDS", "x-wow-odds-failover": "true", "x-wow-primary-status": str(primary.status_code)})


@app.get("/odds-api/v4/sports/{sport}/events")
def events(sport: str, request: Request, authorization: str = Depends(_auth)):
    primary = _primary_get(f"/odds-api/v4/sports/{sport}/events", request, authorization)
    if primary.status_code < 400 or not _should_failover(primary) or not _optic_key() or sport not in LEAGUE_MAP:
        return _passthrough(primary, "THE_ODDS_API")
    params: list[tuple[str, str]] = [("league", LEAGUE_MAP[sport])]
    after = request.query_params.get("commenceTimeFrom")
    before = request.query_params.get("commenceTimeTo")
    if after:
        params.append(("start_date_after", after))
    if before:
        params.append(("start_date_before", before))
    optic = _optic_get("/fixtures/active", params)
    if optic.status_code >= 400:
        return _passthrough(primary, "THE_ODDS_API")
    data = optic.json().get("data", [])
    payload = [_fixture_to_event(x, sport) for x in data if isinstance(x, dict) and x.get("id")]
    return JSONResponse(payload, headers={"x-wow-odds-provider": "OPTICODDS", "x-wow-odds-failover": "true", "x-wow-primary-status": str(primary.status_code)})


@app.get("/odds-api/v4/sports/{sport}/events/{event_id}/markets")
def markets(sport: str, event_id: str, request: Request, authorization: str = Depends(_auth)):
    if not event_id.startswith(EVENT_PREFIX):
        primary = _primary_get(f"/odds-api/v4/sports/{sport}/events/{event_id}/markets", request, authorization)
        return _passthrough(primary, "THE_ODDS_API")
    if not _optic_key() or sport not in LEAGUE_MAP:
        raise HTTPException(status_code=503, detail={"code": "OPTICODDS_FAILOVER_UNAVAILABLE", "can_execute": False})
    optic = _optic_get("/markets/active", {"league": LEAGUE_MAP[sport], "markets_only": "true"})
    if optic.status_code >= 400:
        return _passthrough(optic, "OPTICODDS")
    raw = optic.json().get("data", [])
    market_ids = []
    for item in raw:
        if isinstance(item, str):
            market_ids.append(item)
        elif isinstance(item, dict):
            market_ids.append(str(item.get("id") or item.get("name") or ""))
    markets = [{"key": _optic_market_to_wow(mid, sport), "outcomes": []} for mid in market_ids if mid]
    payload = {"id": event_id, "bookmakers": [{"key": "opticodds_inventory", "title": "OpticOdds market inventory", "markets": markets}], "source_provider": "OPTICODDS"}
    return JSONResponse(payload, headers={"x-wow-odds-provider": "OPTICODDS", "x-wow-odds-failover": "true"})


@app.get("/odds-api/v4/sports/{sport}/events/{event_id}/odds")
def odds(sport: str, event_id: str, request: Request, authorization: str = Depends(_auth)):
    if not event_id.startswith(EVENT_PREFIX):
        primary = _primary_get(f"/odds-api/v4/sports/{sport}/events/{event_id}/odds", request, authorization)
        return _passthrough(primary, "THE_ODDS_API")
    if not _optic_key() or sport not in LEAGUE_MAP:
        raise HTTPException(status_code=503, detail={"code": "OPTICODDS_FAILOVER_UNAVAILABLE", "can_execute": False})
    fixture_id = event_id[len(EVENT_PREFIX):]
    requested = [m for m in (request.query_params.get("markets") or "").split(",") if m]
    params: list[tuple[str, str]] = [("fixture_id", fixture_id), ("odds_format", "AMERICAN")]
    for book in _sportsbooks():
        params.append(("sportsbook", book))
    for market in requested:
        params.append(("market", _wow_market_to_optic(market, sport)))
    optic = _optic_get("/fixtures/odds", params)
    if optic.status_code >= 400:
        return _passthrough(optic, "OPTICODDS")
    payload = _normalize_optic_odds(optic.json(), sport, fixture_id)
    return JSONResponse(payload, headers={"x-wow-odds-provider": "OPTICODDS", "x-wow-odds-failover": "true"})
