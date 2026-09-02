"""Bounded, server-owned V17 Daily snapshot runner.

This route deliberately does not ask the Custom GPT to discover a slate or to
schedule a sequence of row-level Action calls.  It reads only canonical,
already-hydrated pregame records, applies the existing controlled prop and
team-event ingress to a bounded number of rows, and returns a terminal receipt
for every selected row.  It never creates a probability or an executable bet.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from v17.prop_forward_cohort_runtime import install_prop_forward_cohort_route
from v17.team_event_request_runtime import TeamEventRequest, score_team_event_request


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
    return {
        "lane": lane,
        "identity": identity,
        "result": payload,
        "terminal": True,
        "row_status": row_status,
        "probability_publishable": bool(payload.get("probability_publishable", False)),
        "can_execute": False,
    }


# Row-status classification is deliberately narrow and reuses only what the
# scoring calls in this file already tell us -- it does not infer new
# semantics from a terminal_label/code taxonomy that isn't already
# unambiguous on this path. PROPS outcomes are already tagged COMPLETED/HELD
# by the score_prop call sites below; no REJECTED path is currently reachable
# from either lane (score_prop and score_team_event_request never raise a
# hard model-rejection here, only capability/acquisition/contract holds), so
# rows_rejected is honestly 0 until a real rejection path exists on this
# route -- it is never fabricated to look non-zero.
def _props_row_status(outcomes: list[dict[str, Any]]) -> str:
    statuses = {outcome.get("status") for outcome in outcomes}
    if "COMPLETED" in statuses:
        return "COMPLETED"
    if "HELD" in statuses:
        return "HELD"
    return "UNCLASSIFIED"


def _reconcile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = held = rejected = unclassified = 0
    for row in rows:
        status = row.get("row_status")
        if status == "COMPLETED":
            completed += 1
        elif status == "HELD":
            held += 1
        elif status == "REJECTED":
            rejected += 1
        else:
            unclassified += 1
    rows_in = len(rows)
    balanced = unclassified == 0 and (completed + held + rejected == rows_in)
    return {
        "rows_in": rows_in,
        "rows_completed": completed,
        "rows_held": held,
        "rows_rejected": rejected,
        "rows_unclassified": unclassified,
        "balanced": balanced,
    }


def _future(value: Any) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.utcoffset() is not None and parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _prop_rows(db: Any, requested_date: str, requested_timezone: str, limit: int) -> list[dict[str, Any]]:
    rows = (
        db.table("wow_prop_evidence_snapshots")
        .select("source_snapshot_id,event_id,event_start_time,sport,player,stat_type,line,hydration_status,blockers")
        .eq("hydration_status", "PASS")
        .order("event_start_time")
        .limit(max(limit * 4, limit))
        .execute()
        .data
        or []
    )
    try:
        zone = ZoneInfo(requested_timezone)
    except Exception:
        return []
    def matching_local_date(row: dict[str, Any]) -> bool:
        try:
            started = datetime.fromisoformat(str(row.get("event_start_time")).replace("Z", "+00:00"))
            return started.astimezone(zone).date().isoformat() == requested_date
        except (TypeError, ValueError):
            return False
    return [dict(row) for row in rows if not row.get("blockers") and _future(row.get("event_start_time")) and matching_local_date(row)][:limit]


def _team_rows(db: Any, requested_date: str, limit: int) -> list[dict[str, Any]]:
    rows = (
        db.table("wow_mlb_forward_shadow_events")
        .select("official_event_id,official_date,event_start_time,home_team,away_team,venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id,snapshot_timestamp,feature_hydration_status")
        .eq("official_date", requested_date)
        .eq("feature_hydration_status", "PASS")
        .order("event_start_time")
        .limit(limit)
        .execute()
        .data
        or []
    )
    return [dict(row) for row in rows if _future(row.get("event_start_time"))]


def run_daily_snapshot(
    req: DailySnapshotRequest,
    *,
    db: Any,
    market_api: Any,
    event_api: Any,
) -> dict[str, Any]:
    """Run bounded Daily rows and return a complete, non-executable receipt."""
    try:
        date.fromisoformat(req.requested_slate_date)
    except ValueError:
        return {
            "run_id": f"v17-daily-{uuid4()}", "terminal": True,
            "run_status": "RUN_INVALID_REQUEST", "rows": [],
            "blockers": ["REQUESTED_SLATE_DATE_INVALID"], "can_execute": False,
        }

    run_id = f"v17-daily-{uuid4()}"
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    requested_lanes = set(req.lanes)

    if "PROPS" in requested_lanes:
        try:
            prop_rows = _prop_rows(db, req.requested_slate_date, req.requested_timezone, req.max_props)
        except Exception as exc:
            prop_rows = []
            blockers.append(f"PROP_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")
        for row in prop_rows:
            identity = {key: row.get(key) for key in ("event_id", "event_start_time", "sport", "player", "stat_type", "line", "source_snapshot_id")}
            outcomes: list[dict[str, Any]] = []
            for direction in ("MORE", "LESS"):
                try:
                    scored = market_api.score_prop(
                        market_api.ScorePropRequest(**{**identity, "direction": direction}),
                        "WOW_BETTING_ENGINE",
                    )
                    # Not every score_prop implementation is symmetric (raise
                    # on every non-publishable state, return normally only
                    # when publishable). The lane-separation variant
                    # (score_prop_lane_separated -> _raw_specialist_research,
                    # calibration_publication_api.py:292-297 /
                    # api_lane_separated.py) returns normally with
                    # probability_publishable=False, governed_publishable=
                    # False, research_only=True whenever publication is
                    # blocked but raw specialist research is still
                    # permitted. A normal return must therefore be
                    # classified the same way as an exception: COMPLETED
                    # only when the payload itself claims
                    # probability_publishable is True.
                    status = "COMPLETED" if scored.get("probability_publishable") is True else "HELD"
                    outcomes.append({"direction": direction, "status": status, "payload": scored})
                except HTTPException as exc:
                    outcomes.append({"direction": direction, "status": "HELD", "payload": _detail(exc)})
                except Exception as exc:
                    outcomes.append({"direction": direction, "status": "HELD", "payload": {"code": "PROP_SCORER_EXCEPTION", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}})
            rows.append(_terminal_row(
                "PROPS", identity,
                {"outcomes": outcomes, "probability_publishable": any(bool(x["payload"].get("probability_publishable")) for x in outcomes), "can_execute": False},
                _props_row_status(outcomes),
            ))

    if "MONEYLINE" in requested_lanes:
        try:
            event_rows = _team_rows(db, req.requested_slate_date, req.max_team_events)
        except Exception as exc:
            event_rows = []
            blockers.append(f"TEAM_EVENT_SNAPSHOT_QUERY_FAILED:{type(exc).__name__}")
        for event in event_rows:
            identity = {key: event.get(key) for key in ("official_event_id", "event_start_time", "home_team", "away_team", "snapshot_id")}
            request = TeamEventRequest(
                requester_host_identity="WOW_BETTING_ENGINE", research_run_id=run_id,
                requested_slate_date=req.requested_slate_date, requested_timezone=req.requested_timezone,
                candidate_family="OUTRIGHT_WINNER", decision_intent="BEST_SIDE",
                event_key=f"MLB:{event['official_event_id']}", official_event_id=str(event["official_event_id"]),
                event_start_time_utc=event["event_start_time"], sport="MLB", league="MLB",
                settlement_basis="FULL_GAME_INCLUDING_EXTRA_INNINGS", home_team=event["home_team"], away_team=event["away_team"],
                source_snapshot_id=str(event["snapshot_id"]), latest_material_update_timestamp=event.get("snapshot_timestamp"),
                sport_specific_evidence={"venue": event.get("venue_name"), "home_starting_pitcher": event.get("home_probable_pitcher"), "away_starting_pitcher": event.get("away_probable_pitcher"), "home_starter_status": "PROBABLE", "away_starter_status": "PROBABLE", "home_lineup_status": "PROJECTED", "away_lineup_status": "PROJECTED"},
            )
            try:
                result = score_team_event_request(request, event_api=event_api)
            except HTTPException as exc:
                result = _detail(exc)
            except Exception as exc:
                result = {"code": "TEAM_EVENT_SCORER_EXCEPTION", "error_type": type(exc).__name__, "probability_publishable": False, "can_execute": False}
            # Unlike PROPS' score_prop (which only ever returns normally when
            # genuinely publishable, and raises for every held/blocked state),
            # score_team_event_request's success path can itself return
            # normally -- no exception -- while still representing a hold: the
            # LLP governance bridge (_run_mlb_llp_governance) returns a
            # MODEL_QUALIFIED_HOLD/probability_publishable=False package via
            # _llp_governance_hold whenever the bridge isn't proven, which is
            # every result today. Exception-vs-no-exception is therefore not a
            # valid completed/held signal for this lane. probability_publishable
            # is: it is explicitly set on every reachable return from this
            # function, exception or not (verified against every raise site
            # and every _run_mlb_llp_governance/_llp_governance_hold branch).
            row_status = "COMPLETED" if result.get("probability_publishable") is True else "HELD"
            rows.append(_terminal_row("MONEYLINE", identity, result, row_status))

    if not rows and not blockers:
        blockers.append("NO_CANONICAL_PREGAME_SNAPSHOTS")
    return {
        "run_id": run_id, "terminal": True,
        "run_status": "COMPLETED" if not blockers else "COMPLETED_WITH_ACQUISITION_BLOCKERS",
        "requested_slate_date": req.requested_slate_date, "requested_timezone": req.requested_timezone,
        "requested_lanes": sorted(requested_lanes), "rows": rows,
        "reconciliation": _reconcile(rows),
        "blockers": blockers, "can_execute": False,
    }


def install_daily_snapshot_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Any, market_api: Any, event_api: Any) -> None:
    install_prop_forward_cohort_route(
        app,
        auth_dependency=auth_dependency,
        db_client_fn=db_client_fn,
        market_api=market_api,
    )

    if any(getattr(route, "path", None) == "/v17/daily-snapshot-run" for route in app.router.routes):
        return

    @app.post("/v17/daily-snapshot-run", dependencies=[auth_dependency], operation_id="runWowV17DailySnapshot")
    def daily_snapshot_run(req: DailySnapshotRequest):
        return run_daily_snapshot(req, db=db_client_fn(), market_api=market_api, event_api=event_api)
