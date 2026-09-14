"""Default additive TheRundown current-board source for V17 ML discovery.

This module augments successful current event discovery with TheRundown V2
moneyline evidence. It never owns sporting probability, calibration, ranking,
settlement, staking, or execution. The exact controlling LLP sport specialist
remains the sole probability authority.
"""
from __future__ import annotations

import os
import re
from typing import Any

from v17 import market_evidence_sources as sources
from v17.market_evidence_snapshot import snapshot_dates

_EVENTS_RE = re.compile(r"^/odds-api/v4/sports/([^/]+)/events$")


def enabled() -> bool:
    """TheRundown is on by default for ML/current-board discovery.

    The emergency kill switch is intentionally separate from the broader market
    evidence switch so operators can disable this additive source without
    changing fitted-model or governance semantics.
    """
    return os.environ.get("WOW_RUNDOWN_ML_BOARD_ENABLED", "true").strip().lower() == "true"


def _norm(value: Any) -> str:
    return sources._norm(value)


def _event_identity(event: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _norm(event.get("home_team")),
        _norm(event.get("away_team")),
        str(event.get("commence_time") or "")[:16],
    )


def _merge_bookmakers(primary: dict[str, Any], additive: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    books: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for event in (primary, additive):
        marker = event.get("_wow_market_evidence") or event.get("_wow_secondary_source") or {}
        provider = str(marker.get("provider") or "PRIMARY")
        for book in event.get("bookmakers") or []:
            if not isinstance(book, dict):
                continue
            key = (provider, str(book.get("key") or book.get("title") or ""))
            if key in seen:
                continue
            seen.add(key)
            books.append(dict(book))
    merged["bookmakers"] = books
    merged["_wow_rundown_board_augmented"] = True
    merged["_wow_rundown_board_governance"] = {
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "market_role_evidence_only": True,
        "can_execute": False,
    }
    return merged


def _collect_rundown(sport_key: str, *, opener: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    codes: list[str] = []
    for slate_date in snapshot_dates():
        result = sources.rundown_market_evidence(
            sport_key,
            slate_date,
            capability="events",
            opener=opener,
        )
        if result.ok:
            rows.extend(result.data or [])
        else:
            codes.append(str(result.code or "RUNDOWN_UNKNOWN_FAILURE"))
    return rows, codes


def augment_event_discovery(
    path: str,
    primary_data: Any,
    *,
    opener: Any = None,
) -> tuple[Any, dict[str, Any]]:
    """Union TheRundown event rows into a successful primary event response.

    Fail-open applies only to this additive evidence source: if TheRundown is
    disabled or unavailable, the already-successful primary discovery response
    is preserved. No model status is changed.
    """
    audit = {
        "attempted": False,
        "provider": "RUNDOWN_MARKET_EVIDENCE",
        "status": "NOT_APPLICABLE",
        "rows_primary": len(primary_data) if isinstance(primary_data, list) else 0,
        "rows_rundown": 0,
        "rows_after_union": len(primary_data) if isinstance(primary_data, list) else 0,
        "reason_codes": [],
        "prediction_authority": False,
        "market_role_evidence_only": True,
        "can_execute": False,
    }
    match = _EVENTS_RE.match(path)
    if not match or not isinstance(primary_data, list):
        return primary_data, audit
    if not enabled():
        audit["status"] = "DISABLED"
        return primary_data, audit

    audit["attempted"] = True
    sport_key = match.group(1)
    rundown_rows, codes = _collect_rundown(sport_key, opener=opener)
    audit["reason_codes"] = codes
    audit["rows_rundown"] = len(rundown_rows)
    if not rundown_rows:
        audit["status"] = "UNAVAILABLE_PRESERVED_PRIMARY"
        return primary_data, audit

    output = [dict(row) for row in primary_data if isinstance(row, dict)]
    by_identity = {_event_identity(row): idx for idx, row in enumerate(output)}
    by_id = {str(row.get("id")): idx for idx, row in enumerate(output) if row.get("id")}

    for row in rundown_rows:
        if not isinstance(row, dict):
            continue
        idx = by_id.get(str(row.get("id")))
        if idx is None:
            idx = by_identity.get(_event_identity(row))
        if idx is None:
            output.append(dict(row))
            idx = len(output) - 1
            if row.get("id"):
                by_id[str(row.get("id"))] = idx
            by_identity[_event_identity(row)] = idx
        else:
            output[idx] = _merge_bookmakers(output[idx], row)

    audit["status"] = "USED"
    audit["rows_after_union"] = len(output)
    return output, audit


__all__ = ["augment_event_discovery", "enabled"]
