"""Route Scout's Odds-API-v4 request paths to research-only market evidence.

This is the tertiary acquisition tier. It is attempted only after the primary
Odds API path and the ESPN secondary research feed have both failed, and it is
subject to the same ceiling as every other research tier: evidence only, no
probability, no exact-line authority, no execution.

The bridge deliberately mirrors ``scout_secondary_source.secondary_for_request``
so the caller wiring stays a few lines and no new propagation contract is
introduced (the field-propagation failure class recorded in
``.agents/memory/acquisition-routing-patch.md``).
"""
from __future__ import annotations

import re
from typing import Any

from v17 import market_evidence_sources as sources
from v17.market_evidence_snapshot import snapshot_dates

_EVENTS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events$")
_EVENT_DATA_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events/([^/]+)/(markets|odds)$")

TERTIARY_PROVIDER = "WOW_MARKET_EVIDENCE_TERTIARY"


def _norm(value: Any) -> str:
    return sources._norm(value)


def _collect(sport_key: str, *, opener: Any = None, primary_failure: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Gather every research-only event this tier can see for one sport."""
    events: list[dict[str, Any]] = []
    codes: list[str] = []

    sharp = sources.sharpapi_market_evidence(sport_key, opener=opener, primary_failure=primary_failure)
    if sharp.ok:
        events.extend(sharp.data)
    else:
        codes.append(f"SHARPAPI:{sharp.code}")

    for date in snapshot_dates():
        result = sources.rundown_market_evidence(
            sport_key, date, capability="events", opener=opener, primary_failure=primary_failure,
        )
        if result.ok:
            events.extend(result.data)
        else:
            codes.append(f"RUNDOWN:{result.code}")

    return events, codes


def _merge_books(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold same-event rows from different providers into one cross-book payload.

    Books are keyed by ``provider_key`` so two providers quoting the same book
    can never be silently collapsed into a single row (the join-key collision
    class from the prior acquisition patch).
    """
    merged = dict(events[0])
    seen: set[str] = set()
    books: list[dict[str, Any]] = []
    for event in events:
        provider = str((event.get("_wow_market_evidence") or {}).get("provider") or "UNKNOWN")
        for book in event.get("bookmakers") or []:
            if not isinstance(book, dict):
                continue
            key = f"{_norm(provider)}__{book.get('key')}"
            if key in seen:
                continue
            seen.add(key)
            books.append({**book, "key": key, "source_provider": provider})
    merged["bookmakers"] = books
    return merged


def market_evidence_for_request(
    path: str,
    params: dict[str, Any] | None,
    event_context: dict[str, dict[str, Any]],
    *,
    primary_failure: str | None = None,
    opener: Any = None,
) -> sources.MarketEvidenceResult:
    if not sources.ENABLED:
        return sources._fail(TERTIARY_PROVIDER, "request", "MARKET_EVIDENCE_DISABLED")

    match = _EVENTS_RE.match(path)
    if match:
        sport_key = match.group(1)
        events, codes = _collect(sport_key, opener=opener, primary_failure=primary_failure)
        if not events:
            return sources._fail(TERTIARY_PROVIDER, "events", "MARKET_EVIDENCE_NO_ROWS", schema_probe={"provider_codes": codes})
        return sources.MarketEvidenceResult(
            True, TERTIARY_PROVIDER, "events", data=events, status=200, code="MARKET_EVIDENCE_USED",
        )

    match = _EVENT_DATA_RE.match(path)
    if match:
        sport_key, event_id, _kind = match.groups()
        context = event_context.get(event_id) or {}
        events, codes = _collect(sport_key, opener=opener, primary_failure=primary_failure)
        matches = [
            event for event in events
            if str(event.get("id")) == event_id
            or (
                context
                and _norm(event.get("home_team")) == _norm(context.get("home_team"))
                and _norm(event.get("away_team")) == _norm(context.get("away_team"))
            )
        ]
        if not matches:
            return sources._fail(TERTIARY_PROVIDER, "event_odds", "MARKET_EVIDENCE_EVENT_NOT_FOUND", status=404, schema_probe={"provider_codes": codes})
        return sources.MarketEvidenceResult(
            True, TERTIARY_PROVIDER, "event_odds", data=_merge_books(matches), status=200, code="MARKET_EVIDENCE_USED",
        )

    return sources._fail(TERTIARY_PROVIDER, "request", "MARKET_EVIDENCE_UNSUPPORTED_REQUEST")


__all__ = ["TERTIARY_PROVIDER", "market_evidence_for_request"]
