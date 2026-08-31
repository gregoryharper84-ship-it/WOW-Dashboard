"""Cross-sport TEAM_EVENT ingress for the WOW v17 candidate architecture.

The ingress contract is sport-agnostic, but model support is capability-specific.
Only a sport with a certified backend adapter may score. Unsupported sports fail
closed as MODEL_UNAVAILABLE; market prices or generic reasoning are never used as
a substitute. No route in this module can execute a wager.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from v17.host_routing import LLP_TEAM_BETTING_ENGINE, resolve_host_route

CAN_EXECUTE = False

TeamDecisionIntent = Literal["WINNER", "FAVORITE", "UNDERDOG", "UPSET", "BEST_SIDE"]


class TeamEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requester_host_identity: str
    research_run_id: str = Field(min_length=1, max_length=128)
    requested_slate_date: str
    requested_timezone: str = Field(min_length=1, max_length=64)
    scan_stage: Literal["PREGAME"] = "PREGAME"
    candidate_family: Literal["TEAM_EVENT", "OUTRIGHT_WINNER", "MONEYLINE", "FAVORITE", "UNDERDOG", "UPSET", "MATCH_WINNER", "FIGHT_WINNER"] = "TEAM_EVENT"
    decision_intent: TeamDecisionIntent = "WINNER"

    event_key: str = Field(min_length=1, max_length=256)
    official_event_id: str = Field(min_length=1, max_length=128)
    event_start_time_utc: str
    sport: str = Field(min_length=1, max_length=32)
    league: str = Field(min_length=1, max_length=64)
    market_family: Literal["OUTRIGHT_WINNER"] = "OUTRIGHT_WINNER"
    settlement_basis: str = Field(min_length=1, max_length=256)

    home_team: str = Field(min_length=1, max_length=160)
    away_team: str = Field(min_length=1, max_length=160)
    source_snapshot_id: str = Field(min_length=1, max_length=128)
    latest_material_update_timestamp: str | None = None
    market_prior: dict[str, Any] | None = None
    sport_specific_evidence: dict[str, Any] = Field(default_factory=dict)


class TeamEventCapabilityResponse(BaseModel):
    requester_host_identity: str
    controlling_engine_identity: str = LLP_TEAM_BETTING_ENGINE
    candidate_family: str
    sport: str
    backend_route_status: str
    terminal_label: str
    blockers: list[str] = Field(default_factory=list)
    probability_publishable: bool = False
    can_execute: bool = CAN_EXECUTE


def _aware_future(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if parsed.utcoffset() is None:
        return False
    return parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)


def _base_errors(req: TeamEventRequest) -> list[str]:
    errors: list[str] = []
    try:
        date.fromisoformat(req.requested_slate_date)
    except (TypeError, ValueError):
        errors.append("REQUESTED_SLATE_DATE_INVALID")
    if not _aware_future(req.event_start_time_utc):
        errors.append("EVENT_NOT_PREGAME_OR_TIMESTAMP_INVALID")
    if req.home_team.strip().casefold() == req.away_team.strip().casefold():
        errors.append("EVENT_PARTICIPANTS_NOT_MUTUALLY_EXCLUSIVE")
    if req.latest_material_update_timestamp is not None:
        try:
            material = datetime.fromisoformat(req.latest_material_update_timestamp.replace("Z", "+00:00"))
            if material.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("LATEST_MATERIAL_UPDATE_TIMESTAMP_INVALID")
    return errors


def _augment_detail(detail: Any, req: TeamEventRequest) -> dict[str, Any]:
    if isinstance(detail, dict):
        payload = dict(detail)
    else:
        payload = {"detail": detail}
    route = resolve_host_route(req.requester_host_identity, req.candidate_family)
    payload.update(
        {
            "requester_host_identity": route.requester_host_identity,
            "controlling_engine_identity": route.controlling_engine_identity,
            "candidate_family": route.candidate_family,
            "host_terminal_authority": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }
    )
    return payload


def _mlb_request(req: TeamEventRequest, event_api: Any) -> Any:
    evidence = req.sport_specific_evidence or {}
    required = (
        "venue",
        "home_starting_pitcher",
        "away_starting_pitcher",
        "home_starter_status",
        "away_starter_status",
        "home_lineup_status",
        "away_lineup_status",
    )
    missing = [key for key in required if not str(evidence.get(key) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=_augment_detail(
                {
                    "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    "missing_fields": missing,
                    "probability_publishable": False,
                },
                req,
            ),
        )

    return event_api.ScoreEventRequest(
        research_run_id=req.research_run_id,
        requested_slate_date=req.requested_slate_date,
        requested_timezone=req.requested_timezone,
        scan_stage=req.scan_stage,
        event_key=req.event_key,
        official_event_id=req.official_event_id,
        event_start_time_utc=req.event_start_time_utc,
        sport="MLB",
        league="MLB",
        market_family="OUTRIGHT_WINNER",
        settlement_basis=req.settlement_basis,
        home_team=req.home_team,
        away_team=req.away_team,
        venue=evidence["venue"],
        home_starting_pitcher=evidence["home_starting_pitcher"],
        away_starting_pitcher=evidence["away_starting_pitcher"],
        home_starter_status=evidence["home_starter_status"],
        away_starter_status=evidence["away_starter_status"],
        home_lineup_status=evidence["home_lineup_status"],
        away_lineup_status=evidence["away_lineup_status"],
        latest_material_update_timestamp=req.latest_material_update_timestamp,
        source_snapshot_id=req.source_snapshot_id,
        market_prior=req.market_prior,
    )


def score_team_event_request(req: TeamEventRequest, *, event_api: Any) -> dict[str, Any]:
    try:
        route = resolve_host_route(req.requester_host_identity, req.candidate_family)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": str(exc),
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    if route.controlling_engine_identity != LLP_TEAM_BETTING_ENGINE:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "TEAM_EVENT_CONTROLLING_ENGINE_MISMATCH",
                "controlling_engine_identity": route.controlling_engine_identity,
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    errors = _base_errors(req)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=_augment_detail(
                {
                    "code": "TEAM_EVENT_CONTRACT_INVALID",
                    "errors": errors,
                    "probability_publishable": False,
                },
                req,
            ),
        )

    sport = req.sport.strip().upper()
    if sport == "MLB":
        try:
            result = event_api.score_event(_mlb_request(req, event_api))
        except HTTPException as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=_augment_detail(exc.detail, req),
            ) from exc
        if not isinstance(result, dict):
            raise HTTPException(
                status_code=503,
                detail=_augment_detail(
                    {
                        "code": "TEAM_EVENT_BACKEND_INVALID_RESPONSE",
                        "probability_publishable": False,
                    },
                    req,
                ),
            )
        return {
            **result,
            "requester_host_identity": route.requester_host_identity,
            "controlling_engine_identity": LLP_TEAM_BETTING_ENGINE,
            "candidate_family": route.candidate_family,
            "host_terminal_authority": False,
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }

    # The universal contract exists for all team/event sports, but no sport may
    # be promoted without an actually registered fitted model and adapter.
    raise HTTPException(
        status_code=409,
        detail=_augment_detail(
            {
                "code": "MODEL_UNAVAILABLE",
                "failed_contract_scope": ["CONTROLLING_SPECIALIST"],
                "sport": sport,
                "league": req.league,
                "backend_route_status": "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED",
                "market_probability_substitution_allowed": False,
                "generic_reasoning_substitution_allowed": False,
                "probability_publishable": False,
                "blockers": [f"{sport}_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE"],
            },
            req,
        ),
    )


def install_team_event_routes(app: FastAPI, *, event_api: Any, auth_dependency: Any) -> None:
    """Install the v17 candidate universal team/event ingress on an app."""

    @app.post(
        "/score-team-event",
        dependencies=[auth_dependency],
        operation_id="scoreWowTeamEvent",
    )
    def score_team_event(req: TeamEventRequest):
        return score_team_event_request(req, event_api=event_api)
