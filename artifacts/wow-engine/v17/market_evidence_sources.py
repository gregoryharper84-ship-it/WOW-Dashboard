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
- Market-feed failure is an *evidence* outcome, not a model outcome. It
  terminates as ``MARKET_DATA_UNOBTAINABLE`` (or a more specific provider
  code). It must never, on its own, downgrade an otherwise valid fitted
  sporting probability: ``MODEL_UNAVAILABLE`` stays reserved for an absent
  fitted probability capability, which nothing in this module can cause.

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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from v17.rundown_rate_limit import classify_rate_limit

CAN_EXECUTE = False

# Terminal evidence-side blocker. Deliberately distinct from MODEL_UNAVAILABLE:
# losing a market feed removes evidence, it does not remove a fitted model.
MARKET_DATA_UNOBTAINABLE = "MARKET_DATA_UNOBTAINABLE"

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

# Provider-normalized player/stat markets are discovery identities, not probability
# authority. Preserve an explicit provider key only when it is structurally a
# player/scalar prop; never guess a stat from free text.
_PROP_MARKET_PREFIXES = (
    "player_", "pitcher_", "batter_", "passing_", "rushing_", "receiving_",
    "goalie_", "shots_", "saves_", "aces_", "double_faults_",
)

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
    canonical = _PROVIDER_MARKET_CANONICAL.get(token)
    if canonical:
        return canonical
    if token and any(token.startswith(prefix) for prefix in _PROP_MARKET_PREFIXES):
        return token
    return None


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
        key_envs=("THERUNDOWN_API_KEY", "RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY"),
        auth_style="header",
        auth_name="X-TheRundown-Key",
        endpoints={
            "sports": "/api/v2/sports",
            "events": "/api/v2/sports/{sport_id}/events/{date}",
            "openers": "/api/v2/sports/{sport_id}/openers/{date}",
            "delta": "/api/v2/markets/delta",
        },
        endpoint_env_prefix="WOW_RUNDOWN_",
        notes="Product V2 header-auth market evidence. Openers and market delta make this the opener/CLV source; sport ids are resolved at runtime, never guessed.",
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
    # Populated only for HTTP 429. Preserves the provider's own rate-limit
    # headers so burst throttling stays distinguishable from quota exhaustion.
    rate_limit: dict[str, Any] | None = None
    # Value-free per-request metadata (endpoint family, cache origin, retries,
    # payload classification) so a provider failure is debuggable from the
    # result alone rather than from a bare reason code.
    request_audit: dict[str, Any] | None = None
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


