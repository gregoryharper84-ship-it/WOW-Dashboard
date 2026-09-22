"""Live provider-native adapters for WOW V17 research-only market evidence.

This module owns only the upstream translation boundary:

    SharpAPI native rows  ─┐
                           ├─> Odds-API-v4 internal interchange
    TheRundown V2 events ──┘

It deliberately does not score, calibrate, publish, rank, stake, or execute.
Provider probabilities/EV fields are ignored. Every emitted event is stamped
research-only and ``can_execute=False`` by the existing marker contract.

The shapes handled here are pinned from credentialed acceptance probes and
sanitized provider fixtures. Unknown shapes fail closed with structural
provider diagnostics rather than being reinterpreted as model failures.
"""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

from v17 import market_evidence_observability as observability
from v17 import market_evidence_sources as sources
from v17 import rundown_payload_contract as payload_contract
from v17 import rundown_rate_limit as rate_limit
from v17 import rundown_snapshot_cache as snapshot_cache

CAN_EXECUTE = False

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
    """Unwrap the documented/common one-level provider containers safely."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data", "odds", "results", "games"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = _candidate_events(value)
                if nested:
                    return nested
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
    nested = row.get("event") if isinstance(row.get("event"), dict) else {}
    event_id = (
        row.get("event_id") or row.get("event_uuid") or row.get("external_event_id")
        or nested.get("event_id") or nested.get("event_uuid") or nested.get("id")
    )
    home = row.get("home_team") or nested.get("home_team")
    away = row.get("away_team") or nested.get("away_team")
    start = (
        row.get("event_start_time") or row.get("commence_time") or row.get("start_time")
        or row.get("event_date") or nested.get("event_start_time") or nested.get("commence_time")
        or nested.get("start_time") or nested.get("event_date")
    )
    if event_id is None:
        if not home or not away:
            return None
        token = str(start or "")[:10].replace("-", "") or "nodate"
        event_id = f"{_norm(away)}-at-{_norm(home)}-{token}"
    return str(event_id), str(home) if home else None, str(away) if away else None, start


def _sharp_book_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand the row plus SharpAPI line-shopping ``all_books`` variants."""
    base = dict(row)
    base.pop("all_books", None)
    rows = [base]
    all_books = row.get("all_books")
    if isinstance(all_books, dict):
        for book_name, raw in all_books.items():
            container = dict(raw) if isinstance(raw, dict) else {"odds": raw}
            expanded = dict(base)
            expanded.update(container)
            expanded["sportsbook"] = container.get("sportsbook") or container.get("bookmaker") or str(book_name)
            rows.append(expanded)
    elif isinstance(all_books, list):
        for raw in all_books:
            if not isinstance(raw, dict):
                continue
            expanded = dict(base)
            expanded.update(raw)
            sportsbook = raw.get("sportsbook") or raw.get("bookmaker") or raw.get("book")
            if sportsbook:
                expanded["sportsbook"] = sportsbook
                rows.append(expanded)
    return rows


def _first_number(row: dict[str, Any], keys: tuple[str, ...]) -> int | float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


