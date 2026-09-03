"""Cross-sport TEAM_EVENT ingress for the active WOW V17 architecture.

The ingress contract is sport-agnostic, but model support is capability-specific.
Only a sport with a certified backend adapter may score. Unsupported sports fail
closed as MODEL_UNAVAILABLE; market prices or generic reasoning are never used as
a substitute. MLB fitted-model evidence must also traverse the LLP probability-
claim / event-decision governance bridge before any numeric probability can leave
this v17 boundary. No route in this module can execute a wager.

Public /score-team-event traffic requires server-owned canonical MLB evidence
hydration. Internal callers such as the Daily runner may pass evidence that they
already hydrated from the same canonical ledger. This preserves one evidence
authority while keeping lower-level scoring/test seams deterministic.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_runtime.idempotency import input_hash as _compute_input_hash
from agent_runtime.registry import worker_spec
from agent_runtime.runner_scout_research import execute_envelope
from agent_runtime.schemas import WorkerJobEnvelope
from agent_runtime.scout_research import RESEARCH_RECONCILER, RESEARCH_WORKERS, scout_lane
from v17.host_routing import LLP_TEAM_BETTING_ENGINE, resolve_host_route
from v17.mlb_team_event_hydration import resolve_mlb_team_event_evidence
from v17.v17_candidate_envelopes import V17TeamEventCandidateEnvelope, V17GovernedProbabilityPackage, DataUnavailable

CAN_EXECUTE = False

TeamDecisionIntent = Literal["WINNER", "FAVORITE", "UNDERDOG", "UPSET", "BEST_SIDE"]

_MLB_NUMERIC_MODEL_FIELDS = {
    "raw_home_probability", "raw_away_probability",
    "independent_home_probability", "independent_away_probability",
    "calibrated_home_probability", "calibrated_away_probability",
    "calibrated_home_lower_bound", "calibrated_home_upper_bound",
    "calibrated_away_lower_bound", "calibrated_away_upper_bound",
    "projected_runs_home", "projected_runs_away", "tie_after_9_probability",
}
_MLB_SPORT_ALIASES = frozenset({"MLB", "BASEBALL", "BASEBALL_MLB"})
_MLB_REQUIRED_EVIDENCE = (
    "venue", "home_starting_pitcher", "away_starting_pitcher",
    "home_starter_status", "away_starter_status",
    "home_lineup_status", "away_lineup_status",
)


def normalize_team_event_sport(sport: str, league: str | None) -> str:
    normalized_sport = str(sport or "").strip().upper()
    normalized_league = str(league or "").strip().upper()
    if normalized_sport in _MLB_SPORT_ALIASES and normalized_league in {"", "MLB"}:
        return "MLB"
    return normalized_sport


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
    return parsed.utcoffset() is not None and parsed.astimezone(timezone.utc) > datetime.now(timezone.utc)


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
    payload = dict(detail) if isinstance(detail, dict) else {"detail": detail}
    route = resolve_host_route(req.requester_host_identity, req.candidate_family)
    payload.update({
        "requester_host_identity": route.requester_host_identity,
        "controlling_engine_identity": route.controlling_engine_identity,
        "candidate_family": route.candidate_family,
        "host_terminal_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    })
    return payload


def _canonicalize_public_mlb_request(req: TeamEventRequest, event_api: Any) -> TeamEventRequest:
    """Replace public caller evidence with the exact server-owned MLB snapshot."""
    resolution = resolve_mlb_team_event_evidence(req, event_api=event_api)
    if resolution.get("ok") is not True:
        missing = list(resolution.get("missing_fields") or [])
        code = str(resolution.get("code") or "MLB_TEAM_EVENT_CANONICAL_EVIDENCE_UNAVAILABLE")
        # Preserve the stable acquisition failure class for an absent/incomplete
        # canonical package; typed canonical codes remain available as blocker_code.
        failure_code = "RUN_INVALID_ACQUISITION_INCOMPLETE" if missing else code
        raise HTTPException(
            status_code=422,
            detail=_augment_detail({
                "code": failure_code,
                "blocker_code": code,
                "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE" if missing else "RUN_INVALID_DATA_CONTRACT",
                "missing_fields": missing,
                "identity_mismatches": list(resolution.get("identity_mismatches") or []),
                "error_type": resolution.get("error_type"),
                "canonical_acquisition_attempted": True,
                "market_probability_substitution_allowed": False,
                "generic_reasoning_substitution_allowed": False,
                "probability_publishable": False,
            }, req),
        )
    return req.model_copy(update={
        "sport_specific_evidence": dict(resolution["evidence"]),
        "source_snapshot_id": str(resolution["canonical_source_snapshot_id"]),
        "latest_material_update_timestamp": str(resolution["canonical_snapshot_timestamp"]),
    })


def _mlb_request(req: TeamEventRequest, event_api: Any) -> Any:
    evidence = req.sport_specific_evidence or {}
    missing = [key for key in _MLB_REQUIRED_EVIDENCE if not str(evidence.get(key) or "").strip()]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=_augment_detail({
                "code": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "missing_fields": missing,
                "probability_publishable": False,
            }, req),
        )
    return event_api.ScoreEventRequest(
        research_run_id=req.research_run_id,
        requested_slate_date=req.requested_slate_date,
        requested_timezone=req.requested_timezone,
        scan_stage=req.scan_stage,
        event_key=req.event_key,
        official_event_id=req.official_event_id,
        event_start_time_utc=req.event_start_time_utc,
        sport="MLB", league="MLB", market_family="OUTRIGHT_WINNER",
        settlement_basis=req.settlement_basis,
        home_team=req.home_team, away_team=req.away_team,
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


def _without_numeric_model_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in _MLB_NUMERIC_MODEL_FIELDS}


def _llp_governance_hold(req: TeamEventRequest, route: Any, model_result: dict[str, Any], *, governance_detail: dict[str, Any] | None = None) -> dict[str, Any]:
    safe_model = _without_numeric_model_fields(model_result)
    blockers = ["LLP_PROBABILITY_CLAIM_AUDIT_NOT_PROVEN", "LLP_EVENT_DECISION_GOVERNOR_NOT_PROVEN", "V17_EVENT_LEDGER_LINK_NOT_PROVEN"]
    if governance_detail and governance_detail.get("blockers"):
        blockers.extend(str(value) for value in governance_detail.get("blockers") or [])
    return {
        **safe_model,
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "upstream_model_code": model_result.get("code"),
        "requester_host_identity": route.requester_host_identity,
        "controlling_engine_identity": LLP_TEAM_BETTING_ENGINE,
        "candidate_family": route.candidate_family,
        "llp_governance": governance_detail or {"status": "NOT_PROVEN"},
        "llp_probability_audit_result": "NOT_PROVEN",
        "llp_event_decision": "NOT_PROVEN",
        "event_mutex_status": "NOT_PROVEN",
        "terminal_label": "MODEL_QUALIFIED_HOLD",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "blockers": sorted(set(blockers)),
        "probability_fields_withheld": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "host_terminal_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def _validate_identity_lock(envelope: V17TeamEventCandidateEnvelope) -> list[str]:
    """Validate identity lock requirements per patch section 3."""
    blockers = []

    if not envelope.official_event_id:
        blockers.append("OFFICIAL_EVENT_ID_MISSING")
    if not envelope.official_event_id_source:
        blockers.append("OFFICIAL_EVENT_ID_PROVENANCE_MISSING")
    if not envelope.event_start_time_utc:
        blockers.append("EVENT_START_TIME_MISSING")
    if not envelope.home_team:
        blockers.append("HOME_TEAM_MISSING")
    if not envelope.away_team:
        blockers.append("AWAY_TEAM_MISSING")
    if not envelope.settlement_market:
        blockers.append("SETTLEMENT_MARKET_MISSING")

    return blockers


def _run_mlb_llp_governance(
    req: TeamEventRequest,
    route: Any,
    model_result: dict[str, Any],
    envelope: V17TeamEventCandidateEnvelope | None = None,
    *,
    event_api: Any,
) -> dict[str, Any]:
    score_snapshot_id = model_result.get("score_snapshot_id")
    if not score_snapshot_id:
        return _llp_governance_hold(req, route, model_result)

    if envelope:
        identity_blockers = _validate_identity_lock(envelope)
        if identity_blockers:
            return {
                **_without_numeric_model_fields(model_result),
                "code": "MODEL_OUTPUT_INVALID",
                "terminal_status": "MODEL_OUTPUT_INVALID",
                "blockers": identity_blockers,
                "failure_class": "IDENTITY_LOCK_FAIL",
                "rank_eligible": False,
                "probability_publishable": False,
                "host_terminal_authority": False,
                "global_terminal_authority": "V17_TERMINAL_REDUCER",
                "can_execute": False,
            }

        package = _build_governed_probability_package(envelope, model_result)
        if package is None:
            ok, calib_errors = False, []
            if model_result.get("calibration_health_status") == "PASS":
                ok, calib_errors = _validate_model_output_lossless(model_result)
            return {
                **_without_numeric_model_fields(model_result),
                "code": "MODEL_OUTPUT_INVALID",
                "terminal_status": "MODEL_OUTPUT_INVALID",
                "blockers": (calib_errors or ["MODEL_OUTPUT_PACKAGE_CONSTRUCTION_FAILED"]),
                "failure_class": "TRANSLATION_FAILURE",
                "rank_eligible": False,
                "probability_publishable": False,
                "host_terminal_authority": False,
                "global_terminal_authority": "V17_TERMINAL_REDUCER",
                "can_execute": False,
            }

    if envelope:
        ok, market_errors = envelope.validate_market_context()
        if not ok and envelope.market_status in ("EXACT_LINE", "ADJACENT_LINE"):
            return {
                **_without_numeric_model_fields(model_result),
                "code": "MODEL_OUTPUT_INVALID",
                "terminal_status": "MODEL_OUTPUT_INVALID",
                "blockers": market_errors or ["MARKET_CONTEXT_INCOMPLETE"],
                "failure_class": "MARKET_HANDOFF_FAIL",
                "rank_eligible": False,
                "probability_publishable": False,
                "host_terminal_authority": False,
                "global_terminal_authority": "V17_TERMINAL_REDUCER",
                "can_execute": False,
            }

    get_client = getattr(event_api, "get_client", None)
    if not callable(get_client):
        return _llp_governance_hold(req, route, model_result, governance_detail={"status": "UNAVAILABLE", "blockers": ["EVENT_LEDGER_CLIENT_UNAVAILABLE"]})
    try:
        rpc_result = get_client().rpc("wow_v17_mlb_team_event_governance_bridge", {
            "p_score_snapshot_id": str(score_snapshot_id),
            "p_research_run_id": req.research_run_id,
            "p_event_key": req.event_key,
            "p_requested_timezone": req.requested_timezone,
            "p_candidate_family": req.candidate_family,
            "p_decision_intent": req.decision_intent,
        }).execute()
        governance = rpc_result.data
    except Exception as exc:
        return _llp_governance_hold(req, route, model_result, governance_detail={"status": "UNAVAILABLE", "blockers": ["V17_EVENT_GOVERNANCE_BRIDGE_UNAVAILABLE"], "error_type": type(exc).__name__})
    if not isinstance(governance, dict):
        return _llp_governance_hold(req, route, model_result, governance_detail={"status": "INVALID", "blockers": ["V17_EVENT_GOVERNANCE_BRIDGE_INVALID_RESPONSE"]})
    required_pass = (
        governance.get("status") == "PASS"
        and governance.get("probability_audit_result") == "PASS_PROBABILITY_AUDIT"
        and governance.get("event_mutex_status") == "PASS"
        and governance.get("postmodel_gates_status") == "PASS"
        and governance.get("final_gates_status") == "PASS"
        and governance.get("global_terminal_reducer") == "V17_TERMINAL_REDUCER"
        and governance.get("can_execute") is False
    )
    if not required_pass:
        held = _llp_governance_hold(req, route, model_result, governance_detail=governance)
        final_label = str(governance.get("terminal_label") or "MODEL_QUALIFIED_HOLD")
        held["terminal_label"] = final_label
        held["terminal_ceiling"] = final_label
        held["terminal_reducer_input"] = {
            "status": governance.get("status"),
            "probability_audit": governance.get("probability_audit_result"),
            "event_mutex": governance.get("event_mutex_status"),
            "postmodel_gates": governance.get("postmodel_gates_status"),
            "final_gates": governance.get("final_gates_status"),
            "global_terminal_reducer": governance.get("global_terminal_reducer"),
        }
        return held

    final_label = str(governance.get("terminal_label") or "MODEL_QUALIFIED_HOLD")
    publishable = bool(model_result.get("probability_publishable") is True and governance.get("probability_publishable") is True and final_label == "FINAL_APPROVED")
    if not publishable:
        held = _llp_governance_hold(req, route, model_result, governance_detail=governance)
        held["terminal_label"] = final_label
        held["terminal_ceiling"] = final_label
        held["terminal_reducer_input"] = {
            "status": "PASS",
            "terminal_output": final_label,
        }
        return held

    return {
        **model_result,
        "requester_host_identity": route.requester_host_identity,
        "controlling_engine_identity": LLP_TEAM_BETTING_ENGINE,
        "candidate_family": route.candidate_family,
        "llp_governance": governance,
        "llp_probability_audit_result": governance["probability_audit_result"],
        "llp_event_decision": governance.get("event_decision"),
        "event_mutex_status": governance["event_mutex_status"],
        "terminal_label": final_label,
        "terminal_ceiling": final_label,
        "terminal_reducer_input": {
            "status": "PASS",
            "terminal_output": final_label,
        },
        "probability_publishable": True,
        "rank_eligible": bool(governance.get("rank_eligible")),
        "host_terminal_authority": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
    }


def _scout_research_envelope(run_id: str, candidate_id: str, worker_id: str, payload: dict[str, Any]) -> WorkerJobEnvelope:
    spec = worker_spec(worker_id)
    return WorkerJobEnvelope(run_id=run_id, job_id=f"{run_id}:{worker_id}", candidate_id=candidate_id, worker_id=worker_id, worker_version=spec.worker_version, as_of=datetime.now(timezone.utc).isoformat(), input_hash=_compute_input_hash(payload), payload=payload)


def _scout_research_barrier_blocked(req: TeamEventRequest, stage: str, blockers: list[str]) -> HTTPException:
    return HTTPException(status_code=409, detail=_augment_detail({"code": "SCOUT_RESEARCH_BARRIER_BLOCKED", "stage": stage, "blockers": list(blockers), "probability_publishable": False}, req))


def _run_mandatory_scout_research(req: TeamEventRequest) -> dict[str, Any]:
    run_id = f"v17-sync-{req.research_run_id}"
    candidate_id = req.event_key
    candidate: dict[str, Any] = {
        "sport": req.sport.strip().upper(), "league": req.league,
        "official_event_id": req.official_event_id, "market_family": req.market_family,
        "event_start_utc": req.event_start_time_utc,
        "evidence": dict(req.sport_specific_evidence or {}),
    }
    stages: list[dict[str, Any]] = []
    def _run(worker_id: str, payload: dict[str, Any]):
        env = _scout_research_envelope(run_id, candidate_id, worker_id, payload)
        out = execute_envelope(env)
        stages.append({"worker_id": worker_id, "status": out.status, "blockers": list(out.blockers)})
        return out
    scout_out = _run("wow.global-scout-coordinator", {"candidate": candidate, "scout_mode": "FOCUSED"})
    if scout_out.status != "SUCCEEDED":
        raise _scout_research_barrier_blocked(req, "wow.global-scout-coordinator", scout_out.blockers)
    lane = scout_lane(candidate)
    lane_worker = "wow.prop-scout-router" if lane == "PROP" else "wow.ml-event-scout-router"
    lane_out = _run(lane_worker, {"candidate": candidate})
    if lane_out.status != "SUCCEEDED":
        raise _scout_research_barrier_blocked(req, lane_worker, lane_out.blockers)
    reports: list[dict[str, Any]] = []
    team_jobs_ok = True
    for worker_id in RESEARCH_WORKERS:
        out = _run(worker_id, {"candidate": candidate, "evidence": candidate.get("evidence")})
        team_jobs_ok = team_jobs_ok and out.status == "SUCCEEDED"
        reports.append(out.output if out.status == "SUCCEEDED" else {"research_status": "DATA_UNOBTAINABLE", "worker_id": worker_id})
    reconciler_out = _run(RESEARCH_RECONCILER, {"research_reports": reports, "team_jobs_ok": team_jobs_ok, "evidence_present": isinstance(candidate.get("evidence"), dict), "event_start_present": bool(candidate.get("event_start_utc"))})
    if reconciler_out.status != "SUCCEEDED":
        raise _scout_research_barrier_blocked(req, RESEARCH_RECONCILER, reconciler_out.blockers)
    return {"status": "SUCCEEDED", "stages": stages}


def _build_team_event_envelope(
    req: TeamEventRequest,
    hydration_data: dict[str, Any] | None = None,
    market_data: dict[str, Any] | None = None,
) -> V17TeamEventCandidateEnvelope:
    """Build immutable canonical envelope for team event candidate (patch section 2).

    Every field required by the contract must be present here with explicit provenance.
    Missing data is marked with DataUnavailable, never NOT_CALLED after hydration.
    """
    hydration_data = hydration_data or {}
    market_data = market_data or {}
    evidence = req.sport_specific_evidence or {}

    event_date_local = hydration_data.get("event_date_local", "")
    if not event_date_local:
        try:
            dt = datetime.fromisoformat(req.event_start_time_utc.replace("Z", "+00:00"))
            event_date_local = dt.date().isoformat()
        except (TypeError, ValueError):
            event_date_local = ""

    def _maybe_unavailable(value: Any, source_key: str) -> str | DataUnavailable:
        if value:
            return str(value)
        return DataUnavailable(status="DATA_UNOBTAINABLE", source_attempted=[source_key])

    venue = evidence.get("venue") or DataUnavailable(
        status="DATA_UNOBTAINABLE",
        source_attempted=["sport_specific_evidence"],
    )

    official_event_status = _maybe_unavailable(evidence.get("official_event_status"), "sport_specific_evidence")

    home_starter = evidence.get("home_starting_pitcher")
    home_starter_status = _maybe_unavailable(evidence.get("home_starter_status"), "sport_specific_evidence")

    away_starter = evidence.get("away_starting_pitcher")
    away_starter_status = _maybe_unavailable(evidence.get("away_starter_status"), "sport_specific_evidence")

    home_lineup_status = _maybe_unavailable(evidence.get("home_lineup_status"), "sport_specific_evidence")
    away_lineup_status = _maybe_unavailable(evidence.get("away_lineup_status"), "sport_specific_evidence")

    injury_status = _maybe_unavailable(evidence.get("injury_status"), "sport_specific_evidence")
    weather_status = _maybe_unavailable(evidence.get("weather_status"), "market_weather_service")
    bullpen_status = _maybe_unavailable(evidence.get("bullpen_status"), "sport_specific_evidence")
    settlement_rule = _maybe_unavailable(evidence.get("settlement_rule"), "sport_specific_evidence")

    market_role_status = _maybe_unavailable(market_data.get("market_role_status"), "market_data")

    market_status = str(market_data.get("status", "DATA_UNOBTAINABLE"))

    timestamp_now = datetime.now(timezone.utc).isoformat()
    source_snapshot_timestamp = req.latest_material_update_timestamp or timestamp_now

    envelope = V17TeamEventCandidateEnvelope(
        research_run_id=req.research_run_id,
        requested_slate_date=req.requested_slate_date,
        requested_timezone=req.requested_timezone,
        event_key=req.event_key,
        official_event_id=req.official_event_id,
        official_event_id_source="CANONICAL_MLB_LEDGER",
        event_start_time_utc=req.event_start_time_utc,
        event_date_local=event_date_local,
        sport=req.sport,
        league=req.league,
        home_team=req.home_team,
        away_team=req.away_team,
        venue=venue,
        official_event_status=official_event_status,
        official_event_status_source="CANONICAL_MLB_LEDGER",
        settlement_market=req.market_family,
        settlement_basis=req.settlement_basis,
        settlement_rule=settlement_rule,
        settlement_source="CANONICAL_MLB_LEDGER",
        home_starter=home_starter,
        home_starter_status=home_starter_status,
        home_starter_source="CANONICAL_MLB_LEDGER",
        away_starter=away_starter,
        away_starter_status=away_starter_status,
        away_starter_source="CANONICAL_MLB_LEDGER",
        home_lineup_status=home_lineup_status,
        home_lineup_source="CANONICAL_MLB_LEDGER",
        away_lineup_status=away_lineup_status,
        away_lineup_source="CANONICAL_MLB_LEDGER",
        injury_status=injury_status,
        injury_source="CANONICAL_MLB_LEDGER",
        weather_status=weather_status,
        weather_source="MARKET_WEATHER_SERVICE",
        bullpen_status=bullpen_status,
        bullpen_source="CANONICAL_MLB_LEDGER",
        market_snapshot_id=market_data.get("snapshot_id"),
        market_snapshot_timestamp=market_data.get("timestamp"),
        market_source=market_data.get("source"),
        market_status=market_status,
        book_count=market_data.get("book_count"),
        market_role=market_data.get("market_role"),
        market_role_status=market_role_status,
        consensus_probability_no_vig=market_data.get("no_vig_probability"),
        market_prior_probability=market_data.get("prior_probability") or req.market_prior.get("probability") if req.market_prior else None,
        source_snapshot_id=req.source_snapshot_id,
        source_snapshot_timestamp=source_snapshot_timestamp,
        latest_material_update_timestamp=req.latest_material_update_timestamp,
        evidence_as_of=source_snapshot_timestamp,
    )
    return envelope


def _validate_model_output_lossless(model_result: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate non-lossy forwarding: present upstream fields must not become null/NOT_CALLED (patch section 4)."""
    errors = []
    critical_fields = {
        "raw_model_probability",
        "independent_model_probability",
        "calibrated_probability",
        "calibrated_probability_lower_bound",
        "calibrated_probability_upper_bound",
        "calibration_method",
        "calibration_version",
        "calibration_sample_scope",
        "calibration_health_status",
        "model_version",
        "model_timestamp",
    }

    for field in critical_fields:
        value = model_result.get(field)
        if value in (None, "NOT_CALLED", "MISSING", "UNKNOWN", ""):
            errors.append(f"MODEL_OUTPUT_FIELD_INVALID:{field}={value}")

    return len(errors) == 0, errors


