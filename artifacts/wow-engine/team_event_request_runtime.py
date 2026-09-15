"""Objective-aware dispatcher for governed team/event probability requests."""
from __future__ import annotations

from datetime import date, datetime, timezone
import os
from typing import Any, Literal, Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from github_actions_oidc import scout_route_auth_dependency
from nfl_event_hydration_runtime import install_nfl_hydration_startup
from nfl_event_model_startup import install_nfl_model_startup
from v17 import team_event_request_runtime as v17_team_event_base
from v17.basketball_model_maintenance import install_basketball_model_maintenance_route
from v17.ncaaf_model_maintenance import install_ncaaf_model_maintenance_route
from v17.nfl_forward_shadow import run_forward_shadow
from v17.nfl_team_event_publication import install_nfl_team_event_publication
from v17.team_event_probability_preservation import score_team_event_request as score_v17_team_event_request

ObjectiveLane = Literal["OUTRIGHT_WIN_PROBABILITY", "UPSET_PROBABILITY", "MARKET_EDGE"]
CANONICAL_TIME_TOLERANCE_MINUTES = 30.0


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
    event_start_time_utc: str | None = None
    home_team: str | None = None
    away_team: str | None = None


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


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _latest_pass(query: Any) -> Optional[dict[str, Any]]:
    result = query.eq("feature_hydration_status", "PASS").order("snapshot_timestamp", desc=True).limit(64).execute()
    rows = result.data or []
    if not rows:
        return None
    event_ids = {str(item.get("official_event_id") or "") for item in rows}
    return rows[0] if len(event_ids) == 1 else None


def _hydrate(db: Any, row: TeamEventRequestRow) -> Optional[dict[str, Any]]:
    fields = ("official_event_id,official_date,event_start_time,event_status,home_team,away_team,"
              "venue_name,home_probable_pitcher,away_probable_pitcher,snapshot_id,"
              "snapshot_timestamp,feature_hydration_status")
    table = db.table("wow_mlb_forward_shadow_events")
    direct = _latest_pass(
        table.select(fields).eq("official_event_id", _event_id(row)).eq("official_date", row.event_date)
    )
    if direct:
        return direct
    if not row.home_team or not row.away_team or not row.event_start_time_utc:
        return None
    canonical = _latest_pass(
        table.select(fields)
        .eq("official_date", row.event_date)
        .eq("home_team", row.home_team)
        .eq("away_team", row.away_team)
    )
    if canonical is None:
        return None
    requested_start = _aware(row.event_start_time_utc)
    canonical_start = _aware(canonical.get("event_start_time"))
    if requested_start is None or canonical_start is None:
        return None
    delta_minutes = abs((requested_start - canonical_start).total_seconds()) / 60.0
    if delta_minutes > CANONICAL_TIME_TOLERANCE_MINUTES:
        return None
    return canonical


def _score_request(row: TeamEventRequestRow, event: dict[str, Any]) -> dict[str, Any]:
    """Legacy V16 request payload retained for WOW_V17_ACTIVE=0 rollback."""
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


def _mlb_v17_score_request(row: TeamEventRequestRow, event: dict[str, Any]) -> Any:
    return v17_team_event_base.TeamEventRequest(
        requester_host_identity="WOW_BETTING_ENGINE",
        research_run_id=row.research_run_id,
        requested_slate_date=row.event_date,
        requested_timezone=row.timezone,
        scan_stage=row.event_state,
        candidate_family="TEAM_EVENT",
        decision_intent="UPSET" if row.objective_lane == "UPSET_PROBABILITY" else "WINNER",
        event_key=row.event_key,
        official_event_id=str(event["official_event_id"]),
        event_start_time_utc=str(event["event_start_time"]),
        sport="MLB",
        league="MLB",
        market_family="OUTRIGHT_WINNER",
        settlement_basis="FULL_GAME_INCLUDING_EXTRA_INNINGS",
        home_team=str(event["home_team"]),
        away_team=str(event["away_team"]),
        source_snapshot_id=str(event["snapshot_id"]),
        latest_material_update_timestamp=str(event.get("snapshot_timestamp") or "") or None,
        market_prior=None,
        sport_specific_evidence={
            "venue": event["venue_name"],
            "official_event_status": event.get("event_status"),
            "home_starting_pitcher": event["home_probable_pitcher"],
            "away_starting_pitcher": event["away_probable_pitcher"],
            "home_starter_status": "PROBABLE",
            "away_starter_status": "PROBABLE",
            "home_lineup_status": "PROJECTED",
            "away_lineup_status": "PROJECTED",
        },
    )