def _api_keys(provider: MarketEvidenceProvider) -> tuple[str, ...]:
    """Return configured credentials in declared priority order, de-duplicated.

    Values are intentionally never logged or returned through diagnostics. The
    multi-key form exists so TheRundown can survive a credential-alias migration
    where one configured alias is stale while another is still valid.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for env in provider.key_envs:
        value = os.environ.get(env)
        token = value.strip() if value and value.strip() else ""
        if token and token not in seen:
            seen.add(token)
            keys.append(token)
    return tuple(keys)


def _api_key(provider: MarketEvidenceProvider) -> str | None:
    keys = _api_keys(provider)
    return keys[0] if keys else None


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
    """Fetch one provider capability. Never raises; every failure is typed.

    TheRundown Product V2 may have more than one credential alias configured
    during migrations. For that provider only, a 401/403 may advance to the next
    distinct configured alias. No other status or transport failure is retried,
    so rate limits, timeouts, malformed payloads, and provider outages remain
    fail-closed and retain their original typed semantics.
    """
    provider = PROVIDERS.get(str(provider_name).upper())
    if provider is None:
        return _fail(str(provider_name).upper(), capability, "MARKET_EVIDENCE_PROVIDER_UNKNOWN")
    if not ENABLED:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_DISABLED")

    api_keys = _api_keys(provider)
    if not api_keys:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_CREDENTIAL_UNCONFIGURED")

    path = _endpoint_path(provider, capability)
    if not path:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_ENDPOINT_UNCONFIGURED")

    for key, value in (path_values or {}).items():
        path = path.replace("{" + key + "}", str(value))
    if "{" in path or "}" in path:
        return _fail(provider.name, capability, "MARKET_EVIDENCE_PATH_PARAMETER_MISSING")

    base_query = {k: v for k, v in (params or {}).items() if v is not None}
    url = _base_url(provider) + (path if path.startswith("/") else "/" + path)

    allow_auth_failover = provider.name == "RUNDOWN" and provider.auth_style == "header"
    attempt_keys = api_keys if allow_auth_failover else api_keys[:1]

    for attempt_index, api_key in enumerate(attempt_keys):
        query = dict(base_query)
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if provider.auth_style == "header":
            headers[provider.auth_name] = api_key
        else:
            query[provider.auth_name] = api_key

        full = url + (f"?{urlencode(query)}" if query else "")
        safe_endpoint = _redact(url, api_key)
        request = Request(full, headers=headers)

        try:
            with (opener or urlopen)(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
                status = getattr(response, "status", None) or getattr(response, "code", None)
        except HTTPError as exc:
            has_next_alias = attempt_index + 1 < len(attempt_keys)
            if allow_auth_failover and exc.code in (401, 403) and has_next_alias:
                continue

            # A 429 carries the only evidence that separates a short burst throttle
            # from an exhausted account allowance. Preserve provider headers while
            # keeping the established failure code.
            rate_limit = None
            if exc.code == 429:
                try:
                    error_body = exc.read().decode("utf-8", "replace")[:2048]
                except Exception:  # noqa: BLE001 - diagnostics must never raise
                    error_body = ""
                rate_limit = classify_rate_limit(getattr(exc, "headers", None), error_body).as_dict()
            return _fail(
                provider.name, capability, f"{provider.name}_HTTP_{exc.code}",
                status=exc.code, endpoint=safe_endpoint, rate_limit=rate_limit,
            )
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

    # Defensive only: every configured attempt returns from the loop.
    return _fail(provider.name, capability, f"{provider.name}_AUTH_ALIASES_EXHAUSTED")


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
            if market_key not in CANONICAL_MARKET_KEYS:
                outcomes = [o for o in outcomes if o.get("description") and "point" in o]
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


# ---------------------------------------------------------------------------
# TheRundown V2 native adapter
#
# V2 is not a reshuffle of V1: it nests
#     event -> markets[] -> participants[] -> lines[] -> prices{affiliate_id}
# where V1 carried event.lines{affiliate_id}.{moneyline,spread,total}. The V2
# endpoints this module calls (/api/v2/.../events/{date}, /openers/{date})
# return the V2 model, so they get their own translator rather than being
# squeezed through the V1 one.
# ---------------------------------------------------------------------------

def _rundown_market_id_map() -> dict[str, str]:
    """Optional provider market-id -> canonical key map.

    TheRundown addresses markets by numeric id. Those ids are provider-owned
    and are not guessed here: markets are matched by name, and an operator can
    pin ids explicitly via ``WOW_RUNDOWN_MARKET_ID_MAP_JSON``
    (e.g. ``{"1": "spreads", "3": "h2h"}``).
    """
    raw = os.environ.get("WOW_RUNDOWN_MARKET_ID_MAP_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in parsed.items():
        canonical = canonical_market_key(value)
        if canonical:
            out[str(key)] = canonical
    return out


def rundown_winner_market_ids() -> tuple[str, ...]:
    """Provider market ids for the outright-winner market, or ``()`` if unknown.

    Narrowing a slate request to the winner market is the cheapest way to keep a
    cross-sport scan inside the provider's data-point allowance, but it is only
    safe with *real* ids. Those come from the same operator-pinned canonical map
    the V2 adapter already uses — never from a guessed or historical constant.
    An empty result means "do not narrow", which is the correct fail-open for a
    research-only evidence lane: a wider payload is a cost, not a defect.
    """
    explicit = os.environ.get("WOW_RUNDOWN_WINNER_MARKET_IDS", "").strip()
    if explicit:
        return tuple(token.strip() for token in explicit.split(",") if token.strip())
    mapping = _rundown_market_id_map()
    return tuple(sorted(key for key, canonical in mapping.items() if canonical == "h2h"))


def _rundown_v2_participants(event: dict[str, Any], market: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index participants by every id they can be referenced under.

    Participants may be declared on the market or on the event; both are
    indexed so a line can resolve its side wherever the provider put it.
    """
    index: dict[str, dict[str, Any]] = {}
    for source in (event.get("participants"), market.get("participants")):
        for participant in source or []:
            if not isinstance(participant, dict):
                continue
            nested = participant.get("participant") if isinstance(participant.get("participant"), dict) else {}
            name = (
                participant.get("name")
                or participant.get("full_name")
                or nested.get("name")
                or nested.get("full_name")
                or participant.get("abbreviation")
                or nested.get("abbreviation")
            )
            if not name:
                continue
            record = {
                "name": str(name),
                "is_home": bool(participant.get("is_home") or nested.get("is_home") or str(participant.get("type") or "").lower() == "home"),
                "is_away": bool(participant.get("is_away") or nested.get("is_away") or str(participant.get("type") or "").lower() == "away"),
            }
            for id_key in ("id", "participant_id", "team_id"):
                for holder in (participant, nested):
                    value = holder.get(id_key)
                    if value is not None:
                        index.setdefault(str(value), record)
            index.setdefault(_norm(name), record)
    return index


