"""Governed runtime for the V17 NFL pick'em pool decision layer.

Discovery is schedule-first and canonical: the route reads the same immutable
NFLVERSE schedule snapshot used by the NFL team/event specialist, filters only
requested regular-season dates, and invokes the existing governed NFL scorer for
every discovered event. It never creates or modifies a sporting probability.

The route uses the existing cross-sport model invocation limit as a per-batch
continuation bound. That bound is not a terminal slate cap: all canonical NFL
events are attempted in sequential batches. No global scorer limit is raised.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from nflverse_historical_adapter import NFLVerseHistoricalAdapterError, parse_nflverse_kickoff
from v17.cross_sport_resilience_overlay import model_invocation_limit
from v17.daily_response_contract import persist_row_detail
from v17.nfl_pickem_pool_optimizer import (
    DECISION_OBJECTIVE,
    build_pickem_board,
    tiebreaker_capability,
)
from v17.nfl_team_event_specialist import _load_latest_schedule_snapshot
from v17.team_event_probability_preservation import TeamEventRequest, score_team_event_request

CAN_EXECUTE = False
RUNTIME_CONTRACT = "V17_NFL_PICKEM_RUNTIME_V1"
ROUTE_PATH = "/v17/nfl-pickem-board"
OPERATION_ID = "runWowV17NFLPickemBoard"


class NFLPickemBoardRequest(BaseModel):
    requested_slate_dates: list[str] = Field(default_factory=list)
    requested_timezone: str = "America/Chicago"
    expected_game_count: int = 16
    strategy_mode: str = DECISION_OBJECTIVE


def _validate_request(req: NFLPickemBoardRequest) -> tuple[tuple[str, ...], str]:
    dates = tuple(dict.fromkeys(str(value or "").strip() for value in req.requested_slate_dates))
    if not dates or len(dates) > 7 or any(not value for value in dates):
        raise ValueError("PICKEM_REQUESTED_SLATE_DATES_INVALID")
    for value in dates:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("PICKEM_REQUESTED_SLATE_DATE_INVALID") from exc
    if isinstance(req.expected_game_count, bool) or not 1 <= int(req.expected_game_count) <= 32:
        raise ValueError("PICKEM_EXPECTED_GAME_COUNT_INVALID")
    if str(req.strategy_mode or "").strip() != DECISION_OBJECTIVE:
        raise ValueError("PICKEM_STRATEGY_MODE_UNSUPPORTED")
    timezone_name = str(req.requested_timezone or "").strip()
    if not timezone_name:
        raise ValueError("PICKEM_REQUESTED_TIMEZONE_INVALID")
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise ValueError("PICKEM_REQUESTED_TIMEZONE_INVALID") from exc
    return dates, timezone_name


def _pregame_schedule_row(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("home_score") or "").strip() == ""
        and str(row.get("away_score") or "").strip() == ""
    )


def _canonical_inventory(
    db: Any,
    *,
    requested_dates: tuple[str, ...],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read canonical NFL schedule truth and return requested regular-season rows."""
    snapshot, schedule_rows = _load_latest_schedule_snapshot(db)
    requested = set(requested_dates)
    events: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in schedule_rows:
        gameday = str(raw.get("gameday") or "")[:10]
        if gameday not in requested:
            continue
        game_type = str(raw.get("game_type") or "REG").strip().upper()
        if game_type not in {"REG", "REGULAR", "REGULAR_SEASON"}:
            continue
        if not _pregame_schedule_row(raw):
            blocked.append({
                "official_event_id": str(raw.get("game_id") or "").strip() or None,
                "gameday": gameday,
                "code": "PICKEM_EVENT_ALREADY_STARTED_OR_COMPLETED",
                "can_execute": False,
            })
            continue

        event_id = str(raw.get("game_id") or "").strip()
        home = str(raw.get("home_team") or "").strip().upper()
        away = str(raw.get("away_team") or "").strip().upper()
        if not event_id or not home or not away or home == away:
            blocked.append({
                "official_event_id": event_id or None,
                "gameday": gameday,
                "code": "PICKEM_CANONICAL_EVENT_IDENTITY_INCOMPLETE",
                "home_team": home or None,
                "away_team": away or None,
                "can_execute": False,
            })
            continue
        if event_id in seen:
            blocked.append({
                "official_event_id": event_id,
                "gameday": gameday,
                "code": "PICKEM_DUPLICATE_CANONICAL_SCHEDULE_EVENT",
                "can_execute": False,
            })
            continue
        seen.add(event_id)

        try:
            kickoff = parse_nflverse_kickoff(raw)
        except NFLVerseHistoricalAdapterError as exc:
            blocked.append({
                "official_event_id": event_id,
                "gameday": gameday,
                "home_team": home,
                "away_team": away,
                "code": "MODEL_INPUTS_INSUFFICIENT",
                "blockers": [str(exc)],
                "can_execute": False,
            })
            continue

        events.append({
            "official_event_id": event_id,
            "gameday": gameday,
            "event_start_time_utc": kickoff.isoformat(),
            "home_team": home,
            "away_team": away,
            "week": raw.get("week"),
            "season": raw.get("season"),
            "game_type": game_type,
            "can_execute": False,
        })

    events.sort(key=lambda item: (item["event_start_time_utc"], item["official_event_id"]))
    return dict(snapshot), events, blocked


