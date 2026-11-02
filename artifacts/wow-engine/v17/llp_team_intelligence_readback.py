"""Authenticated read-only LLP view over the *existing* Daily row readback.

Installed only by explicit feature flag on Daily's already-authenticated FastAPI
instance. It never runs a scorer, touches Pick'em, writes records, or changes
the existing Daily /rows response. Spread and total receipts remain unavailable
unless an independent exact-market source is actually present in the saved row.
"""
from __future__ import annotations

from typing import Any, Mapping

from fastapi import FastAPI, HTTPException

from v17.daily_response_contract import (
    DETAIL_PAGE_DEFAULT_LIMIT,
    DETAIL_PAGE_MAX_LIMIT,
    read_row_detail_page,
)
from v17.llp_team_intelligence_bundle import build_llp_team_intelligence_bundle

CAN_EXECUTE = False
ROUTE_PATH = "/v17/llp-team-intelligence/{run_id}/events"
OPERATION_ID = "readLlpV17TeamIntelligenceEvents"
INSTALL_STATE = "wow_v17_llp_team_intelligence_readback_installed"
_SUPPORTED = frozenset({"NFL", "NBA", "NHL"})


def _token(value: Any) -> str:
    return str(value or "").strip()


def _data(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _event_from_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    identity = _data(row.get("identity"))
    detail = _data(row.get("detail"))
    original = _data(detail.get("identity"))
    result = _data(detail.get("result"))
    # Stored Daily records are heterogeneous: canonical MLB rows use
    # event_start_time, while broad discovery uses commence_time_utc.
    def get(*names: str) -> Any:
        for name in names:
            for source in (identity, original, result):
                value = source.get(name)
                if value not in (None, ""):
                    return value
        return None

    sport = _token(get("sport", "league")).upper()
    if sport not in _SUPPORTED:
        return None
    return {
        "sport": sport,
        "official_event_id": get("official_event_id"),
        "home_team": get("home_team"),
        "away_team": get("away_team"),
        "event_start_time_utc": get(
            "event_start_time_utc", "commence_time_utc", "event_start_time"
        ),
    }


def _winner_receipt_from_row(row: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    detail = _data(row.get("detail"))
    result = _data(detail.get("result"))
    # This projection does NOT invent certification or promote a held row.
    # All contract fields must be *persisted evidence*, not inferred from
    # the route, sport, an odds line, or the enclosing Daily terminal state.
    return {
        **event,
        "market_family": "OUTRIGHT_WINNER",
        "side": result.get("selected_side") or result.get("winner_side"),
        "source_receipt_id": result.get("source_receipt_id"),
        "probability_publishable": (
            row.get("row_status") == "COMPLETED"
            and detail.get("probability_publishable") is True
            and result.get("probability_publishable") is True
        ),
        "rank_eligible": result.get("rank_eligible") is True,
        "terminal_authority": result.get("terminal_authority"),
        "controlling_engine_identity": result.get("controlling_engine_identity"),
        "model_class": result.get("model_class"),
        "model_version": result.get("model_version"),
        "calibration_version": result.get("calibration_version"),
        "model_timestamp": result.get("model_timestamp"),
        "source_snapshot_timestamp": result.get("source_snapshot_timestamp"),
        "calibrated_probability": result.get("calibrated_probability"),
        "calibrated_probability_lower_bound": result.get("calibrated_probability_lower_bound"),
        "calibrated_probability_upper_bound": result.get("calibrated_probability_upper_bound"),
        "can_execute": result.get("can_execute"),
    }


def project_llp_intelligence_page(page: Mapping[str, Any]) -> dict[str, Any]:
    """Page-preserving, read-only projection. Missing proof stays HELD.

    Only existing canonical Daily MONEYLINE rows are projected; this never
    makes a fabricated zero-spread or estimated total from moneyline output.
    """
    rows = page.get("rows")
    raw_rows = rows if isinstance(rows, list) else []
    projected: list[dict[str, Any]] = []
    duplicate_ids: set[str] = set()
    seen_ids: set[str] = set()
    skipped_prop_rows = 0
    malformed_rows = 0

    for raw in raw_rows:
        row = _data(raw)
        if row.get("lane") != "MONEYLINE":
            skipped_prop_rows += 1
            continue
        event = _event_from_row(row)
        if event is None:
            malformed_rows += 1
            continue
        event_id = _token(event["official_event_id"])
        if event_id in seen_ids:
            duplicate_ids.add(event_id)
        seen_ids.add(event_id)
        try:
            bundle = build_llp_team_intelligence_bundle(
                event=event, receipts=[_winner_receipt_from_row(row, event)]
            )
            bundle["daily_row_index"] = row.get("row_index")
            # Daily's underlying data has winner rows, not certified
            # independent spread/game-total scorer packages.
            bundle["market_coverage"] = {
                "OUTRIGHT_WINNER": "RECEIPT_PRESENT_OR_HELD",
                "POINT_SPREAD": "NO_EXACT_SPREAD_RECEIPT_IN_DAILY_RUN",
                "GAME_TOTAL": "NO_EXACT_TOTAL_RECEIPT_IN_DAILY_RUN",
            }
            projected.append(bundle)
        except (ValueError, TypeError, KeyError):
            malformed_rows += 1

    return {
        "run_id": page.get("run_id"),
        "status": "DETAIL_UNAVAILABLE" if page.get("status") == "DETAIL_UNAVAILABLE"
            else "READ_ONLY_PROJECTION",
        "source_status": page.get("status"),
        "events": projected,
        "source_rows_returned": page.get("returned", len(raw_rows)),
        "non_team_event_rows_skipped": skipped_prop_rows,
        "invalid_event_rows": malformed_rows,
        "duplicate_event_ids_in_page": sorted(duplicate_ids),
        "source_offset": page.get("offset"),
        "source_next_offset": page.get("next_offset"),
        "blockers": list(page.get("blockers") or []),
        "board_complete": False,  # a single paged read is never the full-slate proof
        "source_mutated": False,
        "nfl_pickem_mutated": False,
        "can_execute": False,
    }


def read_llp_team_intelligence_page(
    db: Any, *, run_id: str, offset: int = 0, limit: int = DETAIL_PAGE_DEFAULT_LIMIT,
) -> dict[str, Any]:
    if not _token(run_id) or len(_token(run_id)) > 128:
        raise ValueError("LLP_DAILY_RUN_ID_INVALID")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("LLP_DAILY_OFFSET_INVALID")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= DETAIL_PAGE_MAX_LIMIT:
        raise ValueError("LLP_DAILY_LIMIT_INVALID")
    page = read_row_detail_page(db, run_id=run_id, offset=offset, limit=limit)
    return project_llp_intelligence_page(page)


def install_llp_team_intelligence_readback(
    app: FastAPI, *, auth_dependency: Any, db_client_fn: Any,
) -> bool:
    """Explicit install, never changes existing scoring/board/route handlers."""
    if getattr(app.state, INSTALL_STATE, False):
        return False
    if any(getattr(route, "path", None) == ROUTE_PATH for route in app.router.routes):
        return False
    setattr(app.state, INSTALL_STATE, True)

    @app.get(ROUTE_PATH, dependencies=[auth_dependency], operation_id=OPERATION_ID)
    def llp_team_intelligence_events(
        run_id: str, offset: int = 0, limit: int = DETAIL_PAGE_DEFAULT_LIMIT,
    ):
        try:
            return read_llp_team_intelligence_page(
                db_client_fn(), run_id=run_id, offset=offset, limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc), "can_execute": False},
            ) from exc

    return True
