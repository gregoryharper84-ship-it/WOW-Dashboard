"""Cross-book market evidence acquisition for WOW V17 (research-only).

This module is a sibling of ``scout_secondary_source`` and lives outside every
governed probability lane. It acquires sportsbook price evidence from
subscription market-data providers so Scout can do line shopping, opener/CLV
tracking, and cross-book disagreement flagging.

Governance boundary (CLAUDE.md sections 2, 3 and 7):

- A standalone market-data service is evidence, never a controlling
  probability specialist. Nothing here produces model probability, calibrated
  bounds, approval labels, settlement flags, edge, or stake.
- Every emitted row carries ``prediction_authority=False``,
  ``exact_line_authority=False``, ``research_only=True`` and
  ``can_execute=False``.
- Every failure path is typed and fails closed. A provider that is disabled,
  unconfigured, rate-limited, rejected, or returns an unrecognised schema
  yields a reason code and zero rows. Market prices are never converted into
  a synthetic probability to fill a gap.

Schema discipline (mirrors ``research_source_adapters``): only request paths
that were actually observed or documented are used as defaults, and every one
of them stays env-overridable. Response payloads are *validated structurally*
against the shapes this module understands rather than being parsed against
guessed field names; an unrecognised payload returns
``<PROVIDER>_SCHEMA_UNRECOGNISED`` together with a value-free structural probe
so the shape can be pinned in a follow-up change instead of being improvised.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CAN_EXECUTE = False
SOURCE_CLASS = "SPORTSBOOK_FEED"
SOURCE_TIER = "SPORTSBOOK_FEED_RESEARCH"
TRUST_TIER = "TIER_3_ESTABLISHED_DATA"
MAX_AGE_MINUTES = 15
USER_AGENT = "WOW-V17-Scout-Research/1.0 (market-evidence; contact=repo-owner)"

ENABLED = os.environ.get("WOW_MARKET_EVIDENCE_ENABLED", "false").strip().lower() == "true"
TIMEOUT_SECONDS = float(os.environ.get("WOW_MARKET_EVIDENCE_TIMEOUT_SECONDS", "15"))

ODDS_API_SPORT_KEYS = (
    "americanfootball_nfl",
    "americanfootball_ncaaf",
    "baseball_mlb",
    "basketball_nba",
    "basketball_wnba",
    "basketball_ncaab",
    "icehockey_nhl",
    "mma_mixed_martial_arts",
    "soccer_epl",
)

# Canonical WOW market keys. TheRundown/SharpAPI vocabulary is translated into
# these before anything leaves this module, so a downstream join never sees a
# provider-native market name (the FIX-C/FIX-D failure class).
CANONICAL_MARKET_KEYS = ("h2h", "spreads", "totals")

_PROVIDER_MARKET_CANONICAL: dict[str, str] = {
    "h2h": "h2h",
    "moneyline": "h2h",
    "money_line": "h2h",
    "ml": "h2h",
    "spread": "spreads",
    "spreads": "spreads",
    "point_spread": "spreads",
    "handicap": "spreads",
    "total": "totals",
    "totals": "totals",
    "over_under": "totals",
    "ou": "totals",
}


def canonical_market_key(raw: Any) -> str | None:
    """Translate a provider market label into a canonical WOW market key."""
    token = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PROVIDER_MARKET_CANONICAL.get(token)


@dataclass(frozen=True)
class MarketEvidenceProvider:
    name: str
    base_url_env: str
    default_base_url: str
    key_envs: tuple[str, ...]
    auth_style: str  # "header" or "query"
    auth_name: str
    endpoints: dict[str, str] = field(default_factory=dict)
    endpoint_env_prefix: str = ""
    notes: str = ""


PROVIDERS: dict[str, MarketEvidenceProvider] = {
    "SHARPAPI": MarketEvidenceProvider(
        name="SHARPAPI",
        base_url_env="WOW_SHARPAPI_BASE_URL",
        default_base_url="https://api.sharpapi.io",
        key_envs=("SHARPAPI_API_KEY", "WOW_SHARPAPI_API_KEY"),
        auth_style="header",
        auth_name="X-API-Key",
        endpoints={"odds": "/api/v1/odds"},
        endpoint_env_prefix="WOW_SHARPAPI_",
        notes="Breadth across many books. Request path taken from the provider's own key-issuance example.",
    ),
    "RUNDOWN": MarketEvidenceProvider(
        name="RUNDOWN",
        base_url_env="WOW_RUNDOWN_BASE_URL",
        default_base_url="https://therundown.io",
        key_envs=("RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY", "THERUNDOWN_API_KEY"),
        auth_style="query",
        auth_name="key",
        endpoints={
            "sports": "/api/v1/sports",
            "events": "/api/v2/sports/{sport_id}/events/{date}",
            "openers": "/api/v2/sports/{sport_id}/openers/{date}",
            "delta": "/api/v2/markets/delta",
        },
        endpoint_env_prefix="WOW_RUNDOWN_",
        notes="Openers and market delta make this the opener/CLV source. Sport ids are resolved at runtime, never guessed.",
    ),
}


@dataclass
class MarketEvidenceResult:
    ok: bool
    provider: str
    capability: str
    data: Any = None
    status: int | None = None
    code: str | None = None
    observed_at: str | None = None
    endpoint: str | None = None
    schema_probe: dict[str, Any] | None = None
    prediction_authority: bool = False
    exact_line_authority: bool = False
    research_only: bool = True
    can_execute: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fail(provider: str, capability: str, code: str, **kw: Any) -> MarketEvidenceResult:
    return MarketEvidenceResult(False, provider, capability, code=code, observed_at=_now_iso(), **kw)


def _norm(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def provider_marker(provider: str, detail: str | None = None, *, primary_failure: str | None = None) -> dict[str, Any]:
    """Provenance stamp attached to every emitted market-evidence payload.

    Key names match the marker ``nightly_multiscout_oidc._bookmaker_rows``
    already stamps onto rows, so evidence flows through the existing path
    without a new propagation contract to get wrong.
    """
    return {
        "provider": f"{provider}_MARKET_EVIDENCE",
        "provider_detail": detail,
        "source_class": SOURCE_CLASS,
        "source_tier": SOURCE_TIER,
        "trust_tier": TRUST_TIER,
        "max_age_minutes": MAX_AGE_MINUTES,
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "primary_source_failure": primary_failure,
        "can_execute": CAN_EXECUTE,
    }


def structural_probe(payload: Any, *, depth: int = 3) -> dict[str, Any]:
    """Describe a payload's *shape* with no values, so schemas can be pinned safely.

    Never returns provider values, only key names, container types and lengths.
    Credentials or personal data therefore cannot leak through a probe.
    """
    def describe(node: Any, level: int) -> Any:
        if isinstance(node, dict):
            if level >= depth:
                return {"type": "object", "keys": sorted(str(k) for k in node)[:40]}
            return {
                "type": "object",
                "fields": {str(k): describe(v, level + 1) for k, v in list(node.items())[:40]},
            }
        if isinstance(node, list):
            if level >= depth or not node:
                return {"type": "array", "length": len(node)}
            return {"type": "array", "length": len(node), "item": describe(node[0], level + 1)}
        if node is None:
            return {"type": "null"}
        return {"type": type(node).__name__}

    return {"shape": describe(payload, 0)}


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def _base_url(provider: MarketEvidenceProvider) -> str:
    return os.environ.get(provider.base_url_env, provider.default_base_url).rstrip("/")


def _api_key(provider: MarketEvidenceProvider) -> str | None:
    for env in provider.key_envs:
        value = os.environ.get(env)
        if value and value.strip():
            return value.strip()
    return None


def _endpoint_path(provider: MarketEvidenceProvider, capability: str) -> str | None:
    override = os.environ.get(f"{provider.endpoint_env_prefix}{capability.upper()}_PATH")
    if override and override.strip():
        return override.strip()
    return provider.endpoints.get(capability)


def _redact(url: str, secret: str | None) -> str:
    return url.replace(secret, "***") if secret else url


def fetch(
    provider_name: str,
    capability: str,
    *,
    path_values: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    opener: Any = None,
) -> MarketEvidenceResult:
    """Fetch one provider capability. Never raises; every failure is typed."""
    provider = PROVIDERS.get(str(provider_name).upper())
    if provider is None:
        return _fail(str(provider_name).upper(), capability, "MARKET_EVIDENCE_PROVIDER_UNKNOWN")
    if not ENABLED:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_DISABLED")

    api_key = _api_key(provider)
    if not api_key:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_CREDENTIAL_UNCONFIGURED")

    path = _endpoint_path(provider, capability)
    if not path:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_ENDPOINT_UNCONFIGURED")

    for key, value in (path_values or {}).items():
        path = path.replace("{" + key + "}", str(value))
    if "{" in path or "}" in path:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_PATH_PARAMETER_MISSING")

    query = {k: v for k, v in (params or {}).items() if v is not None}
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if provider.auth_style == "header":
        headers[provider.auth_name] = api_key
    else:
        query[provider.auth_name] = api_key

    url = _base_url(provider) + (path if path.startswith("/") else "/" + path)
    full = url + (f"?{urlencode(query)}" if query else "")
    safe_endpoint = _redact(url, api_key)

    request = Request(full, headers=headers)
    try:
        with (opener or urlopen)(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
            status = getattr(response, "status", None) or getattr(response, "code", None)
    except HTTPError as exc:
        return _fail(provider.name, capability, f"{provider.name}_HTTP_{exc.code}", status=exc.code, endpoint=safe_endpoint)
    except (URLError, TimeoutError, OSError) as exc:
        return _fail(provider.name, capability, f"{provider.name}_{type(exc).__name__}", endpoint=safe_endpoint)

    try:
        payload = json.loads(body)
    except ValueError:
        return _fail(provider.name, capability, f"{provider.name}_INVALID_JSON", status=status, endpoint=safe_endpoint)

    return MarketEvidenceResult(
        True, provider.name, capability, data=payload, status=status,
        code="MARKET_EVIDENCE_FETCH_OK", observed_at=_now_iso(), endpoint=safe_endpoint,
    )


# ---------------------------------------------------------------------------
# Normalisation to the Odds-API-v4 shape the Scout pipeline already consumes
# ---------------------------------------------------------------------------

def _clean_outcome(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    price = _number(raw.get("price"))
    if not name or price is None:
        return None
    outcome: dict[str, Any] = {"name": str(name), "price": price}
    point = _number(raw.get("point"))
    if point is not None:
        outcome["point"] = point
    if raw.get("description"):
        outcome["description"] = str(raw["description"])
    return outcome


def coerce_odds_api_v4_event(raw: Any) -> dict[str, Any] | None:
    """Strictly validate one already-Odds-API-v4-shaped event.

    This is a validator, not a translator: anything that does not already
    present ``bookmakers[].markets[].outcomes[]`` with usable prices and a
    recognised market key is rejected rather than coaxed into shape.
    """
    if not isinstance(raw, dict):
        return None
    books_raw = raw.get("bookmakers")
    if not isinstance(books_raw, list) or not books_raw:
        return None

    books: list[dict[str, Any]] = []
    for book in books_raw:
        if not isinstance(book, dict):
            continue
        book_key = book.get("key") or book.get("title")
        if not book_key:
            continue
        markets: list[dict[str, Any]] = []
        for market in book.get("markets") or []:
            if not isinstance(market, dict):
                continue
            market_key = canonical_market_key(market.get("key"))
            if market_key is None:
                continue
            outcomes = [o for o in (_clean_outcome(x) for x in market.get("outcomes") or []) if o]
            if not outcomes:
                continue
            markets.append({
                "key": market_key,
                "last_update": market.get("last_update") or book.get("last_update"),
                "outcomes": outcomes,
            })
        if markets:
            books.append({
                "key": str(book_key),
                "title": str(book.get("title") or book_key),
                "last_update": book.get("last_update"),
                "markets": markets,
            })

    if not books:
        return None
    event_id = raw.get("id") or raw.get("event_id")
    if not event_id:
        return None
    return {
        "id": str(event_id),
        "sport_key": raw.get("sport_key"),
        "commence_time": raw.get("commence_time") or raw.get("event_date"),
        "home_team": raw.get("home_team"),
        "away_team": raw.get("away_team"),
        "bookmakers": books,
    }


def _rundown_teams(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    home = away = None
    for team in raw.get("teams_normalized") or raw.get("teams") or []:
        if not isinstance(team, dict):
            continue
        label = team.get("name") or team.get("full_name") or team.get("abbreviation")
        if not label:
            continue
        if team.get("is_home"):
            home = str(label)
        elif team.get("is_away"):
            away = str(label)
    return home, away


def _rundown_markets(line: dict[str, Any], home: str, away: str) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []

    moneyline = line.get("moneyline") if isinstance(line.get("moneyline"), dict) else {}
    home_ml = _number(moneyline.get("moneyline_home"))
    away_ml = _number(moneyline.get("moneyline_away"))
    if home_ml is not None and away_ml is not None:
        outcomes = [{"name": home, "price": home_ml}, {"name": away, "price": away_ml}]
        draw = _number(moneyline.get("moneyline_draw"))
        if draw is not None:
            outcomes.append({"name": "Draw", "price": draw})
        markets.append({"key": "h2h", "last_update": moneyline.get("date_updated"), "outcomes": outcomes})

    spread = line.get("spread") if isinstance(line.get("spread"), dict) else {}
    home_point = _number(spread.get("point_spread_home"))
    away_point = _number(spread.get("point_spread_away"))
    if home_point is not None and away_point is not None:
        markets.append({
            "key": "spreads",
            "last_update": spread.get("date_updated"),
            "outcomes": [
                {"name": home, "price": _number(spread.get("point_spread_home_money")) or -110, "point": home_point},
                {"name": away, "price": _number(spread.get("point_spread_away_money")) or -110, "point": away_point},
            ],
        })

    total = line.get("total") if isinstance(line.get("total"), dict) else {}
    over_point = _number(total.get("total_over"))
    under_point = _number(total.get("total_under"))
    if over_point is not None and under_point is not None:
        markets.append({
            "key": "totals",
            "last_update": total.get("date_updated"),
            "outcomes": [
                {"name": "Over", "price": _number(total.get("total_over_money")) or -110, "point": over_point},
                {"name": "Under", "price": _number(total.get("total_under_money")) or -110, "point": under_point},
            ],
        })

    return markets


def rundown_event_to_odds_api_v4(raw: Any, *, sport_key: str | None = None) -> dict[str, Any] | None:
    """Translate one TheRundown ``events[]`` record carrying an affiliate ``lines`` map."""
    if not isinstance(raw, dict):
        return None
    event_id = raw.get("event_id") or raw.get("id")
    lines = raw.get("lines")
    if not event_id or not isinstance(lines, dict) or not lines:
        return None
    home, away = _rundown_teams(raw)
    if not home or not away:
        return None

    books: list[dict[str, Any]] = []
    for affiliate_id, line in lines.items():
        if not isinstance(line, dict):
            continue
        affiliate = line.get("affiliate") if isinstance(line.get("affiliate"), dict) else {}
        title = str(affiliate.get("affiliate_name") or f"affiliate_{affiliate_id}")
        markets = _rundown_markets(line, home, away)
        if not markets:
            continue
        books.append({
            "key": f"rundown_{_norm(title) or affiliate_id}",
            "title": title,
            "last_update": line.get("date_updated") or raw.get("event_date"),
            "markets": markets,
        })

    if not books:
        return None
    return {
        "id": f"rundown-{event_id}",
        "sport_key": sport_key,
        "commence_time": raw.get("event_date"),
        "home_team": home,
        "away_team": away,
        "bookmakers": books,
    }


def _candidate_events(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "data", "odds", "results", "games"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if payload.get("bookmakers") or payload.get("lines"):
            return [payload]
    return []


def normalize_market_payload(
    payload: Any,
    *,
    provider: str,
    capability: str,
    sport_key: str | None = None,
    primary_failure: str | None = None,
) -> MarketEvidenceResult:
    """Convert a provider payload into research-only Odds-API-v4 events.

    Translation is attempted only against shapes this module actually
    understands. A payload matching none of them returns
    ``<PROVIDER>_SCHEMA_UNRECOGNISED`` plus a value-free structural probe, and
    yields zero rows; nothing is inferred or filled in.
    """
    provider = str(provider).upper()
    candidates = _candidate_events(payload)
    if not candidates:
        return _fail(
            provider, capability, f"{provider}_SCHEMA_UNRECOGNISED",
            schema_probe=structural_probe(payload),
        )

    marker = provider_marker(provider, capability, primary_failure=primary_failure)
    events: list[dict[str, Any]] = []
    for raw in candidates:
        event = rundown_event_to_odds_api_v4(raw, sport_key=sport_key) or coerce_odds_api_v4_event(raw)
        if event is None:
            continue
        if sport_key and not event.get("sport_key"):
            event["sport_key"] = sport_key
        event["_wow_secondary_source"] = dict(marker)
        event["_wow_market_evidence"] = dict(marker)
        events.append(event)

    if not events:
        return _fail(
            provider, capability, f"{provider}_SCHEMA_UNRECOGNISED",
            schema_probe=structural_probe(payload),
        )

    return MarketEvidenceResult(
        True, provider, capability, data=events, status=200,
        code="MARKET_EVIDENCE_NORMALISED", observed_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Sport identity — resolved from the provider, never guessed
# ---------------------------------------------------------------------------

_RUNDOWN_SPORT_INDEX: dict[str, str] = {}

_RUNDOWN_SPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "americanfootball_nfl": ("nfl",),
    "americanfootball_ncaaf": ("ncaafootball", "ncaaf", "collegefootball"),
    "baseball_mlb": ("mlb",),
    "basketball_nba": ("nba",),
    "basketball_wnba": ("wnba",),
    "basketball_ncaab": ("ncaabasketball", "ncaamensbasketball", "ncaab"),
    "icehockey_nhl": ("nhl",),
    "mma_mixed_martial_arts": ("ufcmma", "mma", "ufc"),
    "soccer_epl": ("premierleague", "epl"),
}


def rundown_sport_id(sport_key: str, *, opener: Any = None) -> MarketEvidenceResult:
    """Resolve TheRundown's numeric sport id for a WOW sport key.

    An explicit ``WOW_RUNDOWN_SPORT_ID_<SPORT_KEY>`` pin wins. Otherwise the
    id is read from the provider's own ``/sports`` index. No numeric id is
    hard-coded, so a provider-side renumbering cannot silently mis-route a
    request to the wrong sport.
    """
    pinned = os.environ.get("WOW_RUNDOWN_SPORT_ID_" + str(sport_key).upper())
    if pinned and pinned.strip():
        return MarketEvidenceResult(True, "RUNDOWN", "sports", data=pinned.strip(), code="MARKET_EVIDENCE_SPORT_ID_PINNED", observed_at=_now_iso())

    if not _RUNDOWN_SPORT_INDEX:
        listing = fetch("RUNDOWN", "sports", opener=opener)
        if not listing.ok:
            return listing
        rows = listing.data.get("sports") if isinstance(listing.data, dict) else listing.data
        if not isinstance(rows, list) or not rows:
            return _fail("RUNDOWN", "sports", "RUNDOWN_SCHEMA_UNRECOGNISED", schema_probe=structural_probe(listing.data))
        for row in rows:
            if not isinstance(row, dict):
                continue
            sport_id = row.get("sport_id") or row.get("id")
            name = row.get("sport_name") or row.get("name")
            if sport_id is None or not name:
                continue
            _RUNDOWN_SPORT_INDEX[_norm(name)] = str(sport_id)
        if not _RUNDOWN_SPORT_INDEX:
            return _fail("RUNDOWN", "sports", "RUNDOWN_SCHEMA_UNRECOGNISED", schema_probe=structural_probe(listing.data))

    for alias in _RUNDOWN_SPORT_ALIASES.get(str(sport_key), ()):
        if alias in _RUNDOWN_SPORT_INDEX:
            return MarketEvidenceResult(True, "RUNDOWN", "sports", data=_RUNDOWN_SPORT_INDEX[alias], code="MARKET_EVIDENCE_SPORT_ID_RESOLVED", observed_at=_now_iso())
    return _fail("RUNDOWN", "sports", "MARKET_EVIDENCE_UNSUPPORTED_SPORT")


def reset_sport_index() -> None:
    """Drop the in-process sport index (used by tests and long-running scans)."""
    _RUNDOWN_SPORT_INDEX.clear()


# ---------------------------------------------------------------------------
# Capability entrypoints
# ---------------------------------------------------------------------------

def rundown_market_evidence(
    sport_key: str,
    date: str,
    *,
    capability: str = "events",
    opener: Any = None,
    primary_failure: str | None = None,
) -> MarketEvidenceResult:
    """Daily TheRundown evidence for one sport/date (``events`` or ``openers``)."""
    if capability not in {"events", "openers"}:
        return _fail("RUNDOWN", capability, "MARKET_EVIDENCE_CAPABILITY_UNSUPPORTED")
    resolved = rundown_sport_id(sport_key, opener=opener)
    if not resolved.ok:
        return resolved
    fetched = fetch(
        "RUNDOWN", capability,
        path_values={"sport_id": resolved.data, "date": date},
        params={"offset": "0"},
        opener=opener,
    )
    if not fetched.ok:
        return fetched
    return normalize_market_payload(
        fetched.data, provider="RUNDOWN", capability=capability,
        sport_key=sport_key, primary_failure=primary_failure,
    )


def sharpapi_market_evidence(
    sport_key: str,
    *,
    params: dict[str, Any] | None = None,
    opener: Any = None,
    primary_failure: str | None = None,
) -> MarketEvidenceResult:
    """Cross-book SharpAPI odds evidence for one sport."""
    request_params = {"sport": sport_key}
    request_params.update(params or {})
    fetched = fetch("SHARPAPI", "odds", params=request_params, opener=opener)
    if not fetched.ok:
        return fetched
    return normalize_market_payload(
        fetched.data, provider="SHARPAPI", capability="odds",
        sport_key=sport_key, primary_failure=primary_failure,
    )


def provider_health() -> dict[str, Any]:
    return {
        "schema_version": "wow.v17.market_evidence_sources.v1",
        "enabled": ENABLED,
        "source_class": SOURCE_CLASS,
        "trust_tier": TRUST_TIER,
        "max_age_minutes": MAX_AGE_MINUTES,
        "providers": [
            {
                "provider": provider.name,
                "credential_configured": bool(_api_key(provider)),
                "capabilities": sorted(provider.endpoints),
                "notes": provider.notes,
            }
            for provider in PROVIDERS.values()
        ],
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "CANONICAL_MARKET_KEYS",
    "MarketEvidenceProvider",
    "MarketEvidenceResult",
    "PROVIDERS",
    "canonical_market_key",
    "coerce_odds_api_v4_event",
    "fetch",
    "normalize_market_payload",
    "provider_health",
    "provider_marker",
    "reset_sport_index",
    "rundown_event_to_odds_api_v4",
    "rundown_market_evidence",
    "rundown_sport_id",
    "sharpapi_market_evidence",
    "structural_probe",
]
