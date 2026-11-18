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
_WINNER_LANE = "MONEYLINE"
_SPREAD_LANES = frozenset({"SPREAD", "SPREADS", "POINT_SPREAD", "POINTSPREAD"})
# The shared LLP bundle governs POINT_SPREAD only. A saved puck line, run line or
# Asian handicap is a different market family and must stay visibly held rather
# than be relabelled as a point spread.
_GOVERNED_SPREAD_FAMILIES = frozenset({"POINT_SPREAD", "SPREAD", "SPREADS", "POINTSPREAD"})

CARD_QUALIFIED = "QUALIFIED"
CARD_PROVISIONAL = "PROVISIONAL"
CARD_HELD = "HELD"
CARD_MISSING_RECEIPT = "MISSING_RECEIPT"
# A saved receipt whose only defects are certification/authority is a genuine
# research line: PROVISIONAL, and still non-numeric. Any structural defect
# (identity, exact line, settlement basis, distribution, freshness, provenance)
# is HELD. Neither label publishes a probability.
_PROVISIONAL_ONLY_REASONS = frozenset({
    "UPSTREAM_UNCERTIFIED_OR_HELD",
    "MODEL_CLASS_NOT_CERTIFIED_FITTED",
    "SPREAD_BOUNDS_MISSING",
})


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
        for key in ("code", "failure_code", "failure_class"):
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
    home = _token(event.get("home_team")).upper()
    away = _token(event.get("away_team")).upper()
    side = _token(result.get("selected_side") or result.get("winner_side")).upper()
    # Score-event uses calibrated_home/away_* and global_terminal_authority,
    # rather than a generic selected-side receipt. Translate only an explicitly
    # identified selection, never infer a side from a price or larger p.
    if side == home:
        point = result.get("calibrated_home_probability")
        lower = result.get("calibrated_home_lower_bound")
        upper = result.get("calibrated_home_upper_bound")
    elif side == away:
        point = result.get("calibrated_away_probability")
        lower = result.get("calibrated_away_lower_bound")
        upper = result.get("calibrated_away_upper_bound")
    else:
        point = lower = upper = None
    # The generic validated receipt form remains accepted if it is already
    # persisted; do not substitute a guessed selected side for missing fields.
    if point is None:
        point = result.get("calibrated_probability")
        lower = result.get("calibrated_probability_lower_bound")
        upper = result.get("calibrated_probability_upper_bound")
    return {
        **event,
        "market_family": "OUTRIGHT_WINNER",
        "side": side,
        # No canonical producer-side receipt_id is present on legacy Daily
        # discovery rows. Never synthesize it from event/snapshot/run IDs.
        "source_receipt_id": result.get("source_receipt_id"),
        "probability_publishable": (
            row.get("row_status") == "COMPLETED"
            and detail.get("probability_publishable") is True
            and result.get("probability_publishable") is True
        ),
        "rank_eligible": result.get("rank_eligible") is True,
        "terminal_authority": (
            result.get("terminal_authority") or result.get("global_terminal_authority")
        ),
        "controlling_engine_identity": result.get("controlling_engine_identity"),
        "model_class": result.get("model_class"),
        "model_version": result.get("model_version"),
        "calibration_version": result.get("calibration_version"),
        "model_timestamp": result.get("model_timestamp"),
        "source_snapshot_timestamp": result.get("source_snapshot_timestamp"),
        "calibrated_probability": point,
        "calibrated_probability_lower_bound": lower,
        "calibrated_probability_upper_bound": upper,
        "can_execute": result.get("can_execute"),
    }

def _spread_market_family(row: Mapping[str, Any]) -> str:
    """Read the market family the saved row declares for itself, if any."""
    detail = _data(row.get("detail"))
    for source in (
        _data(detail.get("result")),
        _data(detail.get("identity")),
        _data(row.get("identity")),
    ):
        declared = _token(
            source.get("market_family") or source.get("market_type") or source.get("market")
        ).upper()
        if declared:
            return declared
    return ""