def _nfl_score_request(row: TeamEventRequestRow) -> Any:
    if not row.event_start_time_utc or not row.home_team or not row.away_team:
        raise ValueError("NFL_SCOUT_EVENT_IDENTITY_INCOMPLETE")
    return v17_team_event_base.TeamEventRequest(
        requester_host_identity="WOW_BETTING_ENGINE",
        research_run_id=row.research_run_id,
        requested_slate_date=row.event_date,
        requested_timezone=row.timezone,
        scan_stage=row.event_state,
        candidate_family="TEAM_EVENT",
        decision_intent="UPSET" if row.objective_lane == "UPSET_PROBABILITY" else "WINNER",
        event_key=row.event_key,
        official_event_id=_event_id(row),
        event_start_time_utc=row.event_start_time_utc,
        sport="NFL",
        league="NFL",
        market_family="OUTRIGHT_WINNER",
        settlement_basis="FULL_GAME_OUTRIGHT",
        home_team=row.home_team,
        away_team=row.away_team,
        source_snapshot_id="SCOUT_CANONICALIZATION_PENDING",
        latest_material_update_timestamp=None,
        market_prior=None,
        sport_specific_evidence={},
    )


def _typed_mlb_failure(detail: dict[str, Any]) -> tuple[str, str]:
    raw_code = str(detail.get("code") or "PROVIDER_UNAVAILABLE")
    blocker = str(detail.get("blocker_code") or raw_code)
    if raw_code in {
        "MODEL_INPUTS_INSUFFICIENT",
        "MODEL_SCORER_FAILED",
        "MODEL_OUTPUT_INVALID",
        "MODEL_UNAVAILABLE",
        "INPUT_INCOMPLETE",
    }:
        return raw_code, blocker
    if raw_code == "RUN_INVALID_ACQUISITION_INCOMPLETE":
        return "INPUT_INCOMPLETE", blocker
    if raw_code.startswith("MODEL_"):
        return raw_code, blocker
    if "SCORER" in raw_code or "OUTPUT" in raw_code:
        return "MODEL_OUTPUT_INVALID", blocker
    return "PROVIDER_UNAVAILABLE", blocker


def _completed(row: TeamEventRequestRow, event: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    keys = ("calibrated_home_probability", "calibrated_away_probability",
            "calibrated_home_lower_bound", "calibrated_away_lower_bound",
            "calibrated_home_upper_bound", "calibrated_away_upper_bound")
    if not all(isinstance(scored.get(k), (int, float)) and not isinstance(scored.get(k), bool) for k in keys):
        code = str(scored.get("code") or "MODEL_OUTPUT_INVALID")
        blockers = [str(value) for value in (scored.get("blockers") or [])]
        blocker = blockers[0] if blockers else "GOVERNED_PROBABILITY_FIELDS_INVALID"
        if code == "REAL_FITTED_MODEL_PATH_PROVEN":
            code = "MODEL_INPUTS_INSUFFICIENT"
        return _held(row, code, blocker, scored)
    home = float(scored["calibrated_home_lower_bound"]) >= float(scored["calibrated_away_lower_bound"])
    side = "home" if home else "away"
    market_needed = row.price_required_for_objective or row.objective_lane != "OUTRIGHT_WIN_PROBABILITY"
    inherited_blockers = [str(value) for value in (scored.get("blockers") or [])]
    blockers = list(dict.fromkeys([
        *inherited_blockers,
        *(["MARKET_DATA_UNOBTAINABLE"] if market_needed else []),
    ]))
    decision = str(scored.get("llp_event_decision") or "MANUAL_QUALIFIED_WINNER")
    if row.objective_lane == "UPSET_PROBABILITY" and not scored.get("llp_event_decision"):
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
        "internal_ceiling": scored.get("terminal_label") or (
            "SPORTING_PROBABILITY_ONLY" if blockers else "FULL_MODEL_PROBABILITY"
        ),
        "governed_publication_code": scored.get("code"),
        "terminal_label": scored.get("terminal_label"),
        "score_snapshot_id": scored.get("score_snapshot_id") or scored.get("base_score_snapshot_id"),
        "event_prediction_id": scored.get("event_prediction_id"),
        "probability_publishable": bool(scored.get("probability_publishable")),
        "can_execute": False,
    }