def _build_governed_probability_package(
    envelope: V17TeamEventCandidateEnvelope,
    model_result: dict[str, Any],
) -> V17GovernedProbabilityPackage | None:
    """Build immutable governed probability package from model output (patch section 4).

    Sport-model output must traverse losslessly. All fields the model produced
    must be forwarded or explicitly translated with typed error record.
    Returns None if validation fails; caller must treat as MODEL_OUTPUT_INVALID.
    """
    ok, errors = _validate_model_output_lossless(model_result)
    if not ok:
        return None

    participant = str(model_result.get("participant", "UNKNOWN")).strip()
    opponent = str(model_result.get("opponent", "UNKNOWN")).strip()
    if not participant or participant == "UNKNOWN":
        return None
    if not opponent or opponent == "UNKNOWN":
        return None

    try:
        package = V17GovernedProbabilityPackage(
            research_run_id=envelope.research_run_id,
            event_key=envelope.event_key,
            official_event_id=envelope.official_event_id,
            participant=participant,
            opponent=opponent,
            market_role=envelope.market_role,
            outcome_space=str(model_result.get("outcome_space", "MONEYLINE")),
            raw_model_probability=float(model_result.get("raw_model_probability")),
            independent_model_probability=float(model_result.get("independent_model_probability")),
            market_prior_probability=envelope.market_prior_probability,
            market_prior_weight=float(model_result.get("market_prior_weight", 0.0)),
            calibrated_probability=float(model_result.get("calibrated_probability")),
            calibrated_probability_lower_bound=float(model_result.get("calibrated_probability_lower_bound")),
            calibrated_probability_upper_bound=float(model_result.get("calibrated_probability_upper_bound")),
            calibration_method=str(model_result.get("calibration_method")),
            calibration_version=str(model_result.get("calibration_version")),
            calibration_sample_scope=str(model_result.get("calibration_sample_scope")),
            calibration_health_status=str(model_result.get("calibration_health_status")),
            model_version=str(model_result.get("model_version")),
            model_timestamp=str(model_result.get("model_timestamp")),
            latest_material_update_timestamp=envelope.latest_material_update_timestamp,
            model_valid_after_latest_material_update=str(model_result.get("model_timestamp", "")) >= (envelope.latest_material_update_timestamp or ""),
            source_snapshot_id=envelope.source_snapshot_id,
            source_snapshot_timestamp=envelope.source_snapshot_timestamp,
            simulation_count_if_applicable=model_result.get("simulation_count"),
            model_component_weights_if_available=model_result.get("model_component_weights"),
            model_disagreement_if_available=model_result.get("model_disagreement"),
            uncertainty_method=model_result.get("uncertainty_method"),
        )

        ok, calib_errors = package.validate_calibration()
        if not ok:
            return None

        ok, domain_errors = package.validate_probability_domain()
        if not ok:
            return None

        return package
    except (TypeError, ValueError):
        return None


