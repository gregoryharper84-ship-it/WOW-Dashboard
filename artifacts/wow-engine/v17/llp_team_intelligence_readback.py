"""Authenticated read-only LLP view over the *existing* Daily row readback.

Installed only by explicit feature flag on Daily's already-authenticated FastAPI
instance. It never runs a scorer, touches Pick'em, writes records, or changes
the existing Daily /rows response. Spread and total receipts remain unavailable
unless an independent exact-market source is actually present in the saved row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import UUID

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


def _saved_research_identity_conflict(
    row: Mapping[str, Any], event: Mapping[str, Any],
) -> bool:
    """Fail closed if persisted identities disagree about a researched event.

    The Daily projection coalesces missing identity keys from several locations.
    That fallback is valid only when *present* values do not contradict one
    another. In particular, a result receipt with another official_event_id
    must not appear as valid research for the outer row's event.
    """
    detail = _data(row.get("detail"))
    sources = (
        _data(row.get("identity")),
        _data(detail.get("identity")),
        _data(detail.get("result")),
    )
    for source in sources:
        # 'league' is a supported alias of 'sport' in persisted Daily rows.
        # A contradictory alias in the *same* source or a nested source must
        # not launder the research context onto the wrong event.
        for field in ("official_event_id", "sport", "league", "home_team", "away_team"):
            actual = source.get(field)
            if actual in (None, ""):
                continue
            expected = event.get("sport") if field == "league" else event.get(field)
            if field == "official_event_id":
                if _token(actual) != _token(expected):
                    return True
            elif _token(actual).upper() != _token(expected).upper():
                return True
        # Distinct same-teams fixtures cannot share research just because
        # their canonical game IDs were omitted in one persisted source.
        for key in ("event_start_time_utc", "commence_time_utc", "event_start_time"):
            raw = source.get(key)
            if raw in (None, ""):
                continue
            try:
                stored = datetime.fromisoformat(_token(raw).replace("Z", "+00:00"))
                start = datetime.fromisoformat(
                    _token(event.get("event_start_time_utc")).replace("Z", "+00:00")
                )
                if (
                    stored.tzinfo is None or start.tzinfo is None
                    or stored.astimezone(timezone.utc) != start.astimezone(timezone.utc)
                ):
                    return True
            except (ValueError, TypeError, OverflowError):
                return True
    return False


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
        "source_snapshot_id": result.get("source_snapshot_id"),
        "calibrated_probability": point,
        "calibrated_probability_lower_bound": lower,
        "calibrated_probability_upper_bound": upper,
        "can_execute": result.get("can_execute"),
    }

# This is the existing persisted *event* prediction ledger, not the prop
# wow_predictions table and never a new research-side evidence registry.
_EVENT_PREDICTION_TABLE = "wow_event_predictions"
_EVENT_PREDICTION_COLUMNS = ",".join((
    "event_prediction_id", "created_at", "official_event_id",
    "event_start_time", "sport", "market_family", "home_team", "away_team",
    "selected_participant", "model_version", "calibration_version",
    "model_timestamp", "source_snapshot_timestamp", "source_snapshot_id",
    "calibrated_home_probability", "calibrated_home_lower_bound",
    "calibrated_home_upper_bound", "calibrated_away_probability",
    "calibrated_away_lower_bound", "calibrated_away_upper_bound",
    "rank_eligible", "probability_publishable", "locked_at",
    "terminal_reduced_at", "can_execute",
))


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, (str, datetime)):
        return None
    try:
        parsed = (
            value if isinstance(value, datetime)
            else datetime.fromisoformat(value.replace("Z", "+00:00"))
        )
        return (
            parsed.astimezone(timezone.utc)
            if parsed.tzinfo is not None else None
        )
    except (ValueError, TypeError, OverflowError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = Decimal(str(value))
        return num if num.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _verify_governed_event_prediction(
    db: Any, *, event: Mapping[str, Any], winner: Mapping[str, Any],
) -> str | None:
    """Return typed hold reason, or None only on exact immutable ledger proof.

    A Daily detail row's strings/booleans are merely claims. Never use those
    claims as their own receipt verification. Query the independently written
    event prediction ledger by its genuine UUID and compare immutable fields.
    On unavailable/ambiguous/missing/mismatched proof, withhold visibility.
    """
    receipt_id = _token(winner.get("source_receipt_id"))
    try:
        canonical_id = str(UUID(receipt_id))
        if canonical_id != receipt_id.lower():
            return "LLP_EVENT_PREDICTION_ID_UNVERIFIED"
    except (ValueError, TypeError, AttributeError):
        return "LLP_EVENT_PREDICTION_ID_UNVERIFIED"
    if db is None:
        return "LLP_EVENT_PREDICTION_LOOKUP_NOT_PERFORMED"
    try:
        result = (
            db.table(_EVENT_PREDICTION_TABLE)
            .select(_EVENT_PREDICTION_COLUMNS)
            .eq("event_prediction_id", canonical_id)
            .limit(2)
            .execute()
        )
        rows = getattr(result, "data", None)
        if not isinstance(rows, list):
            return "LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE"
    except Exception:
        return "LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE"
    if len(rows) != 1 or not isinstance(rows[0], Mapping):
        return "LLP_EVENT_PREDICTION_NOT_EXACTLY_ONE"
    saved = rows[0]
    start = _utc(event.get("event_start_time_utc"))
    model_at = _utc(winner.get("model_timestamp"))
    snapshot_at = _utc(winner.get("source_snapshot_timestamp"))
    locked_at = _utc(saved.get("locked_at"))
    created_at = _utc(saved.get("created_at"))
    reduced_at = _utc(saved.get("terminal_reduced_at"))
    home = _token(event.get("home_team")).upper()
    away = _token(event.get("away_team")).upper()
    side = _token(winner.get("side")).upper()
    saved_side = _token(saved.get("selected_participant")).upper()
    if saved_side == "HOME":
        saved_side = home
    elif saved_side == "AWAY":
        saved_side = away
    if (
        start is None or model_at is None or snapshot_at is None
        or locked_at is None or created_at is None or reduced_at is None
        or not (snapshot_at <= model_at <= locked_at < start)
        or not (created_at <= locked_at and reduced_at < start)
        or _utc(saved.get("event_start_time")) != start
        or _utc(saved.get("model_timestamp")) != model_at
        or _utc(saved.get("source_snapshot_timestamp")) != snapshot_at
        or _token(saved.get("event_prediction_id")).lower() != canonical_id
        or _token(saved.get("official_event_id")) != _token(event.get("official_event_id"))
        or _token(saved.get("sport")).upper() != _token(event.get("sport")).upper()
        or _token(saved.get("market_family")).upper() != "OUTRIGHT_WINNER"
        or _token(saved.get("home_team")).upper() != home
        or _token(saved.get("away_team")).upper() != away
        or saved_side != side or side not in {home, away}
        or _token(saved.get("model_version")) != _token(winner.get("model_version"))
        or _token(saved.get("calibration_version")) != _token(winner.get("calibration_version"))
        or not _token(saved.get("source_snapshot_id"))
        or _token(saved.get("source_snapshot_id")).lower() != _token(winner.get("source_snapshot_id")).lower()
        or saved.get("rank_eligible") is not True
        or saved.get("probability_publishable") is not True
        or saved.get("can_execute") is not False
    ):
        return "LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"
    prefix = "home" if side == home else "away"
    for saved_field, winner_field in (
        (f"calibrated_{prefix}_probability", "calibrated_probability"),
        (f"calibrated_{prefix}_lower_bound", "calibrated_probability_lower_bound"),
        (f"calibrated_{prefix}_upper_bound", "calibrated_probability_upper_bound"),
    ):
        left = _decimal(saved.get(saved_field))
        right = _decimal(winner.get(winner_field))
        if left is None or right is None or left != right:
            return "LLP_EVENT_PREDICTION_PROBABILITY_MISMATCH"
    return None


def project_llp_intelligence_page(
    page: Mapping[str, Any], *, db: Any = None,
) -> dict[str, Any]:
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
            research = result.get("llp_deep_research_context")
            # Core saved-event identity safety is independent of the OPTIONAL
            # research context. A conflicting result cannot publish a winner
            # with or without research, and a missing/invalid research context
            # cannot by itself change a valid winner's visibility.
            conflict = (
                event["sport"] in {"NBA", "NHL"}
                and _saved_research_identity_conflict(row, event)
            )
            winner = _winner_receipt_from_row(row, event)
            ledger_hold = _verify_governed_event_prediction(
                db, event=event, winner=winner
            )
            if ledger_hold is not None or conflict:
                # Core immutable Action/ledger truth never comes from optional
                # analyst research or forged fields inside Daily row details.
                winner["probability_publishable"] = False
                winner["rank_eligible"] = False
                if conflict and research is not None:
                    research = {"persisted_event_identity_conflict": True}
            bundle = build_llp_team_intelligence_bundle(
                event=event, receipts=[winner],
                deep_research_context=research,
            )
            bundle["daily_row_index"] = row.get("row_index")
            bundle["source_diagnostics"] = _source_failure_context(row)
            if conflict:
                bundle["source_diagnostics"]["source_reason_codes"].append(
                    "LLP_RESEARCH_SAVED_EVENT_IDENTITY_CONFLICT"
                )
            if ledger_hold is not None:
                bundle["source_diagnostics"]["source_reason_codes"].append(
                    ledger_hold
                )
                bundle["markets"][0]["reason_codes"].append(ledger_hold)
            bundle["source_diagnostics"]["event_prediction_receipt_verified"] = (
                ledger_hold is None and not conflict
            )
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
    return project_llp_intelligence_page(page, db=db)


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
