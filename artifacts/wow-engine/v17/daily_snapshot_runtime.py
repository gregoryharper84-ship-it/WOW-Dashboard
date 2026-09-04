"""Bounded, server-owned V17 Daily snapshot runner.

Daily first consumes canonical pregame snapshots.  If the PROPS lane is empty,
it invokes the certified server-owned prop acquisition producer once and then
re-queries canonical snapshots before returning a zero-row terminal receipt.
It never invents a probability or executable wager.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from v17.daily_prop_acquisition import acquire_daily_prop_snapshots
from v17.prop_forward_cohort_route import install_prop_forward_cohort_route
from v17.team_event_probability_preservation import TeamEventRequest, score_team_event_request


class DailySnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requested_slate_date: str
    requested_timezone: str = Field(min_length=1, max_length=64)
    lanes: list[Literal["PROPS", "MONEYLINE"]] = Field(default_factory=lambda: ["PROPS", "MONEYLINE"])
    max_props: int = Field(default=6, ge=0, le=12)
    max_team_events: int = Field(default=6, ge=0, le=12)


def _detail(exc: HTTPException) -> dict[str, Any]:
    return dict(exc.detail) if isinstance(exc.detail, dict) else {"code": "HTTP_EXCEPTION", "message": str(exc.detail)}


def _terminal_row(lane: str, identity: dict[str, Any], payload: dict[str, Any], row_status: str) -> dict[str, Any]:
    publishable = bool(payload.get("probability_publishable") is True and payload.get("rank_eligible") is True)
    return {"lane": lane, "identity": identity, "result": payload, "terminal": True, "row_status": row_status, "probability_publishable": publishable, "can_execute": False}


def _props_row_status(outcomes: list[dict[str, Any]]) -> str:
    statuses = {outcome.get("status") for outcome in outcomes}
    if "COMPLETED" in statuses:
        return "COMPLETED"
    return "HELD"


def _reconcile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = held = rejected = unclassified = 0
    for row in rows:
        status = row.get("row_status")
        if status == "COMPLETED": completed += 1
        elif status == "HELD": held += 1
        elif status == "REJECTED": rejected += 1
        else: unclassified += 1
    rows_in = len(rows)
    return {"rows_in": rows_in, "rows_completed": completed, "rows_held": held, "rows_rejected": rejected, "rows_unclassified": unclassified, "balanced": unclassified == 0 and completed + held + rejected == rows_in}


def _lane_reconciliation(rows: list[dict[str, Any]], lane: str, blockers: list[str] | None = None, acquisition: dict[str, Any] | None = None) -> dict[str, Any]:
    blockers = blockers or []
    lane_rows = [row for row in rows if row.get("lane") == lane]
    discovered = len(lane_rows)
    zero_row_reason = None
    if discovered == 0:
        expected = "PROP_SNAPSHOT_QUERY_FAILED" if lane == "PROPS" else "TEAM_EVENT_SNAPSHOT_QUERY_FAILED"
        if expected in " ".join(blockers): zero_row_reason = "DISCOVERY_DATA_UNOBTAINABLE"
        elif lane == "PROPS" and acquisition and acquisition.get("status") == "DATA_UNOBTAINABLE": zero_row_reason = "DISCOVERY_DATA_UNOBTAINABLE"
        else: zero_row_reason = "NO_CANONICAL_CANDIDATES"
    return {
        "discovered_count": discovered,
        "canonicalized_count": discovered,
        "scored_count": discovered,
        "completed_count": sum(1 for row in lane_rows if row.get("row_status") == "COMPLETED"),
        "held_count": sum(1 for row in lane_rows if row.get("row_status") == "HELD"),
        "rejected_count": sum(1 for row in lane_rows if row.get("row_status") == "REJECTED"),
        "zero_row_reason": zero_row_reason,
        "acquisition": acquisition if lane == "PROPS" else None,
    }


def _future(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.utcoffset() is not None and parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _prop_rows(db: Any, requested_date: str, requested_timezone: str, limit: int) -> list[dict[str, Any]]:
    rows = db.table("wow_prop_evidence_snapshots").select("source_snapshot_id,event_id,event_start_time,sport,player,stat_type,line,hydration_status,blockers").eq("hydration_status", "PASS").order("event_start_time").limit(max(limit * 4, limit)).execute().data or []
    try: zone = ZoneInfo(requested_timezone)
    except Exception: return []
    def matching(row: dict[str, Any]) -> bool:
        try: return datetime.fromisoformat(str(row.get("event_start_time")).replace("Z", "+00:00")).astimezone(zone).date().isoformat() == requested_date
        except (TypeError, ValueError): return False
    return [dict(row) for row in rows if not row.get("blockers") and _future(row.get("event_start_time")) and matching(row)][:limit]


def _team_rows(db: Any, requested_date: str, limit: int) -> list[dict[str, Any]]:
    rows = db.table("wow_mlb_forward_shadow_events").select("official_event_id,official_date,event_start_time,home_team,away_team,venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id,snapshot_timestamp,feature_hydration_status").eq("official_date", requested_date).eq("feature_hydration_status", "PASS").order("event_start_time").limit(limit).execute().data or []
    return [dict(row) for row in rows if _future(row.get("event_start_time"))]


def run_daily_snapshot(req: DailySnapshotRequest, *, db: Any, market_api: Any, event_api: Any) -> dict[str, Any]:
    try: date.fromisoformat(req.requested_slate_date)
    except ValueError:
        return {"run_id": f"v17-daily-{uuid4()}", "terminal": True, "run_status": "RUN_INVALID_REQUEST", "rows": [], "blockers": ["REQUESTED_SLATE_DATE_INVALID"], "can_execute": False}

    run_id = f"v17-daily-{uuid4()}"
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    requested_lanes = set(req.lanes)
    prop_acquisition: dict[str, Any] | None = None

    if "PROPS" in requested_lanes:
        try:
            prop_rows = _prop_rows(db, req.requested_slate_date, req.requested_timezone, req.max_props)
        except Exception as exc:
            prop_rows = []
            blockers.append(f"PROP_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")
        if not prop_rows and not any(str(x).startswith("PROP_SNAPSHOT_QUERY_FAILED") for x in blockers):
            prop_acquisition = acquire_daily_prop_snapshots(db=db, requested_date=req.requested_slate_date, requested_timezone=req.requested_timezone, max_candidates=max(req.max_props * 3, req.max_props))
            if prop_acquisition.get("status") == "DATA_UNOBTAINABLE": blockers.extend(prop_acquisition.get("blockers") or [])
            try: prop_rows = _prop_rows(db, req.requested_slate_date, req.requested_timezone, req.max_props)
            except Exception as exc:
                prop_rows = []
                blockers.append(f"PROP_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")
        for row in prop_rows:
            identity = {key: row.get(key) for key in ("event_id", "event_start_time", "sport", "player", "stat_type", "line", "source_snapshot_id")}
            outcomes: list[dict[str, Any]] = []
            for direction in ("MORE", "LESS"):
                try:
                    scored = market_api.score_prop(market_api.ScorePropRequest(**{**identity, "direction": direction}), "WOW_BETTING_ENGINE")
                    status = "COMPLETED" if scored.get("probability_publishable") is True and scored.get("rank_eligible") is True else "HELD"
                    outcomes.append({"direction": direction, "status": status, "payload": scored})
                except HTTPException as exc: outcomes.append({"direction": direction, "status": "HELD", "payload": _detail(exc)})
                except Exception as exc: outcomes.append({"direction": direction, "status": "HELD", "payload": {"code": "PROP_SCORER_EXCEPTION", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}})
            publishable = any(x["payload"].get("probability_publishable") is True and x["payload"].get("rank_eligible") is True for x in outcomes)
            rows.append(_terminal_row("PROPS", identity, {"outcomes": outcomes, "probability_publishable": publishable, "rank_eligible": publishable, "can_execute": False}, _props_row_status(outcomes)))

    if "MONEYLINE" in requested_lanes:
        try: event_rows = _team_rows(db, req.requested_slate_date, req.max_team_events)
        except Exception as exc:
            event_rows = []
            blockers.append(f"TEAM_EVENT_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")
        for event in event_rows:
            identity = {key: event.get(key) for key in ("official_event_id", "event_start_time", "home_team", "away_team", "snapshot_id")}
            request = TeamEventRequest(requester_host_identity="WOW_BETTING_ENGINE", research_run_id=run_id, requested_slate_date=req.requested_slate_date, requested_timezone=req.requested_timezone, candidate_family="OUTRIGHT_WINNER", decision_intent="BEST_SIDE", event_key=f"MLB:{event['official_event_id']}", official_event_id=str(event["official_event_id"]), event_start_time_utc=event["event_start_time"], sport="MLB", league="MLB", settlement_basis="FULL_GAME_INCLUDING_EXTRA_INNINGS", home_team=event["home_team"], away_team=event["away_team"], source_snapshot_id=str(event["snapshot_id"]), latest_material_update_timestamp=event.get("snapshot_timestamp"), sport_specific_evidence={"venue": event.get("venue_name"), "home_starting_pitcher": event.get("home_probable_pitcher"), "away_starting_pitcher": event.get("away_probable_pitcher"), "home_starter_status": "PROBABLE", "away_starter_status": "PROBABLE", "home_lineup_status": "PROJECTED", "away_lineup_status": "PROJECTED"})
            try: result = score_team_event_request(request, event_api=event_api, canonical_hydration_required=True)
            except HTTPException as exc: result = _detail(exc)
            except Exception as exc: result = {"code": "TEAM_EVENT_SCORER_EXCEPTION", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}
            row_status = "COMPLETED" if result.get("probability_publishable") is True and result.get("rank_eligible") is True else "HELD"
            rows.append(_terminal_row("MONEYLINE", identity, result, row_status))

    if not rows and not blockers: blockers.append("NO_CANONICAL_PREGAME_SNAPSHOTS")
    lane_reconciliation = {lane: _lane_reconciliation(rows, lane, blockers, prop_acquisition) for lane in requested_lanes}
    return {"run_id": run_id, "terminal": True, "run_status": "COMPLETED" if not blockers else "COMPLETED_WITH_ACQUISITION_BLOCKERS", "requested_slate_date": req.requested_slate_date, "requested_timezone": req.requested_timezone, "requested_lanes": sorted(requested_lanes), "rows": rows, "reconciliation": _reconcile(rows), "lane_reconciliation": lane_reconciliation, "prop_acquisition": prop_acquisition, "blockers": list(dict.fromkeys(blockers)), "can_execute": False}


def install_daily_snapshot_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any, market_api: Any, event_api: Any) -> None:
    install_prop_forward_cohort_route(app, auth_dependency=auth_dependency, db_client_fn=db_client_fn, market_api=market_api)
    if any(getattr(route, "path", None) == "/v17/daily-snapshot-run" for route in app.router.routes): return
    @app.post("/v17/daily-snapshot-run", dependencies=[auth_dependency], operation_id="runWowV17DailySnapshot")
    def daily_snapshot_run(req: DailySnapshotRequest): return run_daily_snapshot(req, db=db_client_fn(), market_api=market_api, event_api=event_api)