def score_team_event_request(req: TeamEventRequest, *, event_api: Any, canonical_hydration_required: bool = False) -> dict[str, Any]:
    try:
        route = resolve_host_route(req.requester_host_identity, req.candidate_family)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc), "probability_publishable": False, "can_execute": False}) from exc
    if route.controlling_engine_identity != LLP_TEAM_BETTING_ENGINE:
        raise HTTPException(status_code=422, detail={"code": "TEAM_EVENT_CONTROLLING_ENGINE_MISMATCH", "controlling_engine_identity": route.controlling_engine_identity, "probability_publishable": False, "can_execute": False})
    errors = _base_errors(req)
    if errors:
        raise HTTPException(status_code=422, detail=_augment_detail({"code": "TEAM_EVENT_CONTRACT_INVALID", "errors": errors, "probability_publishable": False}, req))

    sport = normalize_team_event_sport(req.sport, req.league)
    effective_req = req
    if sport == "MLB" and canonical_hydration_required:
        effective_req = _canonicalize_public_mlb_request(req, event_api)

    scout_research_barrier = _run_mandatory_scout_research(effective_req)
    if sport == "MLB":
        try:
            result = event_api.score_event(_mlb_request(effective_req, event_api))
        except HTTPException as exc:
            raise HTTPException(status_code=exc.status_code, detail=_augment_detail(exc.detail, effective_req)) from exc
        if not isinstance(result, dict):
            raise HTTPException(status_code=503, detail=_augment_detail({"code": "TEAM_EVENT_BACKEND_INVALID_RESPONSE", "probability_publishable": False}, effective_req))

        envelope = _build_team_event_envelope(effective_req, {}, {})
        governed = _run_mlb_llp_governance(effective_req, route, result, envelope=envelope, event_api=event_api)
        governed["scout_research_barrier"] = scout_research_barrier
        governed["candidate_envelope"] = {
            "research_run_id": envelope.research_run_id,
            "event_key": envelope.event_key,
            "official_event_id": envelope.official_event_id,
            "official_event_id_source": envelope.official_event_id_source,
            "event_start_time_utc": envelope.event_start_time_utc,
            "sport": envelope.sport,
            "league": envelope.league,
            "home_team": envelope.home_team,
            "away_team": envelope.away_team,
            "settlement_market": envelope.settlement_market,
            "market_status": envelope.market_status,
        }
        if canonical_hydration_required:
            governed["canonical_acquisition"] = {"status": "PASS", "source_snapshot_id": effective_req.source_snapshot_id, "latest_material_update_timestamp": effective_req.latest_material_update_timestamp, "can_execute": False}
        return governed
    raise HTTPException(status_code=409, detail=_augment_detail({
        "code": "MODEL_UNAVAILABLE", "failed_contract_scope": ["CONTROLLING_SPECIALIST"],
        "sport": sport, "league": req.league,
        "backend_route_status": "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED",
        "market_probability_substitution_allowed": False, "generic_reasoning_substitution_allowed": False,
        "probability_publishable": False, "blockers": [f"{sport}_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE"],
    }, req))


def install_team_event_routes(app: FastAPI, *, event_api: Any, auth_dependency: Any) -> None:
    """Install public V17 team/event ingress with mandatory canonical hydration."""
    @app.post("/score-team-event", dependencies=[auth_dependency], operation_id="scoreWowTeamEvent")
    def score_team_event(req: TeamEventRequest):
        return score_team_event_request(req, event_api=event_api, canonical_hydration_required=True)
