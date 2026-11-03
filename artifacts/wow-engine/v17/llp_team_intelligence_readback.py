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


def _source_failure_context(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep persisted typed upstream holds visible, without promoting the row.

    A downstream LLP view must never replace source failure codes with only
    its own missing-certificate reasons. Do not echo arbitrary raw provider text.
    """
    detail = _data(row.get("detail"))
    result = _data(detail.get("result"))
    nested = _data(result.get("detail"))
    codes: list[str] = []
    for source in (result, nested):
        for key in ("blockers", "failure_codes", "reason_codes"):
            raw = source.get(key)
            values = raw if isinstance(raw, list) else []
            for value in values:
                if isinstance(value, str) and 1 <= len(value) <= 128:
                    token = value.split(":", 1)[0].strip().upper()
                    if token and all(ch.isalnum() or ch in "_-." for ch in token):
                        if token not in codes:
                            codes.append(token)
    for source in (result, nested):
        for key in ("code", "failure_code", "failure_class", "reason", "bucket"):
            value = source.get(key)
            if isinstance(value, str):
                token = value.strip().upper()
                if token and len(token) <= 128 and all(
                    ch.isalnum() or ch in "_-." for ch in token
                ) and token not in codes:
                    codes.append(token)
    return {
        "source_row_status": row.get("row_status"),
        "source_model_status": result.get("model_status"),
        "source_governance_status": result.get("governance_status"),
        "source_reason_codes": codes[:32],
        "source_reason_codes_truncated": max(0, len(codes) - 32),
    }


def _winner_receipt_from_row(row: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    detail = _data(row.get("detail"))
    result = _data(detail.get("result"))
    nested = _data(result.get("detail"))
    # Persisted discovery rows wrap the real scorer output in result.detail.
    # Prefer that receipt only if it contains a scorer-owned winner field;
    # an unrelated nested failure packet must not erase a flat legacy receipt.
    scorer = nested if any(key in nested for key in (
        "selected_side", "winner_side", "calibrated_home_probability",
        "calibrated_away_probability", "calibrated_probability",
    )) else result
    home = _token(event.get("home_team")).upper()
    away = _token(event.get("away_team")).upper()
    side = _token(scorer.get("selected_side") or scorer.get("winner_side")).upper()
    # Score-event uses calibrated_home/away_* and global_terminal_authority,
    # rather than a generic selected-side receipt. Translate only an explicitly
    # identified selection, never infer a side from a price or larger p.
    if side == home:
        point = scorer.get("calibrated_home_probability")
        lower = scorer.get("calibrated_home_lower_bound")
        upper = scorer.get("calibrated_home_upper_bound")
    elif side == away:
        point = scorer.get("calibrated_away_probability")
        lower = scorer.get("calibrated_away_lower_bound")
        upper = scorer.get("calibrated_away_upper_bound")
    else:
        point = lower = upper = None
    # The generic validated receipt form remains accepted if it is already
    # persisted; do not substitute a guessed selected side for missing fields.
    if point is None:
        point = scorer.get("calibrated_probability")
        lower = scorer.get("calibrated_probability_lower_bound")
        upper = scorer.get("calibrated_probability_upper_bound")
    return {
        **event,
        "market_family": "OUTRIGHT_WINNER",
        "side": side,
        # No canonical producer-side receipt_id is present on legacy Daily
        # discovery rows. Never synthesize it from event/snapshot/run IDs.
        "source_receipt_id": (
            scorer.get("source_receipt_id") or result.get("source_receipt_id")
        ),
        "probability_publishable": (
            row.get("row_status") == "COMPLETED"
            and detail.get("probability_publishable") is True
            and result.get("probability_publishable") is True
        ),
        "rank_eligible": result.get("rank_eligible") is True,
        "terminal_authority": (
            scorer.get("terminal_authority")
            or scorer.get("global_terminal_authority")
            or result.get("terminal_authority")
            or result.get("global_terminal_authority")
        ),
        "controlling_engine_identity": (
            scorer.get("controlling_engine_identity")
            or result.get("controlling_engine_identity")
        ),
        "model_class": scorer.get("model_class") or result.get("model_class"),
        "model_version": scorer.get("model_version") or result.get("model_version"),
        "calibration_version": scorer.get("calibration_version") or result.get("calibration_version"),
        "model_timestamp": scorer.get("model_timestamp") or result.get("model_timestamp"),
        "source_snapshot_timestamp": (
            scorer.get("source_snapshot_timestamp")
            or result.get("source_snapshot_timestamp")
        ),
        "calibrated_probability": point,
        "calibrated_probability_lower_bound": lower,
        "calibrated_probability_upper_bound": upper,
        "can_execute": False,
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
    unsupported_sport_rows = 0
    malformed_rows = 0

    for raw in raw_rows:
        row = _data(raw)
        if row.get("lane") != "MONEYLINE":
            skipped_prop_rows += 1
            continue
        # Unsupported broad-discovery sports are valid Daily rows, not data
        # defects in the three-sport LLP-specific view.
        identity = _data(row.get("identity"))
        original = _data(_data(row.get("detail")).get("identity"))
        result = _data(_data(row.get("detail")).get("result"))
        source_sport = _token(
            identity.get("sport") or original.get("sport") or result.get("sport")
            or result.get("league")
        ).upper()
        if source_sport and source_sport not in _SUPPORTED:
            unsupported_sport_rows += 1
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
            bundle["source_diagnostics"] = _source_failure_context(row)
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
        "unsupported_sport_rows_skipped": unsupported_sport_rows,
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
