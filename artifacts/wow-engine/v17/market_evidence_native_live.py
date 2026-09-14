"""Live provider-native adapters for WOW V17 research-only market evidence.

This module owns only the upstream translation boundary:

    SharpAPI native rows  ─┐
                           ├─> Odds-API-v4 internal interchange
    TheRundown V2 events ──┘

It deliberately does not score, calibrate, publish, rank, stake, or execute.
Provider probabilities/EV fields are ignored.  Every emitted event is stamped
research-only and ``can_execute=False`` by the existing marker contract.

The shapes handled here are pinned from the credentialed acceptance probes:

* SharpAPI rows use ``event_start_time`` and ``odds_american``.
* TheRundown V2 nests ``markets[] -> participants[] -> lines[] -> prices``.

WOW-PATCH-2026-09-14-V17-LIVE-MARKET-NATIVE-SHAPES
"""
from __future__ import annotations

from typing import Any

from v17 import market_evidence_sources as sources

CAN_EXECUTE = False

# TheRundown's documented common V2 market IDs. Name-based resolution always
# wins when present; these are a deterministic fallback for the three standard
# full-game markets supported by this research lane.
_RUNDOWN_STANDARD_MARKET_IDS: dict[str, str] = {
    "1": "h2h",
    "2": "spreads",
    "3": "totals",
}


def _number(value: Any) -> int | float | None:
    return sources._number(value)


def _norm(value: Any) -> str:
    return sources._norm(value)


def _marker(provider: str, capability: str, primary_failure: str | None) -> dict[str, Any]:
    return sources.provider_marker(provider, capability, primary_failure=primary_failure)