def _rundown_v2_outcome_name(line: dict[str, Any], participants: dict[str, dict[str, Any]]) -> str | None:
    """Resolve a V2 line to the outcome label the Odds-API-v4 shape expects."""
    for key in ("participant_id", "team_id", "participant"):
        value = line.get(key)
        if isinstance(value, dict):
            value = value.get("id") or value.get("participant_id") or value.get("name")
        if value is not None and str(value) in participants:
            return participants[str(value)]["name"]
    for key in ("selection", "side", "name", "outcome", "label"):
        token = str(line.get(key) or "").strip()
        if not token:
            continue
        lowered = token.lower()
        if lowered in {"over", "under", "draw", "tie"}:
            return lowered.replace("tie", "draw").capitalize()
        if lowered in {"home", "away"}:
            for record in participants.values():
                if record["is_home" if lowered == "home" else "is_away"]:
                    return record["name"]
        if _norm(token) in participants:
            return participants[_norm(token)]["name"]
        return token
    return None


def _rundown_v2_prices(line: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    """Flatten a V2 line's ``prices{affiliate_id}`` map into per-book entries.

    Returns ``(affiliate_id, price_container, price_value)`` triples. A line
    that carries a single inline price instead of a price map is returned as
    one entry so both representations converge here.
    """
    entries: list[tuple[str, Any, Any]] = []
    prices = line.get("prices")
    if isinstance(prices, dict):
        for affiliate_id, container in prices.items():
            entries.append((str(affiliate_id), container, container))
    elif isinstance(prices, list):
        for container in prices:
            if not isinstance(container, dict):
                continue
            affiliate_id = container.get("affiliate_id") or container.get("affiliate") or container.get("book_id")
            if affiliate_id is None:
                continue
            entries.append((str(affiliate_id), container, container))
    elif line.get("affiliate_id") is not None:
        entries.append((str(line["affiliate_id"]), line, line))
    return entries


def _rundown_v2_price_value(container: Any) -> int | float | None:
    if isinstance(container, (int, float, str)):
        return _number(container)
    if not isinstance(container, dict):
        return None
    for key in ("american", "american_odds", "price", "odds", "moneyline", "decimal"):
        value = _number(container.get(key))
        if value is not None:
            return value
    return None


def _rundown_v2_point(line: dict[str, Any], container: Any) -> int | float | None:
    holders = [container, line] if isinstance(container, dict) else [line]
    for holder in holders:
        for key in ("spread", "total", "point", "line_value", "value", "handicap"):
            value = _number(holder.get(key))
            if value is not None:
                return value
    return None


def rundown_v2_event_to_odds_api_v4(raw: Any, *, sport_key: str | None = None) -> dict[str, Any] | None:
    """Translate one TheRundown **V2** event into the Odds-API-v4 shape.

    Walks ``markets[] -> participants[] -> lines[] -> prices{affiliate_id}`` and
    regroups it by book, because Odds-API-v4 is book-major while V2 is
    market-major. Returns ``None`` when the payload is not V2-shaped or carries
    no usable price, so the caller can fall through or fail closed.
    """
    if not isinstance(raw, dict):
        return None
    markets = raw.get("markets")
    if not isinstance(markets, list) or not markets:
        return None
    event_id = raw.get("event_id") or raw.get("id")
    if not event_id:
        return None

    id_map = _rundown_market_id_map()
    # book key -> {title, markets: {canonical_key: {outcomes, last_update}}}
    books: dict[str, dict[str, Any]] = {}
    home = away = None

    for market in markets:
        if not isinstance(market, dict):
            continue
        participants = _rundown_v2_participants(raw, market)
        for record in participants.values():
            if record["is_home"] and not home:
                home = record["name"]
            elif record["is_away"] and not away:
                away = record["name"]

        market_key = canonical_market_key(
            market.get("name") or market.get("market_name") or market.get("type")
        ) or id_map.get(str(market.get("market_id") or market.get("id") or ""))
        if market_key is None:
            continue

        lines = market.get("lines")
        if not isinstance(lines, list):
            lines = [market] if market.get("prices") else []

        for line in lines:
            if not isinstance(line, dict):
                continue
            outcome_name = _rundown_v2_outcome_name(line, participants)
            if not outcome_name:
                continue
            for affiliate_id, container, _raw_price in _rundown_v2_prices(line):
                price = _rundown_v2_price_value(container)
                if price is None:
                    continue
                affiliate = line.get("affiliate") if isinstance(line.get("affiliate"), dict) else {}
                title = str(
                    (container.get("affiliate_name") if isinstance(container, dict) else None)
                    or affiliate.get("affiliate_name")
                    or affiliate.get("name")
                    or f"affiliate_{affiliate_id}"
                )
                book = books.setdefault(f"rundown_{_norm(title) or affiliate_id}", {
                    "title": title,
                    "last_update": None,
                    "markets": {},
                })
                updated = (
                    (container.get("date_updated") if isinstance(container, dict) else None)
                    or line.get("date_updated")
                    or market.get("date_updated")
                    or raw.get("event_date")
                )
                if updated and not book["last_update"]:
                    book["last_update"] = updated
                bucket = book["markets"].setdefault(market_key, {"last_update": updated, "outcomes": []})
                outcome: dict[str, Any] = {"name": outcome_name, "price": price}
                point = _rundown_v2_point(line, container)
                if point is not None:
                    outcome["point"] = point
                if not any(o["name"] == outcome["name"] and o.get("point") == outcome.get("point") for o in bucket["outcomes"]):
                    bucket["outcomes"].append(outcome)

    if not books:
        return None
    if not home or not away:
        home_fallback, away_fallback = _rundown_teams(raw)
        home = home or home_fallback
        away = away or away_fallback

    return {
        "id": f"rundown-{event_id}",
        "sport_key": sport_key,
        "commence_time": raw.get("event_date") or raw.get("date_event") or raw.get("start_time"),
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": key,
                "title": book["title"],
                "last_update": book["last_update"],
                "markets": [
                    {"key": market_key, "last_update": bucket["last_update"], "outcomes": bucket["outcomes"]}
                    for market_key, bucket in book["markets"].items()
                ],
            }
            for key, book in books.items()
        ],
    }


