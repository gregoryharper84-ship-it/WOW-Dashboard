"""Route Scout's Odds-API-v4 request paths to research-only market evidence.

This is the tertiary acquisition tier. It is attempted only after the primary
Odds API path and the ESPN secondary research feed have both failed, and it is
subject to the same ceiling as every other research tier: evidence only, no
probability, no exact-line authority, no execution.

The bridge deliberately mirrors ``scout_secondary_source.secondary_for_request``
so the caller wiring stays a few lines and no new propagation contract is
introduced (the field-propagation failure class recorded in
``.agents/memory/acquisition-routing-patch.md``).

The nightly workflow captures a research-only cross-book snapshot immediately
before discovery. Re-reading that already captured snapshot is allowed only as a
bounded acquisition fallback when duplicate live provider calls are degraded.
This prevents a provider quota/429 between the snapshot step and Scout from
throwing away evidence that was successfully captured minutes earlier. The
snapshot is never probability authority, never exact-line authority, and never
execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

from v17 import market_evidence_sources as sources
from v17 import market_evidence_native_live as live
from v17.market_evidence_snapshot import snapshot_dates

_EVENTS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events$")
_EVENT_DATA_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events/([^/]+)/(markets|odds)$")

TERTIARY_PROVIDER = "WOW_MARKET_EVIDENCE_TERTIARY"
SNAPSHOT_PROVIDER = "WOW_MARKET_EVIDENCE_SNAPSHOT_CACHE"
DEFAULT_SNAPSHOT_MAX_AGE_MINUTES = 30.0


def _norm(value: Any) -> str:
    return sources._norm(value)


def _snapshot_path() -> Path | None:
    explicit = str(os.environ.get("WOW_MARKET_EVIDENCE_SNAPSHOT_PATH") or "").strip()
    if explicit:
        return Path(explicit)
    runner_temp = str(os.environ.get("RUNNER_TEMP") or "").strip()
    if runner_temp:
        return Path(runner_temp) / "wow-multiscout" / "market-evidence.json"
    return None


def _snapshot_max_age_minutes() -> float:
    try:
        return max(1.0, min(float(os.environ.get("WOW_MARKET_EVIDENCE_SNAPSHOT_MAX_AGE_MINUTES", "30")), 120.0))
    except ValueError:
        return DEFAULT_SNAPSHOT_MAX_AGE_MINUTES


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _snapshot_events(sport_key: str) -> tuple[list[dict[str, Any]], str | None]:
    path = _snapshot_path()
    if path is None or not path.is_file():
        return [], "MARKET_EVIDENCE_SNAPSHOT_NOT_FOUND"
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError, TypeError):
        return [], "MARKET_EVIDENCE_SNAPSHOT_INVALID"
    if not isinstance(payload, dict):
        return [], "MARKET_EVIDENCE_SNAPSHOT_INVALID"
    if any(payload.get(key) is not False for key in ("prediction_authority", "exact_line_authority", "can_execute")):
        return [], "MARKET_EVIDENCE_SNAPSHOT_AUTHORITY_INVALID"
    if payload.get("research_only") is not True:
        return [], "MARKET_EVIDENCE_SNAPSHOT_AUTHORITY_INVALID"
    generated = _parse_timestamp(payload.get("generated_at"))
    if generated is None:
        return [], "MARKET_EVIDENCE_SNAPSHOT_TIMESTAMP_INVALID"
    age_minutes = (datetime.now(timezone.utc) - generated).total_seconds() / 60.0
    if age_minutes < -5.0 or age_minutes > _snapshot_max_age_minutes():
        return [], "MARKET_EVIDENCE_SNAPSHOT_STALE"
    events = payload.get("events")
    if not isinstance(events, list):
        return [], "MARKET_EVIDENCE_SNAPSHOT_EVENTS_INVALID"
    rows: list[dict[str, Any]] = []
    for raw in events:
        if not isinstance(raw, dict) or str(raw.get("sport_key") or "") != sport_key:
            continue
        event = dict(raw)
        marker = dict(event.get("_wow_market_evidence") or {})
        secondary_marker = dict(event.get("_wow_secondary_source") or {})
        for authority_marker in (marker, secondary_marker):
            if authority_marker and (
                any(authority_marker.get(key) is not False for key in ("prediction_authority", "exact_line_authority", "can_execute"))
                or authority_marker.get("research_only") is not True
            ):
                return [], "MARKET_EVIDENCE_SNAPSHOT_AUTHORITY_INVALID"
        marker.update({
            "snapshot_reused": True,
            "snapshot_generated_at": payload.get("generated_at"),
            "prediction_authority": False,
            "exact_line_authority": False,
            "research_only": True,
            "can_execute": False,
        })
        event["_wow_market_evidence"] = marker
        secondary = secondary_marker or dict(marker)
        secondary.update({
            "snapshot_reused": True,
            "snapshot_generated_at": payload.get("generated_at"),
            "prediction_authority": False,
            "exact_line_authority": False,
            "research_only": True,
            "can_execute": False,
        })
        event["_wow_secondary_source"] = secondary
        rows.append(event)
    return rows, None if rows else "MARKET_EVIDENCE_SNAPSHOT_NO_ROWS"


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for event in events:
        key = (
            str(event.get("id") or ""),
            _norm(event.get("home_team")),
            _norm(event.get("away_team")),
            str(event.get("commence_time") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(event)
    return output


def _collect(sport_key: str, *, opener: Any = None, primary_failure: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Gather every research-only event this tier can see for one sport."""
    events: list[dict[str, Any]] = []
    codes: list[str] = []

    sharp = live.sharpapi_market_evidence(sport_key, opener=opener, primary_failure=primary_failure)
    if sharp.ok:
        events.extend(sharp.data)
    else:
        codes.append(f"SHARPAPI:{sharp.code}")

    for date in snapshot_dates():
        result = live.rundown_market_evidence(
            sport_key, date, capability="events", opener=opener, primary_failure=primary_failure,
        )
        if result.ok:
            events.extend(result.data)
        else:
            codes.append(f"RUNDOWN:{result.code}")

    # Snapshot reuse is a fallback, not an additional live source. Only consult
    # it when every live tertiary acquisition path produced no usable rows.
    if not events:
        cached, cache_code = _snapshot_events(sport_key)
        if cached:
            events.extend(cached)
            codes.append(f"SNAPSHOT:{SNAPSHOT_PROVIDER}")
        elif cache_code:
            codes.append(f"SNAPSHOT:{cache_code}")

    return _dedupe_events(events), codes


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
        marker = event.get("_wow_market_evidence") or {}
        provider = str(marker.get("provider") or (SNAPSHOT_PROVIDER if marker.get("snapshot_reused") else "UNKNOWN"))
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


__all__ = ["SNAPSHOT_PROVIDER", "TERTIARY_PROVIDER", "market_evidence_for_request"]