def _spread_receipt_from_row(
    row: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, Any]:
    """Translate one saved spread row into an exact-line bundle receipt.

    Values are passed through exactly as saved. No line, side, bound or
    probability is derived, defaulted or sign-flipped here.
    """
    detail = _data(row.get("detail"))
    result = _data(detail.get("result"))
    side = _token(
        result.get("exact_line_side") or result.get("spread_side") or result.get("selected_side")
    ).upper()
    line = result.get("exact_signed_spread")
    # Same explicit convention spread_card_admission uses: a bare home line is
    # the home side. Nothing is invented when no line was saved.
    if line is None and result.get("home_spread") is not None:
        side = side or "HOME"
        line = result.get("home_spread")
    if line is None and result.get("home_run_line") is not None:
        side = side or "HOME"
        line = result.get("home_run_line")
    return {
        **event,
        "market_family": "POINT_SPREAD",
        "side": side,
        "exact_line": line,
        "settlement_basis": result.get("settlement_basis"),
        "source_receipt_id": result.get("source_receipt_id"),
        "p_cover": result.get("p_cover"),
        "p_push": result.get("p_push"),
        "p_not_cover": result.get("p_not_cover"),
        "calibrated_probability_lower_bound": result.get(
            "calibrated_probability_lower_bound", result.get("calibrated_lower_bound")
        ),
        "calibrated_probability_upper_bound": result.get(
            "calibrated_probability_upper_bound", result.get("calibrated_upper_bound")
        ),
        "probability_publishable": (
            row.get("row_status") == "COMPLETED"
            and detail.get("probability_publishable") is True
            and result.get("probability_publishable") is True
        ),
        "rank_eligible": result.get("rank_eligible") is True,
        "terminal_authority": (
            result.get("terminal_authority") or result.get("global_terminal_authority")
        ),
        "controlling_engine_identity": result.get("controlling_engine_identity"),
        "model_class": result.get("model_class"),
        # The real spread receipts carry model_family/model_artifact_version and
        # no model_version. Keep the calibration version strictly as saved: a
        # research bound is never lifted into the calibrated-bounds slot.
        "model_version": (
            result.get("model_version")
            or result.get("model_family")
            or result.get("model_artifact_version")
        ),
        "calibration_version": result.get("calibration_version"),
        "model_timestamp": result.get("model_timestamp"),
        "source_snapshot_timestamp": result.get("source_snapshot_timestamp"),
        "can_execute": result.get("can_execute"),
    }


def _spread_card(
    entry: Mapping[str, Any] | None,
    *,
    missing_reason: str = "SPREAD_RECEIPT_NOT_SAVED_IN_DAILY_RUN",
    missing_status: str = CARD_MISSING_RECEIPT,
    declared_family: str | None = None,
    reported_line: Any = None,
) -> dict[str, Any]:
    """One event's spread presentation: status, exact line, reason codes.

    Numbers are copied only for QUALIFIED evidence, which the upstream bundle
    has already proven publishable and rank eligible.
    """
    card: dict[str, Any] = {
        "market_family": "POINT_SPREAD",
        "selected_side": None,
        "exact_line": None,
        "settlement_basis": None,
        "source_receipt_id": None,
        "model_version": None,
        "calibration_version": None,
        "upstream_probability_publishable": False,
        "upstream_rank_eligible": False,
        "probability_publishable": False,
        "probability_visible": False,
        "can_execute": False,
    }
    if entry is None:
        card["status"] = missing_status
        card["reason_codes"] = [missing_reason]
        if declared_family:
            card["declared_market_family"] = declared_family
        if reported_line is not None and missing_status == CARD_HELD:
            card["reported_exact_line"] = reported_line
        return card

    reasons = list(entry.get("reason_codes") or [])
    if entry.get("evidence_visible"):
        status = CARD_QUALIFIED
    elif reasons and set(reasons) <= _PROVISIONAL_ONLY_REASONS:
        status = CARD_PROVISIONAL
    else:
        status = CARD_HELD
    card.update({
        "selected_side": entry.get("side"),
        "exact_line": entry.get("exact_line"),
        "settlement_basis": entry.get("settlement_basis"),
        "source_receipt_id": entry.get("source_receipt_id"),
        "model_version": entry.get("model_version"),
        "calibration_version": entry.get("calibration_version"),
        "upstream_probability_publishable": entry.get("upstream_probability_publishable") is True,
        "upstream_rank_eligible": entry.get("upstream_rank_eligible") is True,
        "status": status,
        "reason_codes": reasons,
    })
    if status == CARD_QUALIFIED:
        for key in (
            "p_cover",
            "p_push",
            "p_not_cover",
            "calibrated_probability_lower_bound",
            "calibrated_probability_upper_bound",
        ):
            if key in entry:
                card[key] = entry[key]
        card["probability_visible"] = True
    return card