# ---------------------------------------------------------------------------
# SharpAPI native adapter
#
# SharpAPI is row-major: one record per sportsbook/event/market/selection, with
# a line-shopping variant that nests competing books under ``all_books``.
# Rows are regrouped event -> sportsbook -> canonical market -> outcomes.
# ---------------------------------------------------------------------------

SHARPAPI_SPORT_LEAGUES: dict[str, tuple[str, ...]] = {
    "americanfootball_nfl": ("nfl",),
    "americanfootball_ncaaf": ("ncaaf", "college-football", "cfb"),
    "baseball_mlb": ("mlb",),
    "basketball_nba": ("nba",),
    "basketball_wnba": ("wnba",),
    "basketball_ncaab": ("ncaab", "college-basketball"),
    "icehockey_nhl": ("nhl",),
    "mma_mixed_martial_arts": ("ufc", "mma"),
    "soccer_epl": ("epl", "premier-league"),
}


def sharpapi_league(sport_key: str) -> str | None:
    """Map a WOW sport key to SharpAPI's league identifier.

    An explicit ``WOW_SHARPAPI_LEAGUE_<SPORT_KEY>`` pin wins, so a provider-side
    rename is a config change rather than a code change. An unmapped sport
    returns ``None`` and fails closed rather than querying a default league.
    """
    pinned = os.environ.get("WOW_SHARPAPI_LEAGUE_" + str(sport_key).upper())
    if pinned and pinned.strip():
        return pinned.strip()
    leagues = SHARPAPI_SPORT_LEAGUES.get(str(sport_key))
    return leagues[0] if leagues else None