def _http_detail(exc: HTTPException) -> dict[str, Any]:
    if isinstance(exc.detail, Mapping):
        return dict(exc.detail)
    return {
        "code": "MODEL_SCORER_FAILED",
        "blockers": [str(exc.detail)],
        "can_execute": False,
    }


def _score_event(
    event: Mapping[str, Any],
    *,
    run_id: str,
    requested_timezone: str,
    snapshot: Mapping[str, Any],
    event_api: Any,
) -> dict[str, Any]:
    event_id = str(event["official_event_id"])
    source_snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
    snapshot_timestamp = str(snapshot.get("fetched_at") or "").strip()
    try:
        request = TeamEventRequest(
            requester_host_identity="WOW_BETTING_ENGINE",
            research_run_id=run_id,
            requested_slate_date=str(event["gameday"]),
            requested_timezone=requested_timezone,
            candidate_family="OUTRIGHT_WINNER",
            decision_intent="BEST_SIDE",
            event_key=f"NFL:{event_id}",
            official_event_id=event_id,
            event_start_time_utc=str(event["event_start_time_utc"]),
            sport="NFL",
            league="NFL",
            settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
            home_team=str(event["home_team"]),
            away_team=str(event["away_team"]),
            source_snapshot_id=source_snapshot_id or f"pickem:{event_id}",
            latest_material_update_timestamp=snapshot_timestamp or None,
        )
        result = score_team_event_request(
            request,
            event_api=event_api,
            canonical_hydration_required=True,
        )
    except HTTPException as exc:
        result = _http_detail(exc)
    except Exception as exc:  # noqa: BLE001 - invoked scorer failure remains typed
        result = {
            "code": "MODEL_SCORER_FAILED",
            "blockers": ["PICKEM_TEAM_EVENT_SCORER_EXCEPTION"],
            "error_type": type(exc).__name__,
            "model_invoked": True,
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }

    out = dict(result or {})
    existing_envelope = out.get("candidate_envelope")
    envelope = dict(existing_envelope) if isinstance(existing_envelope, Mapping) else {}
    envelope.update({
        "official_event_id": envelope.get("official_event_id") or event_id,
        "event_start_time_utc": envelope.get("event_start_time_utc") or event["event_start_time_utc"],
        "sport": "NFL",
        "league": "NFL",
        "home_team": envelope.get("home_team") or event["home_team"],
        "away_team": envelope.get("away_team") or event["away_team"],
        "source_snapshot_id": envelope.get("source_snapshot_id") or source_snapshot_id,
    })
    out["candidate_envelope"] = envelope
    out.setdefault("canonical_event_id", event_id)
    out.setdefault("official_event_id", event_id)
    out.setdefault("sport", "NFL")
    out.setdefault("league", "NFL")
    out.setdefault("home_team", str(event["home_team"]))
    out.setdefault("away_team", str(event["away_team"]))
    if source_snapshot_id:
        out.setdefault("source_snapshot_id", source_snapshot_id)
    if snapshot_timestamp:
        out.setdefault("source_snapshot_timestamp", snapshot_timestamp)
    out["can_execute"] = False
    return out


def _audit_row(source: Mapping[str, Any]) -> dict[str, Any]:
    envelope = source.get("candidate_envelope")
    envelope = envelope if isinstance(envelope, Mapping) else {}
    identity = {
        "official_event_id": envelope.get("official_event_id") or source.get("official_event_id"),
        "sport": "NFL",
        "league": "NFL",
        "home_team": envelope.get("home_team") or source.get("home_team"),
        "away_team": envelope.get("away_team") or source.get("away_team"),
    }
    completed = source.get("sporting_probability_completed") is True
    return {
        "lane": "MONEYLINE",
        "identity": identity,
        "row_status": "COMPLETED" if completed else "HELD",
        "probability_publishable": source.get("probability_publishable") is True,
        "result": dict(source),
        "terminal": True,
        "can_execute": False,
    }