def project_llp_intelligence_page(page: Mapping[str, Any]) -> dict[str, Any]:
    """Page-preserving, read-only projection into one card entry per event.

    Saved MONEYLINE and SPREAD rows for the same canonical event are grouped so
    the event appears once with both markets. Every event in the page carries an
    explicit spread status: QUALIFIED, PROVISIONAL, HELD or MISSING_RECEIPT.
    Nothing is inferred: a missing line, side, settlement basis or distribution
    is a reason code, never a fabricated zero-spread or market-implied number.
    """
    rows = page.get("rows")
    raw_rows = rows if isinstance(rows, list) else []
    projected: list[dict[str, Any]] = []
    duplicate_ids: set[str] = set()
    seen_ids: set[str] = set()
    skipped_prop_rows = 0
    unsupported_sport_rows = 0
    malformed_rows = 0
    duplicate_lane_rows = 0
    groups: dict[str, dict[str, Any]] = {}

    for raw in raw_rows:
        row = _data(raw)
        lane = _token(row.get("lane")).upper()
        if lane != _WINNER_LANE and lane not in _SPREAD_LANES:
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
        group = groups.setdefault(
            event_id, {"event": event, "rows": {}, "duplicate_lanes": set()}
        )
        slot = "winner_row" if lane == _WINNER_LANE else "spread_row"
        if slot not in group["rows"]:
            # Keep each row's own identity so a same-id row with different
            # participants or start time is detected, not silently merged.
            group["rows"][slot] = (row, event)
        else:
            # Two saved rows for one lane on one event: keep the first and stay
            # explicit instead of silently choosing a thesis.
            group["duplicate_lanes"].add(slot)
            duplicate_lane_rows += 1

    summary = {
        CARD_QUALIFIED: 0,
        CARD_PROVISIONAL: 0,
        CARD_HELD: 0,
        CARD_MISSING_RECEIPT: 0,
    }
    for event_id in (str(key) for key in groups):
        group = groups[event_id]
        event = group["event"]
        winner_pair = group["rows"].get("winner_row")
        spread_pair = group["rows"].get("spread_row")
        winner_row, winner_event = winner_pair if winner_pair else (None, None)
        spread_row, spread_event = spread_pair if spread_pair else (None, None)
        declared_family = _spread_market_family(spread_row) if spread_row is not None else ""
        receipts: list[dict[str, Any]] = []
        if winner_row is not None:
            receipts.append(_winner_receipt_from_row(winner_row, winner_event))
        ungoverned_family = bool(
            spread_row is not None
            and declared_family
            and declared_family not in _GOVERNED_SPREAD_FAMILIES
        )
        if spread_row is not None and not ungoverned_family:
            receipts.append(_spread_receipt_from_row(spread_row, spread_event))
        source_row = winner_row if winner_row is not None else spread_row
        spread_entry: Mapping[str, Any] | None = None
        spread_reason = "SPREAD_RECEIPT_NOT_SAVED_IN_DAILY_RUN"
        try:
            bundle = build_llp_team_intelligence_bundle(event=event, receipts=receipts)
        except (ValueError, TypeError, KeyError):
            # A conflicting saved thesis must not sink the whole event, but it
            # must stay explicitly held instead of silently dropping a market.
            if spread_row is None or ungoverned_family:
                malformed_rows += 1
                continue
            try:
                bundle = build_llp_team_intelligence_bundle(
                    event=event,
                    receipts=[r for r in receipts if r["market_family"] != "POINT_SPREAD"],
                )
            except (ValueError, TypeError, KeyError):
                malformed_rows += 1
                continue
            spread_reason = "SPREAD_RECEIPT_CONFLICTING_THESIS"
        if not ungoverned_family and spread_row is not None:
            spread_entry = next(
                (m for m in bundle["markets"] if m["market_family"] == "POINT_SPREAD"),
                None,
            )
            if spread_entry is None and spread_reason == "SPREAD_RECEIPT_NOT_SAVED_IN_DAILY_RUN":
                spread_reason = "SPREAD_RECEIPT_NOT_PROJECTED"
        bundle["daily_row_index"] = (source_row or {}).get("row_index")
        bundle["source_diagnostics"] = _source_failure_context(source_row or {})
        if ungoverned_family:
            reported = _data(_data((spread_row or {}).get("detail")).get("result"))
            spread_card = _spread_card(
                None,
                missing_reason="SPREAD_MARKET_FAMILY_NOT_YET_GOVERNED",
                missing_status=CARD_HELD,
                declared_family=declared_family,
                reported_line=reported.get("exact_signed_spread", reported.get("home_line")),
            )
        else:
            spread_card = _spread_card(spread_entry, missing_reason=spread_reason)
        if "spread_row" in group["duplicate_lanes"]:
            # One event may not present two competing spread theses as if they
            # were one card row. Downgrade and keep every thesis visible upstream.
            spread_card["status"] = CARD_HELD
            spread_card["probability_visible"] = False
            spread_card["reason_codes"] = list(spread_card.get("reason_codes") or []) + [
                "DUPLICATE_SPREAD_THESIS_IN_PAGE"
            ]
            for key in (
                "p_cover",
                "p_push",
                "p_not_cover",
                "calibrated_probability_lower_bound",
                "calibrated_probability_upper_bound",
            ):
                spread_card.pop(key, None)
        bundle["spread_card"] = spread_card
        bundle["market_coverage"] = {
            "OUTRIGHT_WINNER": (
                "RECEIPT_PRESENT_OR_HELD" if winner_row is not None
                else "NO_WINNER_RECEIPT_IN_DAILY_RUN"
            ),
            "POINT_SPREAD": (
                "RECEIPT_PRESENT" if not ungoverned_family and spread_row is not None
                else "NO_EXACT_SPREAD_RECEIPT_IN_DAILY_RUN"
            ),
            "GAME_TOTAL": "NO_EXACT_TOTAL_RECEIPT_IN_DAILY_RUN",
        }
        summary[spread_card["status"]] = summary.get(spread_card["status"], 0) + 1
        projected.append(bundle)

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
        "duplicate_lane_rows": duplicate_lane_rows,
        "spread_card_summary": {
            "events_total": len(projected),
            "qualified": summary[CARD_QUALIFIED],
            "provisional": summary[CARD_PROVISIONAL],
            "held": summary[CARD_HELD],
            "missing_receipt": summary[CARD_MISSING_RECEIPT],
            "every_event_has_spread_status": bool(projected),
            "probability_publishable": False,
            "can_execute": False,
        },
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