def sharpapi_rows_to_odds_api_v4(rows: Any, *, sport_key: str | None = None) -> list[dict[str, Any]]:
    """Translate observed SharpAPI row-major responses into Odds-API-v4."""
    if not isinstance(rows, list):
        return []
    grouped: dict[str, dict[str, Any]] = {}
    for source_row in rows:
        if not isinstance(source_row, dict):
            continue
        for row in _sharp_book_rows(source_row):
            identity = _sharp_event_identity(row)
            if identity is None:
                continue
            event_id, home, away, start = identity
            market_key = _canonical_market(row.get("market_type") or row.get("market") or row.get("market_key"))
            selection = row.get("selection") or row.get("selection_type") or row.get("outcome") or row.get("side")
            price = _first_number(row, ("odds_american", "american_odds", "odds", "price", "american"))
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

            updated = row.get("timestamp") or row.get("updated_at") or row.get("last_update")
            book_key = f"sharpapi_{_norm(sportsbook)}"
            book = event["books"].setdefault(book_key, {
                "title": str(sportsbook),
                "last_update": updated,
                "markets": {},
            })
            if updated:
                book["last_update"] = updated
            bucket = book["markets"].setdefault(market_key, {
                "last_update": updated,
                "outcomes": [],
            })
            if updated:
                bucket["last_update"] = updated
            outcome: dict[str, Any] = {"name": str(selection), "price": price}
            point = _first_number(row, ("line", "point", "handicap", "spread", "total"))
            is_prop = market_key not in sources.CANONICAL_MARKET_KEYS
            description = (
                row.get("description")
                or row.get("player_name")
                or row.get("player")
                or row.get("athlete_name")
                or row.get("athlete")
                or row.get("participant")
            )
            if is_prop and (point is None or not description):
                continue
            if point is not None and (market_key in {"spreads", "totals"} or sources.canonical_market_key(market_key) == market_key):
                outcome["point"] = point
            if description and is_prop:
                outcome["description"] = str(description)
            signature = (outcome.get("name"), outcome.get("description"), outcome.get("point"))
            if not any(
                (o.get("name"), o.get("description"), o.get("point")) == signature
                for o in bucket["outcomes"]
            ):
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
            if any(market["outcomes"] for market in book["markets"].values())
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
    for source in (event.get("teams_normalized"), event.get("teams"), event.get("participants")):
        if not isinstance(source, list):
            continue
        for team in source:
            if not isinstance(team, dict):
                continue
            name = team.get("name") or team.get("team_name") or team.get("full_name")
            if not name:
                continue
            side = str(team.get("type") or team.get("side") or team.get("participant_type") or "").lower()
            if team.get("is_home") is True or side == "home":
                home = home or str(name)
            elif team.get("is_away") is True or side == "away":
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
                selection = line.get("selection") or line.get("name") or line.get("side") or participant_name
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
                    if point is not None and (market_key in {"spreads", "totals"} or sources.canonical_market_key(market_key) == market_key):
                        outcome["point"] = point
                    description = participant.get("description") or participant.get("player") or participant.get("name")
                    if description and market_key not in {"h2h", "spreads", "totals"}:
                        outcome["description"] = str(description)
                    if not any(
                        o.get("name") == outcome["name"] and o.get("point") == outcome.get("point")
                        for o in bucket["outcomes"]
                    ):
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


def _snapshot_params(
    *,
    market_ids: tuple[str, ...] | list[str] | None,
    affiliate_ids: tuple[str, ...] | list[str] | None,
    main_line: bool | None,
    hide_closed: bool | None,
) -> dict[str, str]:
    """Build the narrow sport/date snapshot query.

    Every narrowing dimension is optional and omitted when unset, so the
    established request shape is unchanged unless a caller (or an operator) has
    supplied real provider ids. Narrowing is a quota measure only — it can never
    change how a payload is parsed or what a model is allowed to conclude.
    """
    params: dict[str, str] = {"offset": str(sources.rundown_date_offset_minutes())}
    if market_ids:
        params["market_ids"] = ",".join(str(value) for value in market_ids)
    if affiliate_ids:
        params["affiliate_ids"] = ",".join(str(value) for value in affiliate_ids)
    if main_line is not None:
        params["main_line"] = "true" if main_line else "false"
    if hide_closed is not None:
        params["hide_closed"] = "true" if hide_closed else "false"
    return params


def _fetch_with_bounded_429_retry(
    capability: str,
    *,
    sport_id: Any,
    date: str,
    params: dict[str, str],
    opener: Any,
    audit: dict[str, Any],
) -> sources.MarketEvidenceResult:
    """One provider fetch plus, for a burst throttle only, a bounded retry.

    Quota exhaustion and an unclassifiable 429 are returned immediately: an
    account that is out of data points does not recover by being asked again,
    and retrying an unreadable refusal is how one 429 becomes a storm.
    """
    attempt = 0
    while True:
        attempt += 1
        result = sources.fetch(
            "RUNDOWN", capability,
            path_values={"sport_id": sport_id, "date": date},
            params=params,
            opener=opener,
        )
        audit["attempts"] = attempt
        if result.ok or result.status != 429:
            return result

        limit = rate_limit.RundownRateLimit.from_dict(result.rate_limit)
        audit["rate_limit_classification"] = limit.classification
        if not rate_limit.should_retry(limit, attempt):
            return sources.MarketEvidenceResult(
                False, "RUNDOWN", capability,
                status=result.status,
                code=limit.classification,
                observed_at=result.observed_at,
                endpoint=result.endpoint,
                rate_limit=limit.as_dict(),
            )
        delay = rate_limit.retry_delay_seconds(limit, attempt)
        audit["retries"] = audit.get("retries", 0) + 1
        observability.increment("rundown_429_retries")
        if delay:
            time.sleep(delay)