def _sharpapi_first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _sharpapi_event_identity(row: dict[str, Any]) -> tuple[str, str | None, str | None, Any] | None:
    event = _sharpapi_first(row, ("event", "game", "match", "fixture"))
    if isinstance(event, dict):
        event_id = _sharpapi_first(event, ("id", "event_id", "game_id", "key"))
        home = _sharpapi_first(event, ("home_team", "home", "home_team_name"))
        away = _sharpapi_first(event, ("away_team", "away", "away_team_name"))
        start = _sharpapi_first(event, ("commence_time", "start_time", "start_date", "event_date", "scheduled"))
    else:
        event_id = _sharpapi_first(row, ("event_id", "game_id", "match_id"))
        home = _sharpapi_first(row, ("home_team", "home", "home_team_name"))
        away = _sharpapi_first(row, ("away_team", "away", "away_team_name"))
        start = _sharpapi_first(row, ("commence_time", "start_time", "start_date", "event_date", "scheduled"))
        if event_id is None and isinstance(event, str):
            event_id = event
    if event_id is None:
        if not home or not away:
            return None
        event_id = f"{_norm(away)}-at-{_norm(home)}-{_date_token(start)}"
    return str(event_id), (str(home) if home else None), (str(away) if away else None), start


def _date_token(value: Any) -> str:
    text = str(value or "")
    return text[:10].replace("-", "") if text else "nodate"