def run_nfl_pickem_board(
    req: NFLPickemBoardRequest,
    *,
    db_client_fn: Any,
    event_api: Any,
) -> dict[str, Any]:
    """Score all canonical NFL events across the requested pick'em week dates."""
    requested_dates, timezone_name = _validate_request(req)
    run_id = f"nfl-pickem-{uuid4()}"
    db = db_client_fn()

    try:
        snapshot, events, discovery_blockers = _canonical_inventory(
            db,
            requested_dates=requested_dates,
        )
    except Exception as exc:  # noqa: BLE001 - acquisition failure is explicit
        return {
            "runtime_contract": RUNTIME_CONTRACT,
            "run_id": run_id,
            "status": "PICKEM_CANONICAL_DISCOVERY_FAILED",
            "submission_ready": False,
            "requested_slate_dates": list(requested_dates),
            "requested_timezone": timezone_name,
            "blockers": [f"PICKEM_CANONICAL_SCHEDULE_UNAVAILABLE:{type(exc).__name__}"],
            "tiebreaker": tiebreaker_capability(),
            "can_execute": False,
        }

    batch_size = max(1, int(model_invocation_limit()))
    source_rows: list[dict[str, Any]] = []
    batch_receipts: list[dict[str, Any]] = []
    for batch_index, start in enumerate(range(0, len(events), batch_size), 1):
        batch = events[start : start + batch_size]
        batch_results = [
            _score_event(
                event,
                run_id=run_id,
                requested_timezone=timezone_name,
                snapshot=snapshot,
                event_api=event_api,
            )
            for event in batch
        ]
        source_rows.extend(batch_results)
        batch_receipts.append({
            "batch_index": batch_index,
            "batch_size_limit": batch_size,
            "events_attempted": len(batch),
            "sporting_probabilities_completed": sum(
                1 for row in batch_results if row.get("sporting_probability_completed") is True
            ),
            "typed_holds": sum(
                1 for row in batch_results if row.get("sporting_probability_completed") is not True
            ),
            "continuation_required": start + len(batch) < len(events),
            "can_execute": False,
        })

    board = build_pickem_board(
        source_rows,
        expected_game_count=int(req.expected_game_count),
        strategy_mode=str(req.strategy_mode),
    )

    event_by_id = {str(event["official_event_id"]): event for event in events}
    for collection_name in ("picks", "blocked"):
        for item in board.get(collection_name) or []:
            event = event_by_id.get(str(item.get("official_event_id") or ""))
            if event:
                item["gameday"] = event["gameday"]
                item["scheduled_start_utc"] = event["event_start_time_utc"]
                item["week"] = event.get("week")
    order = {str(event["official_event_id"]): index for index, event in enumerate(events)}
    board["picks"].sort(key=lambda item: order.get(str(item.get("official_event_id") or ""), 10**9))
    board["blocked"].sort(key=lambda item: order.get(str(item.get("official_event_id") or ""), 10**9))

    audit_rows = [_audit_row(row) for row in source_rows]
    persistence = persist_row_detail(db, run_id=run_id, rows=audit_rows)

    return {
        "runtime_contract": RUNTIME_CONTRACT,
        "run_id": run_id,
        "status": board["status"],
        "submission_ready": board["submission_ready"],
        "requested_slate_dates": list(requested_dates),
        "requested_timezone": timezone_name,
        "expected_game_count": int(req.expected_game_count),
        "canonical_schedule_snapshot_id": snapshot.get("snapshot_id"),
        "canonical_schedule_snapshot_timestamp": snapshot.get("fetched_at"),
        "canonical_events_discovered": len(events),
        "canonical_discovery_blockers": discovery_blockers,
        "model_invocation_batch_limit": batch_size,
        "batch_count": len(batch_receipts),
        "batch_receipts": batch_receipts,
        "board": board,
        "source_receipt_persistence": persistence,
        "source_detail_ref": {
            "path": "/v17/daily-snapshot-run/{run_id}/rows",
            "run_id": run_id,
            "operation_id": "readWowV17DailySnapshotRowDetail",
            "detail_available": persistence.get("detail_available") is True,
            "can_execute": False,
        },
        "tiebreaker": tiebreaker_capability(),
        "market_probability_used": False,
        "sportsbook_price_used": False,
        "pool_popularity_used": False,
        "can_execute": False,
    }


def install_nfl_pickem_routes(
    app: FastAPI,
    *,
    auth_dependency: Any,
    db_client_fn: Any,
    event_api: Any,
) -> bool:
    """Install the authenticated pick'em route once into the active V17 app."""
    if getattr(app.state, "v17_nfl_pickem_routes_installed", False):
        return True

    @app.post(
        ROUTE_PATH,
        operation_id=OPERATION_ID,
        dependencies=[Depends(auth_dependency)],
    )
    def run_pickem(req: NFLPickemBoardRequest):
        try:
            return run_nfl_pickem_board(
                req,
                db_client_fn=db_client_fn,
                event_api=event_api,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc), "can_execute": False},
            ) from exc

    app.state.v17_nfl_pickem_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "NFLPickemBoardRequest",
    "OPERATION_ID",
    "ROUTE_PATH",
    "RUNTIME_CONTRACT",
    "install_nfl_pickem_routes",
    "run_nfl_pickem_board",
]