def get_sport_date_odds_snapshot(
    sport_key: str,
    date: str,
    *,
    capability: str = "events",
    opener: Any = None,
    primary_failure: str | None = None,
    market_ids: tuple[str, ...] | list[str] | None = None,
    affiliate_ids: tuple[str, ...] | list[str] | None = None,
    main_line: bool | None = None,
    hide_closed: bool | None = None,
    sport_id: Any = None,
) -> sources.MarketEvidenceResult:
    """One narrow sport/date **current odds snapshot**, normalised once and shared.

    The name is deliberate. A route that returns *which markets exist* is a
    catalog and is a different contract from this one; the classifier below
    rejects a catalog rather than letting it reach the odds parser, so that
    mistake can no longer surface as an unexplained schema failure.

    Identical concurrent requests collapse to one provider call, so every scorer
    in a research run reads the same snapshot at the same timestamp.
    """
    if capability not in {"events", "openers"}:
        return sources._fail("RUNDOWN", capability, "MARKET_EVIDENCE_CAPABILITY_UNSUPPORTED")

    observability.increment("rundown_snapshot_requests")
    if sport_id is not None:
        # A caller holding a provider-verified sport id addresses the slate
        # directly. Resolving a name to an id is only for callers that do not
        # have one, and a name lookup is exactly where a guessed key used to
        # become an indistinguishable empty result.
        resolved_sport_id: Any = sport_id
    else:
        resolved = sources.rundown_sport_id(sport_key, opener=opener)
        if not resolved.ok:
            observability.record_failure(resolved.code, resolved.status)
            return resolved
        resolved_sport_id = resolved.data

    params = _snapshot_params(
        market_ids=market_ids,
        affiliate_ids=affiliate_ids,
        main_line=main_line,
        hide_closed=hide_closed,
    )
    key = snapshot_cache.snapshot_key(
        provider="RUNDOWN",
        capability=capability,
        sport_key=sport_key,
        sport_id=resolved_sport_id,
        slate_date=date,
        market_ids=market_ids,
        affiliate_ids=affiliate_ids,
        main_line=main_line,
        hide_closed=hide_closed,
    )

    audit: dict[str, Any] = {
        "provider": "RUNDOWN_MARKET_EVIDENCE",
        "endpoint_family": f"sport_date_{capability}_snapshot",
        "sport_key": sport_key,
        "provider_sport_id": resolved_sport_id,
        "requested_date": date,
        "requested_market_ids": [str(value) for value in (market_ids or ())],
        "requested_affiliate_ids": [str(value) for value in (affiliate_ids or ())],
        "main_line": main_line,
        "hide_closed": hide_closed,
        "attempts": 0,
        "retries": 0,
        "can_execute": False,
    }

    def _produce() -> sources.MarketEvidenceResult:
        started = time.monotonic()
        fetched = _fetch_with_bounded_429_retry(
            capability,
            sport_id=resolved_sport_id,
            date=date,
            params=params,
            opener=opener,
            audit=audit,
        )
        audit["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        audit["http_status"] = fetched.status
        observability.increment("rundown_provider_calls")
        if not fetched.ok:
            audit["payload_classification"] = None
            observability.record_failure(fetched.code, fetched.status)
            return sources.MarketEvidenceResult(
                False, "RUNDOWN", capability,
                status=fetched.status, code=fetched.code, observed_at=fetched.observed_at,
                endpoint=fetched.endpoint, rate_limit=fetched.rate_limit,
                request_audit=dict(audit),
            )

        classification = payload_contract.classify_rundown_payload(fetched.data)
        audit["payload_classification"] = classification.kind
        diagnostics = payload_contract.schema_diagnostics(
            classification,
            endpoint_family=str(audit["endpoint_family"]),
            http_status=fetched.status,
            requested_market_ids=market_ids,
        )
        if not classification.is_odds_snapshot:
            # The odds parser is never invoked on a payload that is not an odds
            # snapshot. A catalog, a sport index or an error envelope gets its
            # own typed contract failure plus value-free diagnostics.
            code = classification.reason_code or payload_contract.RUNDOWN_SCHEMA_UNRECOGNISED
            observability.record_failure(code, fetched.status)
            return sources.MarketEvidenceResult(
                False, "RUNDOWN", capability,
                status=fetched.status, code=code, observed_at=fetched.observed_at,
                endpoint=fetched.endpoint,
                schema_probe={**sources.structural_probe(fetched.data), "diagnostics": diagnostics},
                request_audit=dict(audit),
            )

        events = [
            event for event in (
                rundown_v2_event_to_odds_api_v4(raw, sport_key=sport_key)
                for raw in _candidate_events(fetched.data)
            ) if event is not None
        ]
        if not events:
            legacy = sources.normalize_market_payload(
                fetched.data, provider="RUNDOWN", capability=capability,
                sport_key=sport_key, primary_failure=primary_failure,
            )
            if legacy.ok:
                legacy.request_audit = dict(audit)
                observability.increment("rundown_snapshot_ok")
                return legacy
            observability.record_failure(payload_contract.RUNDOWN_SCHEMA_UNRECOGNISED, fetched.status)
            return sources.MarketEvidenceResult(
                False, "RUNDOWN", capability,
                status=fetched.status, code=payload_contract.RUNDOWN_SCHEMA_UNRECOGNISED,
                observed_at=fetched.observed_at, endpoint=fetched.endpoint,
                schema_probe={**sources.structural_probe(fetched.data), "diagnostics": diagnostics},
                request_audit=dict(audit),
            )

        marker = _marker("RUNDOWN", capability, primary_failure)
        for event in events:
            event["_wow_secondary_source"] = dict(marker)
            event["_wow_market_evidence"] = dict(marker)
        observability.increment("rundown_snapshot_ok")
        return sources.MarketEvidenceResult(
            True, "RUNDOWN", capability, data=events, status=fetched.status,
            code="MARKET_EVIDENCE_NORMALISED", observed_at=fetched.observed_at,
            request_audit=dict(audit),
        )

    result, origin = snapshot_cache.get_or_fetch(
        key, _produce, cacheable=lambda value: bool(getattr(value, "ok", False))
    )
    if origin == "CACHE":
        observability.increment("rundown_cache_hits")
    elif origin == "SINGLEFLIGHT":
        observability.increment("rundown_singleflight_hits")
    if isinstance(result.request_audit, dict):
        # Never mutate a shared cached result: callers read their own origin.
        result = replace(result, request_audit={**result.request_audit, "cache_origin": origin})
    return result


def rundown_market_evidence(
    sport_key: str,
    date: str,
    *,
    capability: str = "events",
    opener: Any = None,
    primary_failure: str | None = None,
    market_ids: tuple[str, ...] | list[str] | None = None,
    affiliate_ids: tuple[str, ...] | list[str] | None = None,
    main_line: bool | None = None,
    hide_closed: bool | None = None,
    sport_id: Any = None,
) -> sources.MarketEvidenceResult:
    """Established entry point. Delegates to the explicitly named snapshot call."""
    return get_sport_date_odds_snapshot(
        sport_key, date,
        capability=capability,
        opener=opener,
        primary_failure=primary_failure,
        market_ids=market_ids,
        affiliate_ids=affiliate_ids,
        main_line=main_line,
        hide_closed=hide_closed,
        sport_id=sport_id,
    )


__all__ = [
    "CAN_EXECUTE",
    "get_sport_date_odds_snapshot",
    "rundown_market_evidence",
    "rundown_v2_event_to_odds_api_v4",
    "sharpapi_market_evidence",
    "sharpapi_rows_to_odds_api_v4",
]
