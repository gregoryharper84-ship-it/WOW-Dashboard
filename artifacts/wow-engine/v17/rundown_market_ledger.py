"""Append-only TheRundown quote normalization for the V17 market evidence ledger.

The ledger is deliberately upstream of LLP scoring. It preserves provider event,
market, participant, book, line, quote timestamp, and fetch timestamp without
creating a sporting probability. Persistence helpers accept an injected
Supabase-compatible client so collection can run out-of-band from the scorer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from v17.rundown_market_value import american_to_decimal

CAN_EXECUTE = False
PROVIDER = "RUNDOWN"
TABLE = "wow_market_price_observations"
SYNC_TABLE = "wow_market_feed_sync_state"
CATALOG_TABLE = "wow_market_provider_catalog"
OFF_BOARD_SENTINEL = 0.0001


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: Any, *, fallback: str | None = None) -> str | None:
    if value is None:
        return fallback
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return fallback
    if dt.utcoffset() is None:
        return fallback
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _price_entries(prices: Any) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    if isinstance(prices, dict):
        for affiliate_id, container in prices.items():
            if isinstance(container, dict):
                rows.append((str(affiliate_id), dict(container)))
            else:
                rows.append((str(affiliate_id), {"price": container}))
        return rows
    if isinstance(prices, list):
        for container in prices:
            if not isinstance(container, dict):
                continue
            affiliate_id = container.get("affiliate_id") or container.get("book_id") or container.get("affiliate")
            if affiliate_id is not None:
                rows.append((str(affiliate_id), dict(container)))
    return rows


def _price_state(container: dict[str, Any]) -> tuple[float | None, bool]:
    """Return (American price, available) while preserving the off-board sentinel."""
    for key in ("price", "american", "american_odds", "odds_american", "odds"):
        value = _number(container.get(key))
        if value is None:
            continue
        if abs(value - OFF_BOARD_SENTINEL) < 1e-9:
            return None, False
        if value != 0:
            return value, True
    return None, False


def _line_value(line: dict[str, Any], container: dict[str, Any]) -> float | None:
    for holder in (container, line):
        for key in ("value", "line", "point", "spread", "total", "handicap"):
            value = _number(holder.get(key))
            if value is not None:
                return value
    return None


def _observation_key(parts: list[Any]) -> str:
    normalized = "|".join("" if part is None else str(part) for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()


def price_observations_from_rundown_event(
    raw: dict[str, Any],
    *,
    sport_key: str,
    snapshot_kind: str,
    fetched_at: str | None = None,
    is_live: bool = False,
) -> list[dict[str, Any]]:
    """Flatten a native V2 event into deterministic append-only quote rows."""
    if not isinstance(raw, dict):
        return []
    event_id = raw.get("event_id") or raw.get("event_uuid") or raw.get("id")
    markets = raw.get("markets")
    if event_id is None or not isinstance(markets, list):
        return []
    fetched = _iso(fetched_at) or _now_iso()
    event_start = _iso(raw.get("event_date") or raw.get("start_time") or raw.get("commence_time"))
    rows: list[dict[str, Any]] = []

    for market in markets:
        if not isinstance(market, dict):
            continue
        market_id = market.get("market_id") or market.get("id")
        market_name = market.get("name") or market.get("market_name") or market.get("type") or str(market_id or "UNKNOWN")
        participants = market.get("participants")
        if not isinstance(participants, list):
            continue
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            participant_id = participant.get("participant_id") or participant.get("id")
            participant_name = participant.get("name") or participant.get("full_name") or participant.get("team_name")
            participant_type = participant.get("type") or participant.get("side") or participant.get("participant_type")
            lines = participant.get("lines")
            if not isinstance(lines, list):
                continue
            for line in lines:
                if not isinstance(line, dict):
                    continue
                line_id = line.get("line_id") or line.get("id")
                selection = line.get("selection") or line.get("name") or line.get("side") or participant_name
                for affiliate_id, container in _price_entries(line.get("prices")):
                    american, available = _price_state(container)
                    closed_at = _iso(container.get("closed_at") or line.get("closed_at"))
                    if closed_at is not None:
                        available = False
                    if american is None and available:
                        continue
                    updated = _iso(
                        container.get("updated_at")
                        or container.get("date_updated")
                        or line.get("updated_at")
                        or line.get("date_updated")
                    )
                    # A quote with no provider timestamp can be stored for audit,
                    # but freshness consumers must treat it as unknown rather than
                    # using fetched_at as evidence that the price itself is fresh.
                    sportsbook = (
                        container.get("affiliate_name")
                        or container.get("sportsbook")
                        or container.get("book_name")
                        or f"affiliate_{affiliate_id}"
                    )
                    line_value = _line_value(line, container)
                    identity = [
                        PROVIDER,
                        event_id,
                        market_id,
                        participant_id or participant_name,
                        selection,
                        line_id or line_value,
                        affiliate_id,
                        updated,
                        american,
                        available,
                        closed_at,
                        bool(is_live),
                    ]
                    rows.append(
                        {
                            "observation_key": _observation_key(identity),
                            "provider": PROVIDER,
                            "provider_event_id": str(event_id),
                            "sport_key": str(sport_key),
                            "event_start_utc": event_start,
                            "market_id": str(market_id) if market_id is not None else None,
                            "market_name": str(market_name),
                            "participant_id": str(participant_id) if participant_id is not None else None,
                            "participant_name": str(participant_name or selection or "UNKNOWN"),
                            "participant_type": str(participant_type) if participant_type is not None else None,
                            "selection": str(selection or participant_name or "UNKNOWN"),
                            "line_id": str(line_id) if line_id is not None else None,
                            "line_value": line_value,
                            "affiliate_id": str(affiliate_id),
                            "sportsbook": str(sportsbook),
                            "american_odds": american,
                            "decimal_odds": american_to_decimal(american) if american is not None else None,
                            "price_updated_at": updated,
                            "fetched_at": fetched,
                            "snapshot_kind": str(snapshot_kind).upper(),
                            "is_live": bool(is_live),
                            "is_main_line": bool(line.get("is_main_line") or container.get("is_main_line")),
                            "is_available": bool(available),
                            "closed_at": closed_at,
                            "raw_payload": {
                                "market_id": market_id,
                                "participant_id": participant_id,
                                "line_id": line_id,
                                "selection": selection,
                                "affiliate_id": affiliate_id,
                                "price": american,
                                "available": available,
                                "closed_at": closed_at,
                                "updated_at": updated,
                            },
                            "prediction_authority": False,
                            "can_execute": False,
                        }
                    )
    return rows


def persist_observations(client: Any, rows: list[dict[str, Any]]) -> int:
    """Idempotently persist quote rows by deterministic observation key."""
    if not rows:
        return 0
    client.table(TABLE).upsert(rows, on_conflict="observation_key", ignore_duplicates=True).execute()
    return len(rows)


def persist_sync_state(client: Any, state: dict[str, Any]) -> None:
    payload = dict(state)
    payload["provider"] = PROVIDER
    payload["can_execute"] = False
    payload["updated_at"] = _now_iso()
    client.table(SYNC_TABLE).upsert(payload, on_conflict="feed_key").execute()


def persist_catalog_rows(client: Any, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    safe_rows = []
    for row in rows:
        payload = dict(row)
        payload["provider"] = PROVIDER
        payload["can_execute"] = False
        safe_rows.append(payload)
    client.table(CATALOG_TABLE).upsert(
        safe_rows,
        on_conflict="provider,catalog_type,provider_id",
    ).execute()
    return len(safe_rows)


__all__ = [
    "CAN_EXECUTE",
    "CATALOG_TABLE",
    "OFF_BOARD_SENTINEL",
    "PROVIDER",
    "SYNC_TABLE",
    "TABLE",
    "persist_catalog_rows",
    "persist_observations",
    "persist_sync_state",
    "price_observations_from_rundown_event",
]