def _completed_nfl(row: TeamEventRequestRow, scored: dict[str, Any]) -> dict[str, Any]:
    if scored.get("probability_publishable") is not True or scored.get("terminal_label") != "FINAL_APPROVED":
        blockers = [str(value) for value in (scored.get("blockers") or [])]
        blocker = blockers[0] if blockers else str(scored.get("code") or "NFL_GOVERNED_PUBLICATION_NOT_PROVEN")
        return _held(row, str(scored.get("code") or "NFL_GOVERNED_PUBLICATION_NOT_PROVEN"), blocker, scored)

    selected = str(scored.get("selected_participant") or "").strip()
    home = str(row.home_team or "").strip()
    away = str(row.away_team or "").strip()
    if selected.casefold() == home.casefold():
        side = "home"
    elif selected.casefold() == away.casefold():
        side = "away"
    else:
        return _held(row, "MODEL_OUTPUT_INVALID", "NFL_SELECTED_PARTICIPANT_IDENTITY_INVALID", scored)

    probability = scored.get("calibrated_selection_probability")
    lower = scored.get("rank_calibrated_lower_bound", scored.get("ranked_probability"))
    upper = scored.get(f"calibrated_{side}_upper_bound")
    values = (probability, lower, upper)
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        return _held(row, "MODEL_OUTPUT_INVALID", "NFL_GOVERNED_PROBABILITY_FIELDS_INVALID", scored)

    market_needed = row.price_required_for_objective or row.objective_lane != "OUTRIGHT_WIN_PROBABILITY"
    blockers = ["MARKET_DATA_UNOBTAINABLE"] if market_needed else []
    return {
        "research_run_id": row.research_run_id,
        "event_key": row.event_key,
        "objective_lane": row.objective_lane,
        "terminal_status": "COMPLETED",
        "code": "SPORTING_PROBABILITY_COMPLETED",
        "selected_team": selected,
        "calibrated_probability": float(probability),
        "calibrated_lower_bound": float(lower),
        "calibrated_upper_bound": float(upper),
        "audit_result": "PARTIAL" if blockers else "PASS",
        "event_decision": scored.get("llp_event_decision") or "FINAL_APPROVED",
        "blockers": blockers,
        "internal_ceiling": "SPORTING_PROBABILITY_ONLY" if blockers else "FULL_MODEL_PROBABILITY",
        "governed_publication_code": scored.get("code"),
        "terminal_label": scored.get("terminal_label"),
        "score_snapshot_id": scored.get("score_snapshot_id"),
        "event_prediction_id": scored.get("event_prediction_id"),
        "probability_publishable": True,
        "can_execute": False,
    }


