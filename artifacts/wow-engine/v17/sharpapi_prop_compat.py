"""SharpAPI player-prop compatibility for the V17 Scout acquisition lane.

SharpAPI's live row schema includes explicit ``is_player_prop``, ``player_name``,
``stat_category``, ``line``, ``selection`` and ``odds`` fields. The existing
adapters recognise team mainlines but discard these live prop rows as
``SHARPAPI_SCHEMA_UNRECOGNISED`` before Scout can route them to governed prop
specialists.

This module augments both SharpAPI adapter entrypoints used by V17: the generic
acceptance/normalisation adapter and the live Nightly Scout adapter. It emits the
existing Odds-API-v4 interchange shape and preserves SharpAPI as research/evidence
input only. It does not calculate probability, qualify a pick, or change terminal
or execution authority. ``can_execute`` remains false downstream.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from v17 import market_evidence_sources as sources

_INSTALLED = False
_SOURCES_ORIGINAL: Callable[..., list[dict[str, Any]]] | None = None
_LIVE_ORIGINAL: Callable[..., list[dict[str, Any]]] | None = None


def _clean_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _prop_market_key(row: dict[str, Any]) -> str | None:
    if row.get("is_player_prop") is not True:
        return None
    raw = sources._sharpapi_first(row, ("stat_category", "market_type", "market", "market_key", "bet_type"))
    key = _clean_key(raw)
    if not key:
        return None
    prop_tokens = (
        "player_", "pitcher_", "batter_", "passing_", "rushing_", "receiving_",
        "points", "rebounds", "assists", "threes", "blocks", "steals",
        "strikeouts", "outs", "shots", "saves", "goalscorer", "aces",
        "double_faults",
    )
    return key if any(token in key for token in prop_tokens) else f"player_{key}"


def _merge_outcome(bucket: dict[str, Any], outcome: dict[str, Any]) -> None:
    signature = (
        outcome.get("name"), outcome.get("description"), outcome.get("point"), outcome.get("price")
    )
    for existing in bucket["outcomes"]:
        if (
            existing.get("name"), existing.get("description"),
            existing.get("point"), existing.get("price")
        ) == signature:
            return
    bucket["outcomes"].append(outcome)


def augment_sharpapi_props(
    base_events: list[dict[str, Any]], rows: Any, *, sport_key: str | None = None,
) -> list[dict[str, Any]]:
    """Add explicit SharpAPI player-prop rows to normalized event payloads."""
    events = {str(event.get("id")): event for event in base_events if isinstance(event, dict) and event.get("id")}
    if not isinstance(rows, list):
        return list(events.values())

    for row in rows:
        if not isinstance(row, dict):
            continue
        market_key = _prop_market_key(row)
        if not market_key:
            continue
        identity = sources._sharpapi_event_identity(row)
        if identity is None:
            continue
        event_id, home, away, start = identity
        normalized_id = f"sharpapi-{event_id}"
        event = events.setdefault(normalized_id, {
            "id": normalized_id,
            "sport_key": sport_key,
            "commence_time": start,
            "home_team": home,
            "away_team": away,
            "bookmakers": [],
        })
        event["home_team"] = event.get("home_team") or home
        event["away_team"] = event.get("away_team") or away
        event["commence_time"] = event.get("commence_time") or start
        event["sport_key"] = event.get("sport_key") or sport_key

        selection = sources._sharpapi_first(row, ("selection", "outcome", "side", "name", "runner"))
        if isinstance(selection, dict):
            selection = sources._sharpapi_first(selection, ("name", "label"))
        player = sources._sharpapi_first(row, ("player_name", "player", "athlete_name", "description"))
        if not selection or not player:
            continue

        for book_name, priced in sources._sharpapi_book_rows(row):
            price = sources._number(sources._sharpapi_first(
                priced, ("odds", "price", "american_odds", "american", "moneyline")
            ))
            point = sources._number(sources._sharpapi_first(
                priced, ("line", "point", "handicap", "spread", "total")
            ))
            if price is None or point is None:
                continue
            book_key = f"sharpapi_{sources._norm(book_name)}"
            book = next((b for b in event["bookmakers"] if b.get("key") == book_key), None)
            if book is None:
                book = {
                    "key": book_key,
                    "title": book_name,
                    "last_update": sources._sharpapi_first(
                        priced, ("last_update", "updated_at", "timestamp", "observed_at")
                    ),
                    "markets": [],
                }
                event["bookmakers"].append(book)
            market = next((m for m in book["markets"] if m.get("key") == market_key), None)
            if market is None:
                market = {"key": market_key, "last_update": book.get("last_update"), "outcomes": []}
                book["markets"].append(market)
            _merge_outcome(market, {
                "name": str(selection),
                "description": str(player),
                "price": price,
                "point": point,
            })

    return [event for event in events.values() if event.get("bookmakers")]


def _wrap(original: Callable[..., list[dict[str, Any]]]) -> Callable[..., list[dict[str, Any]]]:
    def wrapped(rows: Any, *, sport_key: str | None = None) -> list[dict[str, Any]]:
        base = original(rows, sport_key=sport_key)
        return augment_sharpapi_props(base, rows, sport_key=sport_key)
    return wrapped


def install() -> None:
    """Install compatibility on both adapters used by acceptance and Nightly Scout."""
    global _INSTALLED, _SOURCES_ORIGINAL, _LIVE_ORIGINAL
    if _INSTALLED:
        return

    # Imported lazily to avoid making the generic provider module depend on the
    # live adapter during module initialization.
    from v17 import market_evidence_native_live as live

    _SOURCES_ORIGINAL = sources.sharpapi_rows_to_odds_api_v4
    _LIVE_ORIGINAL = live.sharpapi_rows_to_odds_api_v4
    sources.sharpapi_rows_to_odds_api_v4 = _wrap(_SOURCES_ORIGINAL)
    live.sharpapi_rows_to_odds_api_v4 = _wrap(_LIVE_ORIGINAL)
    _INSTALLED = True


__all__ = ["augment_sharpapi_props", "install"]
