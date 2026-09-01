"""Objective-aware dispatcher for governed team/event probability requests."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal, Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

ObjectiveLane = Literal["OUTRIGHT_WIN_PROBABILITY", "UPSET_PROBABILITY", "MARKET_EDGE"]


class TeamEventRequestRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research_run_id: str
    objective_lane: ObjectiveLane
    sport: str
    league: str
    event_key: str
    event_state: Literal["PREGAME"]
    event_date: str
    timezone: str
    price_required_for_objective: bool


class TeamEventRequestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[TeamEventRequestRow] = Field(min_length=1, max_length=100)


def _held(row: TeamEventRequestRow, code: str, blocker: str, detail: Any = None) -> dict[str, Any]:
    return {
        "research_run_id": row.research_run_id, "event_key": row.event_key,
        "objective_lane": row.objective_lane, "terminal_status": "HELD", "code": code,
        "calibrated_probability": None, "calibrated_lower_bound": None,
        "calibrated_upper_bound": None, "audit_result": "BLOCKED",
        "event_decision": code, "blockers": [blocker], "internal_ceiling": code,
        "detail": detail, "probability_publishable": False, "can_execute": False,
    }


def _event_id(row: TeamEventRequestRow) -> str:
    prefix = f"{row.sport.strip().upper()}:"
    return row.event_key[len(prefix):] if row.event_key.upper().startswith(prefix) else row.event_key


def _hydrate(db: Any, row: TeamEventRequestRow) -> Optional[dict[str, Any]]:
    result = (
        db.table("wow_mlb_forward_shadow_events")
        .select("official_event_id,official_date,event_start_time,home_team,away_team,"
                "venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id,"
                "snapshot_timestamp,feature_hydration_status")
        .eq("official_event_id", _event_id(row)).eq("official_date", row.event_date)
        .order("snapshot_timestamp", desc=True).limit(1).execute()
    )
    return (result.data or [None])[0]


def _score_request(row: TeamEventRequestRow, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "research_run_id": row.research_run_id, "requested_slate_date": row.event_date,
        "requested_timezone": row.timezone, "scan_stage": row.event_state,
        "event_key": row.event_key, "official_event_id": str(event["official_event_id"]),
        "event_start_time_utc": event["event_start_time"], "sport": "MLB", "league": "MLB",
        "market_family": "OUTRIGHT_WINNER",
        "settlement_basis": "FULL_GAME_INCLUDING_EXTRA_INNINGS",
        "home_team": event["home_team"], "away_team": event["away_team"],
        "venue": event["venue_name"], "home_starting_pitcher": event["home_probable_pitcher"],
        "away_starting_pitcher": event["away_probable_pitcher"],
        "home_starter_status": "PROBABLE", "away_starter_status": "PROBABLE",
        "home_lineup_status": "PROJECTED", "away_lineup_status": "PROJECTED",
        "latest_material_update_timestamp": event.get("snapshot_timestamp"),
        "source_snapshot_id": event["snapshot_id"],
    }


def _completed(row: TeamEventRequestRow, event: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    keys = ("calibrated_home_probability", "calibrated_away_probability",
            "calibrated_home_lower_bound", "calibrated_away_lower_bound",
            "calibrated_home_upper_bound", "calibrated_away_upper_bound")
    if not all(isinstance(scored.get(k), (int, float)) and not isinstance(scored.get(k), bool) for k in keys):
        return _held(row, "PROVIDER_UNAVAILABLE", "EVENT_SCORER_INVALID_RESPONSE")
    home = float(scored["calibrated_home_lower_bound"]) >= float(scored["calibrated_away_lower_bound"])
    side = "home" if home else "away"
    market_needed = row.price_required_for_objective or row.objective_lane != "OUTRIGHT_WIN_PROBABILITY"
    blockers = ["MARKET_DATA_UNOBTAINABLE"] if market_needed else []
    decision = "MANUAL_QUALIFIED_WINNER"
    if row.objective_lane == "UPSET_PROBABILITY":
        decision = "INPUT_INCOMPLETE"
    elif row.objective_lane == "MARKET_EDGE":
        decision = "MARKET_DATA_UNOBTAINABLE"
    return {
        "research_run_id": row.research_run_id, "event_key": row.event_key,
        "objective_lane": row.objective_lane, "terminal_status": "COMPLETED",
        "code": "SPORTING_PROBABILITY_COMPLETED",
        "selected_team": event[f"{side}_team"],
        "calibrated_probability": float(scored[f"calibrated_{side}_probability"]),
        "calibrated_lower_bound": float(scored[f"calibrated_{side}_lower_bound"]),
        "calibrated_upper_bound": float(scored[f"calibrated_{side}_upper_bound"]),
        "audit_result": "PARTIAL" if blockers else "PASS", "event_decision": decision,
        "blockers": blockers,
        "internal_ceiling": "SPORTING_PROBABILITY_ONLY" if blockers else "FULL_MODEL_PROBABILITY",
        "probability_publishable": bool(scored.get("probability_publishable")),
        "can_execute": False,
    }


def install_team_event_request_routes(app: Any, *, auth_dependency: Any, db_client_fn: Any, event_api: Any) -> None:
    if any(getattr(r, "path", None) == "/score-team-event-request" for r in app.router.routes):
        return

    @app.post("/score-team-event-request", dependencies=[auth_dependency],
              operation_id="scoreWowTeamEventRequest")
    def score_team_event_request(
        batch: TeamEventRequestBatch,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        outcomes: list[dict[str, Any]] = []
        for row in batch.rows:
            try:
                date.fromisoformat(row.event_date)
            except ValueError:
                outcomes.append(_held(row, "INPUT_INCOMPLETE", "EVENT_DATE_INVALID")); continue
            if row.sport.strip().upper() != "MLB" or row.league.strip().upper() != "MLB":
                outcomes.append(_held(row, "MODEL_UNAVAILABLE", "SPORT_SPECIFIC_MODEL_UNAVAILABLE")); continue
            try:
                event = _hydrate(db_client_fn(), row)
            except Exception as exc:
                outcomes.append(_held(row, "PROVIDER_UNAVAILABLE", "EVENT_EVIDENCE_PROVIDER_UNAVAILABLE",
                                      {"error_type": type(exc).__name__})); continue
            if not event or event.get("feature_hydration_status") != "PASS":
                outcomes.append(_held(row, "INPUT_INCOMPLETE", "EVENT_EVIDENCE_INCOMPLETE")); continue
            try:
                req = event_api.ScoreEventRequest(**_score_request(row, event))
                scored = event_api.score_event(req)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                raw_code = str(detail.get("code") or "PROVIDER_UNAVAILABLE")
                code = "MODEL_UNAVAILABLE" if "MODEL" in raw_code else "PROVIDER_UNAVAILABLE"
                outcomes.append(_held(row, code, raw_code, detail)); continue
            except Exception as exc:
                outcomes.append(_held(row, "TRANSPORT_FAILURE", "ROW_SCORER_FAILURE",
                                      {"error_type": type(exc).__name__})); continue
            outcomes.append(_completed(row, event, scored))

        count = len(batch.rows)
        completed = sum(x["terminal_status"] == "COMPLETED" for x in outcomes)
        if len(outcomes) != count:
            raise HTTPException(status_code=500, detail={"code": "RECONCILIATION_FAILURE", "can_execute": False})
        return {
            "ok": completed > 0,
            "run_status": "COMPLETE" if completed == count else ("RUN_PARTIAL" if completed else "BLOCKED"),
            "rows_in": count, "rows_completed": completed, "rows_held": count - completed,
            "reconciliation_pass": True, "rows": outcomes, "can_execute": False,
        }
