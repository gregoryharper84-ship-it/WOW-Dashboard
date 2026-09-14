"""Structural contract for TheRundown payloads before any odds normalisation.

A provider route that returns a *market catalog* (which markets exist) is a
different contract from one that returns a *current odds snapshot* (what the
books are pricing right now).  Feeding the first into the second's parser is how
a valid HTTP 200 turned into ``RUNDOWN_SCHEMA_UNRECOGNISED`` with no way to tell
a contract mistake from a genuinely unknown schema.

This module classifies a payload *before* the odds adapter runs and emits
value-free diagnostics.  It never repairs, coerces, or widens a payload: an
unexpected shape still fails closed, it just fails closed with a typed reason.

Nothing here produces probability, ranking, settlement, or execution authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CAN_EXECUTE = False

EXPECTED_ODDS_SCHEMA = "EVENT_MARKET_PARTICIPANT_LINE_PRICE_V2"

# Payload kinds.  These describe what the provider *returned*, never what the
# caller hoped for.
SPORT_LIST = "SPORT_LIST"
AFFILIATE_LIST = "AFFILIATE_LIST"
MARKET_CATALOG = "MARKET_CATALOG"
EVENT_SNAPSHOT = "EVENT_SNAPSHOT"
EVENT_LIST_SNAPSHOT = "EVENT_LIST_SNAPSHOT"
EVENT_LIST_WITHOUT_PRICES = "EVENT_LIST_WITHOUT_PRICES"
EMPTY_EVENT_LIST = "EMPTY_EVENT_LIST"
ERROR_ENVELOPE = "ERROR_ENVELOPE"
UNKNOWN = "UNKNOWN"

# Only these kinds may reach the odds parser.
ODDS_SNAPSHOT_KINDS = frozenset({EVENT_SNAPSHOT, EVENT_LIST_SNAPSHOT})

# Typed reason codes.  ``RUNDOWN_SCHEMA_UNRECOGNISED`` is preserved for the case
# it was always meant to describe — a shape the adapter genuinely does not know.
RUNDOWN_SCHEMA_UNRECOGNISED = "RUNDOWN_SCHEMA_UNRECOGNISED"
RUNDOWN_MARKET_CATALOG_NOT_ODDS_SNAPSHOT = "RUNDOWN_MARKET_CATALOG_NOT_ODDS_SNAPSHOT"
RUNDOWN_EVENT_SNAPSHOT_WITHOUT_PRICES = "RUNDOWN_EVENT_SNAPSHOT_WITHOUT_PRICES"
RUNDOWN_EMPTY_EVENT_SLATE = "RUNDOWN_EMPTY_EVENT_SLATE"
RUNDOWN_ERROR_ENVELOPE = "RUNDOWN_ERROR_ENVELOPE"
RUNDOWN_WRONG_ENDPOINT_FAMILY = "RUNDOWN_WRONG_ENDPOINT_FAMILY"

_KIND_REASON = {
    MARKET_CATALOG: RUNDOWN_MARKET_CATALOG_NOT_ODDS_SNAPSHOT,
    SPORT_LIST: RUNDOWN_WRONG_ENDPOINT_FAMILY,
    AFFILIATE_LIST: RUNDOWN_WRONG_ENDPOINT_FAMILY,
    EVENT_LIST_WITHOUT_PRICES: RUNDOWN_EVENT_SNAPSHOT_WITHOUT_PRICES,
    EMPTY_EVENT_LIST: RUNDOWN_EMPTY_EVENT_SLATE,
    ERROR_ENVELOPE: RUNDOWN_ERROR_ENVELOPE,
    UNKNOWN: RUNDOWN_SCHEMA_UNRECOGNISED,
}

_EVENT_CONTAINER_KEYS = ("events", "data", "odds", "results", "games")
_PRICE_CONTAINER_KEYS = ("lines", "line_periods", "markets", "bookmakers", "affiliates")
_PRICE_VALUE_KEYS = (
    "moneyline_home",
    "moneyline_away",
    "moneyline_draw",
    "price",
    "odds",
    "american",
    "decimal",
    "point_spread_home_money",
    "total_over_money",
)


@dataclass(frozen=True)
class PayloadClassification:
    kind: str
    is_odds_snapshot: bool
    reason_code: str | None
    events_count: int
    priced_events_count: int
    top_level_keys: tuple[str, ...]
    payload_python_type: str
    schema_version: str | None
    market_ids_seen: tuple[str, ...]
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "is_odds_snapshot": self.is_odds_snapshot,
            "reason_code": self.reason_code,
            "events_count": self.events_count,
            "priced_events_count": self.priced_events_count,
            "top_level_keys": list(self.top_level_keys),
            "payload_python_type": self.payload_python_type,
            "schema_version": self.schema_version,
            "market_ids_seen": list(self.market_ids_seen),
            "expected_schema": EXPECTED_ODDS_SCHEMA,
            "can_execute": False,
        }


def _top_level_keys(payload: Any) -> tuple[str, ...]:
    if isinstance(payload, dict):
        return tuple(sorted(str(key) for key in payload)[:40])
    return ()


def _rows(payload: Any) -> list[dict[str, Any]]:
    """Unwrap one documented container level without inventing structure."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in _EVENT_CONTAINER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = _rows(value)
                if nested:
                    return nested
        if _looks_like_event(payload):
            return [payload]
    return []