def _sharpapi_book_rows(row: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Expand one SharpAPI row into (sportsbook, price-bearing row) pairs.

    The line-shopping endpoint nests competing books under ``all_books``; each
    becomes its own entry so cross-book comparison survives translation.
    """
    entries: list[tuple[str, dict[str, Any]]] = []
    book = _sharpapi_first(row, ("sportsbook", "book", "bookmaker", "sportsbook_name"))
    if isinstance(book, dict):
        book = _sharpapi_first(book, ("name", "key", "title", "id"))
    if book:
        entries.append((str(book), row))

    all_books = row.get("all_books")
    if isinstance(all_books, dict):
        all_books = [
            {**(value if isinstance(value, dict) else {"odds": value}), "sportsbook": key}
            for key, value in all_books.items()
        ]
    if isinstance(all_books, list):
        for nested in all_books:
            if not isinstance(nested, dict):
                continue
            nested_book = _sharpapi_first(nested, ("sportsbook", "book", "bookmaker", "name", "key"))
            if isinstance(nested_book, dict):
                nested_book = _sharpapi_first(nested_book, ("name", "key", "title", "id"))
            if not nested_book:
                continue
            entries.append((str(nested_book), {**row, **nested}))
    return entries


def sharpapi_rows_to_odds_api_v4(rows: Any, *, sport_key: str | None = None) -> list[dict[str, Any]]:
    """Regroup SharpAPI's row-major odds into Odds-API-v4 events.

    ``rows`` is a flat sequence of sportsbook/event/market/selection records.
    They are grouped event -> sportsbook -> canonical market -> outcomes. Rows
    missing an event identity, a recognised market, a selection or a price are
    dropped rather than defaulted. Explicit prop markets additionally require
    an exact line and participant identity before leaving this adapter.
    """
    if not isinstance(rows, list):
        return []

    events: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _sharpapi_event_identity(row)
        if identity is None:
            continue
        event_id, home, away, start = identity

        market_key = canonical_market_key(_sharpapi_first(row, ("market", "market_type", "market_key", "bet_type")))
        if market_key is None:
            continue
        is_prop = market_key not in CANONICAL_MARKET_KEYS
        selection = _sharpapi_first(row, ("selection", "outcome", "side", "name", "team", "runner"))
        if isinstance(selection, dict):
            selection = _sharpapi_first(selection, ("name", "team", "label"))
        if not selection:
            continue

        record = events.setdefault(event_id, {
            "id": f"sharpapi-{event_id}",
            "sport_key": sport_key,
            "commence_time": start,
            "home_team": home,
            "away_team": away,
            "books": {},
        })
        record["home_team"] = record["home_team"] or home
        record["away_team"] = record["away_team"] or away
        record["commence_time"] = record["commence_time"] or start

        for book_name, priced in _sharpapi_book_rows(row):
            price = _number(_sharpapi_first(priced, ("odds", "price", "american_odds", "american", "moneyline")))
            if price is None:
                continue
            point = _number(_sharpapi_first(priced, ("line", "point", "handicap", "spread", "total")))
            description = _sharpapi_first(
                priced,
                ("description", "player_name", "player", "athlete_name", "athlete", "participant"),
            )
            if isinstance(description, dict):
                description = _sharpapi_first(description, ("name", "full_name", "label"))
            if is_prop and (point is None or not description):
                continue

            book = record["books"].setdefault(f"sharpapi_{_norm(book_name)}", {
                "title": book_name,
                "last_update": _sharpapi_first(priced, ("last_update", "updated_at", "timestamp", "observed_at")),
                "markets": {},
            })
            bucket = book["markets"].setdefault(market_key, {"last_update": book["last_update"], "outcomes": []})
            outcome: dict[str, Any] = {"name": str(selection), "price": price}
            if point is not None:
                outcome["point"] = point
            if is_prop:
                outcome["description"] = str(description)
            signature = (outcome["name"], outcome.get("description"), outcome.get("point"))
            if not any(
                (o.get("name"), o.get("description"), o.get("point")) == signature
                for o in bucket["outcomes"]
            ):
                bucket["outcomes"].append(outcome)

    built: list[dict[str, Any]] = []
    for record in events.values():
        books = record.pop("books")
        if not books:
            continue
        record["bookmakers"] = [
            {
                "key": key,
                "title": book["title"],
                "last_update": book["last_update"],
                "markets": [
                    {"key": market_key, "last_update": bucket["last_update"], "outcomes": bucket["outcomes"]}
                    for market_key, bucket in book["markets"].items()
                ],
            }
            for key, book in books.items()
            if book["markets"]
        ]
        if record["bookmakers"]:
            built.append(record)
    return built


def _candidate_events(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "data", "odds", "results", "games"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        if payload.get("bookmakers") or payload.get("lines") or payload.get("markets"):
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
    """Translate a provider's **native** payload into research-only Odds-API-v4 events.

    Odds-API-v4 is the internal interchange shape, not an assumption about the
    upstream: each provider gets its own native adapter and the providers
    disappear at this boundary, so everything downstream sees one established
    representation.

    Adapters are tried most-specific first. A payload matching none of them
    returns ``<PROVIDER>_SCHEMA_UNRECOGNISED`` plus a value-free structural
    probe and yields zero rows; nothing is inferred or filled in.
    """
    provider = str(provider).upper()
    marker = provider_marker(provider, capability, primary_failure=primary_failure)
    events: list[dict[str, Any]] = []

    if provider == "SHARPAPI":
        # Row-major: regroup the whole payload at once rather than per event.
        events = sharpapi_rows_to_odds_api_v4(_candidate_events(payload), sport_key=sport_key)
        if not events:
            events = [
                event for event in (
                    coerce_odds_api_v4_event(raw) for raw in _candidate_events(payload)
                ) if event is not None
            ]
    else:
        for raw in _candidate_events(payload):
            event = (
                rundown_v2_event_to_odds_api_v4(raw, sport_key=sport_key)
                or rundown_event_to_odds_api_v4(raw, sport_key=sport_key)
                or coerce_odds_api_v4_event(raw)
            )
            if event is not None:
                events.append(event)

    for event in events:
        if sport_key and not event.get("sport_key"):
            event["sport_key"] = sport_key
        event["_wow_secondary_source"] = dict(marker)
        event["_wow_market_evidence"] = dict(marker)

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


def rundown_date_offset_minutes(now: datetime | None = None) -> int:
    """Minutes to shift TheRundown's date boundary onto WOW's local slate day.

    TheRundown documents ``offset=300`` for a US Central date boundary, which
    is the current UTC offset of ``America/Chicago`` expressed as positive
    minutes behind UTC. Deriving it from the compiled zone rather than pinning
    300 keeps "today" correct across the DST transition, when the same zone is
    360. ``WOW_USER_TIMEZONE`` selects the zone, matching the nightly job.
    """
    override = os.environ.get("WOW_RUNDOWN_DATE_OFFSET_MINUTES", "").strip()
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    name = os.environ.get("WOW_USER_TIMEZONE", "America/Chicago").strip() or "America/Chicago"
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return 0
    offset = (now or datetime.now(timezone.utc)).astimezone(zone).utcoffset()
    return 0 if offset is None else int(-offset.total_seconds() // 60)


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
        params={"offset": str(rundown_date_offset_minutes())},
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
    league = sharpapi_league(sport_key)
    if not league:
        return _fail("SHARPAPI", "odds", "MARKET_EVIDENCE_UNSUPPORTED_SPORT")
    request_params: dict[str, Any] = {"league": league}
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
    "MARKET_DATA_UNOBTAINABLE",
    "SHARPAPI_SPORT_LEAGUES",
    "rundown_date_offset_minutes",
    "rundown_v2_event_to_odds_api_v4",
    "sharpapi_league",
    "sharpapi_rows_to_odds_api_v4",
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