def _candidate_events(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data", "odds", "results", "games"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        if payload.get("event_id") or payload.get("markets") or payload.get("bookmakers"):
            return [payload]
    return []


def _canonical_market(raw: Any, market_id: Any = None) -> str | None:
    key = sources.canonical_market_key(raw)
    if key:
        return key
    return _RUNDOWN_STANDARD_MARKET_IDS.get(str(market_id)) if market_id is not None else None


# ---------------------------------------------------------------------------
# SharpAPI
# ---------------------------------------------------------------------------

def _sharp_event_identity(row: dict[str, Any]) -> tuple[str, str | None, str | None, Any] | None:
    event_id = row.get("event_id") or row.get("event_uuid") or row.get("external_event_id")
    home = row.get("home_team")
    away = row.get("away_team")
    start = (
        row.get("event_start_time")
        or row.get("commence_time")
        or row.get("start_time")
        or row.get("event_date")
    )
    if event_id is None:
        if not home or not away:
            return None
        token = str(start or "")[:10].replace("-", "") or "nodate"
        event_id = f"{_norm(away)}-at-{_norm(home)}-{token}"
    return str(event_id), str(home) if home else None, str(away) if away else None, start


def sharpapi_rows_to_odds_api_v4(rows: Any, *, sport_key: str | None = None) -> list[dict[str, Any]]:
    """Translate the observed SharpAPI row-major response into Odds-API-v4."""
    if not isinstance(rows, list):
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = _sharp_event_identity(row)
        if identity is None:
            continue
        event_id, home, away, start = identity
        market_key = _canonical_market(row.get("market_type") or row.get("market") or row.get("market_key"))
        selection = row.get("selection") or row.get("selection_type")
        price = _number(row.get("odds_american"))
        if price is None:
            price = _number(row.get("american_odds") or row.get("odds") or row.get("price"))
        sportsbook = row.get("sportsbook") or row.get("bookmaker") or row.get("book")
        if not market_key or not selection or price is None or not sportsbook:
            continue

        event = grouped.setdefault(event_id, {
            "id": f"sharpapi-{event_id}",
            "sport_key": sport_key,
            "commence_time": start,
            "home_team": home,
            "away_team": away,
            "books": {},
        })
        event["commence_time"] = event.get("commence_time") or start
        event["home_team"] = event.get("home_team") or home
        event["away_team"] = event.get("away_team") or away

        book_key = f"sharpapi_{_norm(sportsbook)}"
        book = event["books"].setdefault(book_key, {
            "title": str(sportsbook),
            "last_update": row.get("timestamp") or row.get("updated_at"),
            "markets": {},
        })
        bucket = book["markets"].setdefault(market_key, {
            "last_update": row.get("timestamp") or row.get("updated_at"),
            "outcomes": [],
        })
        outcome: dict[str, Any] = {"name": str(selection), "price": price}
        point = _number(row.get("line"))
        if point is not None:
            outcome["point"] = point
        if not any(o.get("name") == outcome["name"] and o.get("point") == outcome.get("point") for o in bucket["outcomes"]):
            bucket["outcomes"].append(outcome)

    built: list[dict[str, Any]] = []
    for event in grouped.values():
        books = event.pop("books")
        if not books:
            continue
        event["bookmakers"] = [
            {
                "key": key,
                "title": book["title"],
                "last_update": book["last_update"],
                "markets": [
                    {"key": mk, "last_update": mv["last_update"], "outcomes": mv["outcomes"]}
                    for mk, mv in book["markets"].items()
                    if mv["outcomes"]
                ],
            }
            for key, book in books.items()
            if book["markets"]
        ]
        if event["bookmakers"]:
            built.append(event)
    return built


def sharpapi_market_evidence(
    sport_key: str,
    *,
    opener: Any = None,
    primary_failure: str | None = None,
) -> sources.MarketEvidenceResult:
    league = sources.sharpapi_league(sport_key)
    if not league:
        return sources._fail("SHARPAPI", "odds", "MARKET_EVIDENCE_UNSUPPORTED_SPORT")
    fetched = sources.fetch("SHARPAPI", "odds", params={"league": str(league).upper()}, opener=opener)
    if not fetched.ok:
        return fetched
    events = sharpapi_rows_to_odds_api_v4(_candidate_events(fetched.data), sport_key=sport_key)
    if not events:
        return sources._fail(
            "SHARPAPI", "odds", "SHARPAPI_SCHEMA_UNRECOGNISED",
            status=fetched.status, schema_probe=sources.structural_probe(fetched.data),
        )
    marker = _marker("SHARPAPI", "odds", primary_failure)
    for event in events:
        event["_wow_secondary_source"] = dict(marker)
        event["_wow_market_evidence"] = dict(marker)
    return sources.MarketEvidenceResult(
        True, "SHARPAPI", "odds", data=events, status=fetched.status,
        code="MARKET_EVIDENCE_NORMALISED", observed_at=fetched.observed_at,
    )


# ---------------------------------------------------------------------------
# TheRundown V2
# ---------------------------------------------------------------------------

def _teams(event: dict[str, Any]) -> tuple[str | None, str | None]:
    home = away = None
    for source in (event.get("teams_normalized"), event.get("teams")):
        if not isinstance(source, list):
            continue
        for team in source:
            if not isinstance(team, dict):
                continue
            name = team.get("name") or team.get("team_name") or team.get("full_name")
            if not name:
                continue
            if team.get("is_home") is True or str(team.get("type") or team.get("side") or "").lower() == "home":
                home = home or str(name)
            elif team.get("is_away") is True or str(team.get("type") or team.get("side") or "").lower() == "away":
                away = away or str(name)
        if home and away:
            break
    return home, away


def _participant_name(participant: dict[str, Any], home: str | None, away: str | None) -> str | None:
    name = participant.get("name") or participant.get("full_name") or participant.get("team_name")
    if name:
        return str(name)
    side = str(participant.get("type") or participant.get("side") or participant.get("participant_type") or "").lower()
    if side == "home":
        return home
    if side == "away":
        return away
    return None


def _price_entries(prices: Any) -> list[tuple[str, Any]]:
    if isinstance(prices, dict):
        return [(str(book_id), container) for book_id, container in prices.items()]
    if isinstance(prices, list):
        out: list[tuple[str, Any]] = []
        for container in prices:
            if not isinstance(container, dict):
                continue
            book_id = container.get("affiliate_id") or container.get("book_id") or container.get("affiliate")
            if book_id is not None:
                out.append((str(book_id), container))
        return out
    return []


def _american_price(container: Any) -> int | float | None:
    if isinstance(container, (int, float, str)):
        value = _number(container)
    elif isinstance(container, dict):
        value = None
        for key in ("price", "american", "american_odds", "odds_american", "odds"):
            value = _number(container.get(key))
            if value is not None:
                break
    else:
        value = None
    # TheRundown documents 0.0001 as an off-board sentinel; it is not a price.
    if value is not None and abs(float(value) - 0.0001) < 1e-9:
        return None
    return value


def _line_point(line: dict[str, Any], container: Any) -> int | float | None:
    holders = [container, line] if isinstance(container, dict) else [line]
    for holder in holders:
        if not isinstance(holder, dict):
            continue
        for key in ("value", "line", "point", "spread", "total", "handicap"):
            value = _number(holder.get(key))
            if value is not None:
                return value
    return None


def rundown_v2_event_to_odds_api_v4(raw: Any, *, sport_key: str | None = None) -> dict[str, Any] | None:
    """Translate observed V2 ``market -> participant -> line -> prices`` data."""
    if not isinstance(raw, dict):
        return None
    markets = raw.get("markets")
    event_id = raw.get("event_id") or raw.get("event_uuid") or raw.get("id")
    if not event_id or not isinstance(markets, list) or not markets:
        return None
    home, away = _teams(raw)
    books: dict[str, dict[str, Any]] = {}

    for market in markets:
        if not isinstance(market, dict):
            continue
        market_id = market.get("market_id") or market.get("id")
        market_key = _canonical_market(
            market.get("name") or market.get("market_name") or market.get("type"),
            market_id,
        )
        if not market_key:
            continue
        participants = market.get("participants")
        if not isinstance(participants, list):
            continue

        for participant in participants:
            if not isinstance(participant, dict):
                continue
            participant_name = _participant_name(participant, home, away)
            lines = participant.get("lines")
            if not isinstance(lines, list):
                continue
            for line in lines:
                if not isinstance(line, dict):
                    continue
                selection = (
                    line.get("selection")
                    or line.get("name")
                    or line.get("side")
                    or participant_name
                )
                if market_key == "totals":
                    token = str(selection or participant.get("name") or participant.get("type") or "").lower()
                    if "over" in token:
                        selection = "Over"
                    elif "under" in token:
                        selection = "Under"
                if not selection:
                    continue
                for affiliate_id, container in _price_entries(line.get("prices")):
                    price = _american_price(container)
                    if price is None:
                        continue
                    title = None
                    if isinstance(container, dict):
                        title = container.get("affiliate_name") or container.get("sportsbook") or container.get("book_name")
                    title = str(title or f"affiliate_{affiliate_id}")
                    book_key = f"rundown_{_norm(title) or affiliate_id}"
                    updated = None
                    if isinstance(container, dict):
                        updated = container.get("updated_at") or container.get("date_updated")
                    updated = updated or line.get("updated_at") or line.get("date_updated") or raw.get("event_date")
                    book = books.setdefault(book_key, {"title": title, "last_update": updated, "markets": {}})
                    bucket = book["markets"].setdefault(market_key, {"last_update": updated, "outcomes": []})
                    outcome: dict[str, Any] = {"name": str(selection), "price": price}
                    point = _line_point(line, container)
                    if point is not None and market_key in {"spreads", "totals"}:
                        outcome["point"] = point
                    if not any(o.get("name") == outcome["name"] and o.get("point") == outcome.get("point") for o in bucket["outcomes"]):
                        bucket["outcomes"].append(outcome)

    if not books:
        return None
    return {
        "id": f"rundown-{event_id}",
        "sport_key": sport_key,
        "commence_time": raw.get("event_date") or raw.get("start_time"),
        "home_team": home,
        "away_team": away,
        "bookmakers": [
            {
                "key": key,
                "title": book["title"],
                "last_update": book["last_update"],
                "markets": [
                    {"key": mk, "last_update": mv["last_update"], "outcomes": mv["outcomes"]}
                    for mk, mv in book["markets"].items()
                    if mv["outcomes"]
                ],
            }
            for key, book in books.items()
            if book["markets"]
        ],
    }


def rundown_market_evidence(
    sport_key: str,
    date: str,
    *,
    capability: str = "events",
    opener: Any = None,
    primary_failure: str | None = None,
) -> sources.MarketEvidenceResult:
    if capability not in {"events", "openers"}:
        return sources._fail("RUNDOWN", capability, "MARKET_EVIDENCE_CAPABILITY_UNSUPPORTED")
    resolved = sources.rundown_sport_id(sport_key, opener=opener)
    if not resolved.ok:
        return resolved
    fetched = sources.fetch(
        "RUNDOWN", capability,
        path_values={"sport_id": resolved.data, "date": date},
        params={"offset": str(sources.rundown_date_offset_minutes())},
        opener=opener,
    )
    if not fetched.ok:
        return fetched
    events = [
        event for event in (
            rundown_v2_event_to_odds_api_v4(raw, sport_key=sport_key)
            for raw in _candidate_events(fetched.data)
        ) if event is not None
    ]
    if not events:
        # Preserve the intentional V1/Odds-v4 compatibility fallback while the
        # live V2 adapter remains the controlling first path.
        legacy = sources.normalize_market_payload(
            fetched.data, provider="RUNDOWN", capability=capability,
            sport_key=sport_key, primary_failure=primary_failure,
        )
        if legacy.ok:
            return legacy
        return sources._fail(
            "RUNDOWN", capability, "RUNDOWN_SCHEMA_UNRECOGNISED",
            status=fetched.status, schema_probe=sources.structural_probe(fetched.data),
        )
    marker = _marker("RUNDOWN", capability, primary_failure)
    for event in events:
        event["_wow_secondary_source"] = dict(marker)
        event["_wow_market_evidence"] = dict(marker)
    return sources.MarketEvidenceResult(
        True, "RUNDOWN", capability, data=events, status=fetched.status,
        code="MARKET_EVIDENCE_NORMALISED", observed_at=fetched.observed_at,
    )


__all__ = [
    "CAN_EXECUTE",
    "rundown_market_evidence",
    "rundown_v2_event_to_odds_api_v4",
    "sharpapi_market_evidence",
    "sharpapi_rows_to_odds_api_v4",
]
