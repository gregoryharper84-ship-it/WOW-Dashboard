"""Bounded exact-spread market evidence collection for the V17 challenger.

This module is evidence infrastructure only. It resolves TheRundown's canonical
full-game spread market and a small configured sportsbook set from the persisted
provider catalog, then delegates collection to the existing append-only market
ledger ingestor. The collected line is never a sporting-model feature or a
probability source.

No function in this module can publish a sporting probability, register a
specialist, change V17 terminal authority, or execute a wager/order.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from v17 import rundown_market_ingestor as ingestor
from v17 import rundown_market_ledger as ledger

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PREDICTION_AUTHORITY = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
MARKET_KEY = "spreads"
DEFAULT_BOOKS = ("Pinnacle", "Draftkings", "Fanduel")
SPORT_KEYS = {
    "NFL": "americanfootball_nfl",
    "NBA": "basketball_nba",
    "WNBA": "basketball_wnba",
    "NCAAF": "americanfootball_ncaaf",
    "NCAAB": "basketball_ncaab",
}


class SpreadMarketEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None) or []
    return [dict(row) for row in data if isinstance(row, dict)]


def _catalog_rows(client: Any, catalog_type: str) -> list[dict[str, Any]]:
    response = (
        client.table(ledger.CATALOG_TABLE)
        .select("provider_id,canonical_key,display_name,active")
        .eq("provider", ledger.PROVIDER)
        .eq("catalog_type", catalog_type)
        .execute()
    )
    return _rows(response)


def resolve_spread_collection_scope(
    client: Any,
    *,
    books: Iterable[str] = DEFAULT_BOOKS,
) -> dict[str, Any]:
    """Resolve one canonical spread market plus at least two requested books."""
    market_rows = _catalog_rows(client, "MARKET")
    spread_rows = [
        row for row in market_rows
        if row.get("provider_id") is not None
        and row.get("active") is not False
        and str(row.get("canonical_key") or "").strip().lower() == MARKET_KEY
    ]
    if len(spread_rows) != 1:
        raise SpreadMarketEvidenceError(
            "SPREAD_MARKET_ID_UNRESOLVED",
            f"expected exactly one active canonical spreads market; got {len(spread_rows)}",
        )

    requested = tuple(dict.fromkeys(str(name).strip() for name in books if str(name).strip()))
    if not requested:
        raise SpreadMarketEvidenceError("SPREAD_MARKET_BOOK_SCOPE_EMPTY", "at least one sportsbook must be requested")
    if len(requested) > 5:
        raise SpreadMarketEvidenceError("SPREAD_MARKET_BOOK_SCOPE_TOO_WIDE", "at most five sportsbooks may be requested")

    affiliate_rows = _catalog_rows(client, "AFFILIATE")
    by_name = {
        str(row.get("display_name") or "").strip().casefold(): row
        for row in affiliate_rows
        if row.get("provider_id") is not None and row.get("active") is not False
    }
    resolved: list[str] = []
    missing: list[str] = []
    for name in requested:
        row = by_name.get(name.casefold())
        if row is None:
            missing.append(name)
        else:
            resolved.append(str(row["provider_id"]))
    if missing or len(resolved) < 2:
        raise SpreadMarketEvidenceError(
            "SPREAD_MARKET_MULTI_BOOK_SCOPE_UNRESOLVED",
            f"resolved {len(resolved)} books; missing={','.join(missing) or 'none'}",
        )

    return {
        "market_ids": (str(spread_rows[0]["provider_id"]),),
        "affiliate_ids": tuple(resolved),
        "book_names": requested,
        "canonical_market_key": MARKET_KEY,
        "prediction_authority": False,
        "can_execute": False,
    }


def collect_spread_snapshot(
    client: Any,
    *,
    sport: str,
    slate_date: str,
    books: Iterable[str] = DEFAULT_BOOKS,
    opener: Any = None,
) -> dict[str, Any]:
    """Collect one bounded main-line spread snapshot into the evidence ledger."""
    normalized_sport = str(sport or "").strip().upper()
    sport_key = SPORT_KEYS.get(normalized_sport)
    if sport_key is None:
        raise SpreadMarketEvidenceError("SPREAD_MARKET_SPORT_UNSUPPORTED", f"unsupported spread sport: {normalized_sport}")
    try:
        date.fromisoformat(str(slate_date))
    except ValueError as exc:
        raise SpreadMarketEvidenceError("SPREAD_MARKET_SLATE_DATE_INVALID", "slate_date must be YYYY-MM-DD") from exc

    scope = resolve_spread_collection_scope(client, books=books)
    result = dict(ingestor.collect_snapshot(
        client,
        sport_key=sport_key,
        slate_date=str(slate_date),
        market_ids=scope["market_ids"],
        affiliate_ids=scope["affiliate_ids"],
        opener=opener,
        allow_delta=False,
        include_all_periods=False,
    ))
    status = str(result.get("status") or "").upper()
    if status not in {"COMPLETE", "PARTIAL"}:
        reason = str(result.get("reason_code") or "SPREAD_MARKET_PROVIDER_COLLECTION_FAILED")
        raise SpreadMarketEvidenceError(reason, f"spread evidence collection failed with status={status or 'UNKNOWN'}")

    return {
        "status": status,
        "sport": normalized_sport,
        "sport_key": sport_key,
        "slate_date": str(slate_date),
        "canonical_market_key": MARKET_KEY,
        "market_ids": list(scope["market_ids"]),
        "book_names": list(scope["book_names"]),
        "rows_written": int(result.get("rows_written") or 0),
        "datapoints": int(result.get("datapoints") or 0),
        "provider_status": result.get("provider_status"),
        "reason_code": result.get("reason_code"),
        "market_probability_substitution_used": False,
        "spread_line_used_as_feature": False,
        "moneyline_to_spread_conversion_used": False,
        "prediction_authority": PREDICTION_AUTHORITY,
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "automatic_certification": AUTOMATIC_CERTIFICATION,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_CERTIFICATION",
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "DEFAULT_BOOKS",
    "GLOBAL_TERMINAL_REDUCER",
    "MARKET_KEY",
    "PREDICTION_AUTHORITY",
    "PROBABILITY_PUBLISHABLE",
    "SPORT_KEYS",
    "SpreadMarketEvidenceError",
    "collect_spread_snapshot",
    "resolve_spread_collection_scope",
]