def _looks_like_event(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("event_id") or row.get("event_uuid"):
        return True
    teams = row.get("teams") or row.get("teams_normalized") or row.get("participants")
    return bool(teams) and bool(row.get("event_date") or row.get("start_time") or row.get("commence_time"))


# TheRundown V2 nests event -> markets[] -> participants[] -> lines[] ->
# prices{affiliate_id} -> price, so the probe has to reach at least six levels
# below the event row. A shallower ceiling reports a perfectly good snapshot as
# having no prices, which is the same misdiagnosis this module exists to stop.
_PRICE_PROBE_MAX_DEPTH = 10


def _has_price(node: Any, depth: int = 0) -> bool:
    """True when a real price value is present anywhere in the price containers."""
    if depth > _PRICE_PROBE_MAX_DEPTH:
        return False
    if isinstance(node, dict):
        for key in _PRICE_VALUE_KEYS:
            value = node.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return True
            if isinstance(value, str) and value.strip().lstrip("+-").replace(".", "", 1).isdigit():
                return True
        return any(_has_price(value, depth + 1) for value in node.values())
    if isinstance(node, list):
        return any(_has_price(value, depth + 1) for value in node[:40])
    return False


def _event_has_prices(row: dict[str, Any]) -> bool:
    for key in _PRICE_CONTAINER_KEYS:
        if key in row and _has_price(row.get(key)):
            return True
    return False


def _market_ids(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    seen: list[str] = []
    for row in rows[:40]:
        containers: list[Any] = []
        for key in ("lines", "markets"):
            value = row.get(key)
            if isinstance(value, dict):
                containers.extend(value.values())
                seen.extend(str(k) for k in value)
            elif isinstance(value, list):
                containers.extend(value)
        for container in containers:
            if isinstance(container, dict):
                market_id = container.get("market_id") or container.get("id")
                if market_id is not None:
                    seen.append(str(market_id))
    ordered: list[str] = []
    for value in seen:
        if value not in ordered:
            ordered.append(value)
    return tuple(ordered[:20])


def _is_market_catalog(rows: list[dict[str, Any]], payload: Any) -> bool:
    """A catalog row describes a market definition and carries no event or price."""
    if not rows:
        return False
    if any(_looks_like_event(row) for row in rows):
        return False
    definition_hits = 0
    for row in rows[:40]:
        has_identity = any(key in row for key in ("market_id", "market_name", "market_display_name", "name"))
        has_price = _has_price(row)
        if has_identity and not has_price:
            definition_hits += 1
    if definition_hits and definition_hits == len(rows[:40]):
        return True
    if isinstance(payload, dict) and isinstance(payload.get("markets"), list) and not _has_price(payload.get("markets")):
        return True
    return False


def _is_sport_list(rows: list[dict[str, Any]], payload: Any) -> bool:
    if isinstance(payload, dict) and isinstance(payload.get("sports"), list):
        return True
    if not rows:
        return False
    return all(("sport_id" in row and "sport_name" in row) for row in rows[:40])


def _is_affiliate_list(rows: list[dict[str, Any]], payload: Any) -> bool:
    if isinstance(payload, dict) and isinstance(payload.get("affiliates"), list):
        return True
    if not rows:
        return False
    return all(("affiliate_id" in row and "affiliate_name" in row and not _looks_like_event(row)) for row in rows[:40])


def _is_error_envelope(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if any(key in payload for key in ("error", "errors", "message", "detail")) and not _rows(payload):
        return True
    code = payload.get("code")
    return isinstance(code, int) and code >= 400


def classify_rundown_payload(payload: Any) -> PayloadClassification:
    """Classify a TheRundown payload without reading or emitting any value."""
    rows = _rows(payload)
    priced = [row for row in rows if _event_has_prices(row)]
    events = [row for row in rows if _looks_like_event(row)]
    schema_version = None
    if isinstance(payload, dict):
        meta = payload.get("meta")
        if isinstance(meta, dict):
            raw_version = meta.get("version") or meta.get("schema_version")
            schema_version = str(raw_version) if raw_version is not None else None

    if _is_error_envelope(payload):
        kind = ERROR_ENVELOPE
    elif _is_sport_list(rows, payload):
        kind = SPORT_LIST
    elif _is_affiliate_list(rows, payload):
        kind = AFFILIATE_LIST
    elif _is_market_catalog(rows, payload):
        kind = MARKET_CATALOG
    elif priced:
        kind = EVENT_SNAPSHOT if len(rows) == 1 else EVENT_LIST_SNAPSHOT
    elif events:
        kind = EVENT_LIST_WITHOUT_PRICES
    elif isinstance(payload, list) and not payload:
        kind = EMPTY_EVENT_LIST
    elif isinstance(payload, dict) and any(
        isinstance(payload.get(key), list) and not payload.get(key) for key in _EVENT_CONTAINER_KEYS
    ):
        kind = EMPTY_EVENT_LIST
    else:
        kind = UNKNOWN

    return PayloadClassification(
        kind=kind,
        is_odds_snapshot=kind in ODDS_SNAPSHOT_KINDS,
        reason_code=_KIND_REASON.get(kind),
        events_count=len(events) or len(rows),
        priced_events_count=len(priced),
        top_level_keys=_top_level_keys(payload),
        payload_python_type=type(payload).__name__,
        schema_version=schema_version,
        market_ids_seen=_market_ids(rows),
    )


def schema_diagnostics(
    classification: PayloadClassification,
    *,
    endpoint_family: str,
    http_status: int | None = None,
    requested_market_ids: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Safe structural diagnostics for a rejected or accepted TheRundown payload.

    Deliberately value-free: key names, container types, counts and the reason
    code only.  No credential, header, URL query, price or participant value is
    ever included, so this can be logged verbatim.
    """
    payload = classification.as_dict()
    payload.update(
        {
            "provider": "RUNDOWN_MARKET_EVIDENCE",
            "endpoint_family": endpoint_family,
            "http_status": http_status,
            "requested_market_ids": list(requested_market_ids or ()),
        }
    )
    return payload


__all__ = [
    "AFFILIATE_LIST",
    "CAN_EXECUTE",
    "EMPTY_EVENT_LIST",
    "ERROR_ENVELOPE",
    "EVENT_LIST_SNAPSHOT",
    "EVENT_LIST_WITHOUT_PRICES",
    "EVENT_SNAPSHOT",
    "EXPECTED_ODDS_SCHEMA",
    "MARKET_CATALOG",
    "ODDS_SNAPSHOT_KINDS",
    "PayloadClassification",
    "RUNDOWN_EMPTY_EVENT_SLATE",
    "RUNDOWN_ERROR_ENVELOPE",
    "RUNDOWN_EVENT_SNAPSHOT_WITHOUT_PRICES",
    "RUNDOWN_MARKET_CATALOG_NOT_ODDS_SNAPSHOT",
    "RUNDOWN_SCHEMA_UNRECOGNISED",
    "RUNDOWN_WRONG_ENDPOINT_FAMILY",
    "SPORT_LIST",
    "UNKNOWN",
    "classify_rundown_payload",
    "schema_diagnostics",
]