def install_team_event_request_routes(app: Any, *, auth_dependency: Any, db_client_fn: Any, event_api: Any) -> None:
    install_nfl_hydration_startup(app, db_client_fn=db_client_fn)
    install_nfl_model_startup(app, db_client_fn=db_client_fn)

    if os.getenv("WOW_V17_ACTIVE", "0") == "1":
        install_nfl_team_event_publication(v17_team_event_base)
        install_basketball_model_maintenance_route(
            app,
            auth_dependency=auth_dependency,
            db_client_fn=db_client_fn,
        )
        install_ncaaf_model_maintenance_route(
            app,
            auth_dependency=auth_dependency,
            db_client_fn=db_client_fn,
        )
        if not any(getattr(r, "path", None) == "/internal/v17/nfl-forward-shadow" for r in app.router.routes):
            @app.post(
                "/internal/v17/nfl-forward-shadow",
                dependencies=[scout_route_auth_dependency(auth_dependency)],
                operation_id="runWowV17NflForwardShadow",
            )
            def run_nfl_forward_shadow():
                try:
                    return run_forward_shadow(db_client_fn())
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "code": "NFL_FORWARD_SHADOW_RUN_FAILED",
                            "error_type": type(exc).__name__,
                            "automatic_certification": False,
                            "automatic_promotion": False,
                            "probability_publishable": False,
                            "can_execute": False,
                        },
                    ) from exc

    if any(getattr(r, "path", None) == "/score-team-event-request" for r in app.router.routes):
        return

    @app.post("/score-team-event-request", dependencies=[scout_route_auth_dependency(auth_dependency)],
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

            sport = row.sport.strip().upper()
            league = row.league.strip().upper()
            if sport == "NFL" and league == "NFL":
                if not row.event_start_time_utc or not row.home_team or not row.away_team:
                    outcomes.append(_held(row, "MODEL_INPUTS_INSUFFICIENT", "NFL_SCOUT_EVENT_IDENTITY_INCOMPLETE")); continue
                try:
                    req = _nfl_score_request(row)
                    scored = v17_team_event_base.score_team_event_request(
                        req,
                        event_api=event_api,
                        canonical_hydration_required=True,
                    )
                except HTTPException as exc:
                    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                    raw_code = str(detail.get("code") or "PROVIDER_UNAVAILABLE")
                    outcomes.append(_held(row, raw_code, str(detail.get("blocker_code") or raw_code), detail)); continue
                except Exception as exc:
                    outcomes.append(_held(row, "TRANSPORT_FAILURE", "NFL_ROW_SCORER_FAILURE",
                                          {"error_type": type(exc).__name__})); continue
                outcomes.append(_completed_nfl(row, scored)); continue

            if sport != "MLB" or league != "MLB":
                outcomes.append(_held(row, "MODEL_UNAVAILABLE", "SPORT_SPECIFIC_MODEL_UNAVAILABLE")); continue
            try:
                event = _hydrate(db_client_fn(), row)
            except Exception as exc:
                outcomes.append(_held(row, "PROVIDER_UNAVAILABLE", "EVENT_EVIDENCE_PROVIDER_UNAVAILABLE",
                                      {"error_type": type(exc).__name__})); continue
            if not event or event.get("feature_hydration_status") != "PASS":
                outcomes.append(_held(row, "INPUT_INCOMPLETE", "EVENT_EVIDENCE_INCOMPLETE")); continue
            try:
                if os.getenv("WOW_V17_ACTIVE", "0") == "1":
                    req = _mlb_v17_score_request(row, event)
                    scored = score_v17_team_event_request(
                        req,
                        event_api=event_api,
                        canonical_hydration_required=False,
                    )
                else:
                    req = event_api.ScoreEventRequest(**_score_request(row, event))
                    scored = event_api.score_event(req)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                code, blocker = _typed_mlb_failure(detail)
                outcomes.append(_held(row, code, blocker, detail)); continue
            except Exception as exc:
                outcomes.append(_held(row, "TRANSPORT_FAILURE", "ROW_SCORER_FAILURE",
                                      {"error_type": type(exc).__name__})); continue
            if not isinstance(scored, dict):
                outcomes.append(_held(row, "MODEL_OUTPUT_INVALID", "TEAM_EVENT_BACKEND_INVALID_RESPONSE")); continue
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