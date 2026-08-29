"""Production WOW governed backend compatibility layer.

This wrapper preserves the proven MLB event bridge from api_g11 and provides
lane-scoped governance/acquisition contracts used by api_prod_market.

Player props are a WOW Betting Engine lane, not an LLP Team Betting lane.
The production prop scorer is owned by api_prod_market and traverses:
  WOW_BETTING_ENGINE -> Render -> Supabase evidence -> controlling specialist
  -> WOW_PROP_FITTED_MODEL_V1 -> calibration -> wow_predictions.

The direct api_prod /score-prop route intentionally fails closed after the
capability/evidence/specialist gates. It can never fall back to the legacy
pitcher-shaped FittedParamsBundle scorer if this module is started directly.

No live wager execution is possible here; can_execute is always false.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException

import api as base_api
import api_g11 as event_api

ScorePropRequest = base_api.ScorePropRequest
_require_action_api_key = base_api._require_action_api_key
get_client = base_api.get_client

PROP_CAPABILITY_KEY = "PROP_PROBABILITY"
MLB_EVENT_CAPABILITY_KEY = "MLB_EVENT_PROBABILITY"
LLP_IDENTITIES = {
    "LLP",
    "LLP_TEAM_BETTING_MODEL",
    "LLP_TEAM_BETTING_ENGINE",
    "WOW_LLP_TEAM_BETTING_MODEL",
}

app = FastAPI(
    title=event_api.app.title,
    version=event_api.app.version,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.router.routes.extend(
    route
    for route in event_api.app.router.routes
    if not (
        (getattr(route, "path", None) == "/score-prop" and "POST" in (getattr(route, "methods", set()) or set()))
        or (getattr(route, "path", None) == "/governance" and "GET" in (getattr(route, "methods", set()) or set()))
    )
)
app.exception_handlers.update(event_api.app.exception_handlers)


def _runtime_capability(capability_key: str) -> dict[str, Any]:
    """Read one lane capability from Supabase; missing evidence fails closed."""
    try:
        result = (
            get_client()
            .table("wow_runtime_capabilities")
            .select("capability_key,capability_status,evidence,can_execute,updated_at")
            .eq("capability_key", capability_key)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RUNTIME_CAPABILITY_LEDGER_UNAVAILABLE",
                "capability_key": capability_key,
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    rows = result.data or []
    if not rows:
        return {
            "capability_key": capability_key,
            "capability_status": "UNAVAILABLE",
            "evidence": {"reason": "RUNTIME_CAPABILITY_ROW_MISSING"},
            "can_execute": False,
            "updated_at": None,
        }
    row = dict(rows[0])
    row["can_execute"] = False
    return row


def _prop_evidence(req: ScorePropRequest) -> dict[str, Any]:
    if not req.player:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_PLAYER_IDENTITY_REQUIRED",
                "failure_class": "RUN_INVALID_ACQUISITION_INCOMPLETE",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    try:
        result = get_client().rpc(
            "wow_prop_evidence_snapshot",
            {
                "p_source_snapshot_id": req.source_snapshot_id,
                "p_event_id": req.event_id,
                "p_sport": req.sport,
                "p_player": req.player,
                "p_stat_type": req.stat_type,
                "p_line": req.line,
            },
        ).execute()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PROP_EVIDENCE_BRIDGE_UNAVAILABLE",
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc

    payload = result.data
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PROP_EVIDENCE_BRIDGE_INVALID_RESPONSE",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return payload


def _visible_acquisition_evidence(evidence: dict[str, Any], line: float) -> dict[str, Any]:
    """Return auditable non-probability acquisition evidence to the caller."""
    raw_log = evidence.get("game_log")
    values = list(raw_log[:10]) if isinstance(raw_log, list) else []
    l5 = values[:5]
    more = sum(1 for value in values if isinstance(value, (int, float)) and value > line)
    less = sum(1 for value in values if isinstance(value, (int, float)) and value < line)
    pushes = sum(1 for value in values if isinstance(value, (int, float)) and value == line)
    return {
        "evidence_snapshot_id": evidence.get("source_snapshot_id"),
        "hydration_status": evidence.get("hydration_status"),
        "l10_values": values,
        "l5_values": l5,
        "l10_n": len(values),
        "l5_n": len(l5),
        "exact_line_results": {
            "line": line,
            "more_n": more,
            "less_n": less,
            "push_n": pushes,
        },
        "role_status": evidence.get("role_status"),
        "role_timestamp": evidence.get("role_timestamp"),
        "opportunity_ledger": evidence.get("opportunity_ledger"),
        "source_timestamps": evidence.get("source_timestamps") or {},
        "evidence_version": evidence.get("evidence_version"),
        "rate_provenance": evidence.get("rate_provenance"),
        "probability_fields_withheld": True,
        "can_execute": False,
    }


def _visible_model_evidence(row: Any) -> dict[str, Any]:
    """Project the legacy event/model fields still used by compatibility tests."""
    return {
        "effective_sample_size": getattr(row, "effective_sample_size", None),
        "simulation_draws": getattr(row, "simulation_draws", None),
        "regime_model_version": getattr(row, "regime_model_version", None),
        "calibration_status": getattr(row, "calibration_status", None),
        "calibration_version": getattr(row, "calibration_version", None),
        "bounds_method_version": getattr(row, "bounds_method_version", None),
        "calibrated_probability_lower_bound": getattr(row, "calibrated_probability_lower_bound", None),
        "calibrated_probability_upper_bound": getattr(row, "calibrated_probability_upper_bound", None),
        "model_timestamp": getattr(row, "model_timestamp", None),
        "probability_publishable": bool(getattr(row, "probability_publishable", False)),
        "can_execute": False,
    }


def _reject_llp_prop_identity(model_identity: Optional[str]) -> str:
    identity = (model_identity or "WOW_BETTING_ENGINE").strip().upper()
    if identity in LLP_IDENTITIES:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "LLP_PLAYER_PROP_SCOPE_PROHIBITED",
                "message": "LLP Team Betting Model is governed for team/event outright winners and upsets, not player props. Use the WOW Betting Engine prop lane.",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return identity


@app.get("/governance")
def governance():
    """Report global state plus explicit event/prop lane capabilities."""
    global_state = base_api._query_deployment_gate_state()
    calibration_health = base_api._query_calibration_health()

    if global_state:
        global_capability = global_state.get("governed_probability_capability", "UNAVAILABLE")
        global_status = global_state.get("governed_probability_status", "NOT_PRODUCED")
        deployment_gates = global_state.get("deployment_gates", [])
    else:
        global_capability = "UNAVAILABLE"
        global_status = "NOT_PRODUCED"
        deployment_gates = "GATE_LEDGER_UNREACHABLE"

    event_lane = _runtime_capability(MLB_EVENT_CAPABILITY_KEY)
    prop_lane = _runtime_capability(PROP_CAPABILITY_KEY)

    return {
        "governed_probability_capability": global_capability,
        "governed_probability_status": global_status,
        "probability_publishable": False,
        "can_execute": False,
        "deployment_gates": deployment_gates,
        "calibration_health": calibration_health,
        "compute_provider": "RENDER",
        "database_provider": "SUPABASE",
        "lane_capabilities": {
            MLB_EVENT_CAPABILITY_KEY: {
                "status": event_lane.get("capability_status", "UNAVAILABLE"),
                "evidence": event_lane.get("evidence") or {},
                "probability_publishable": False,
                "can_execute": False,
            },
            PROP_CAPABILITY_KEY: {
                "status": prop_lane.get("capability_status", "UNAVAILABLE"),
                "evidence": prop_lane.get("evidence") or {},
                "probability_publishable": False,
                "can_execute": False,
            },
        },
        "routing_contract": {
            "LLP_TEAM_BETTING_MODEL": "/score-event only for governed team/event outright-winner lanes",
            "WOW_BETTING_ENGINE_PLAYER_PROPS": "/score-prop via api_prod_market",
            "prop_required_path": "WOW_BETTING_ENGINE->RENDER->SUPABASE_EVIDENCE->CONTROLLING_SPECIALIST->WOW_PROP_FITTED_MODEL_V1->CALIBRATION->WOW_PREDICTIONS",
        },
    }


@app.post(
    "/score-prop",
    dependencies=[Depends(_require_action_api_key)],
    operation_id="scoreWowProp",
)
def score_prop(
    req: ScorePropRequest,
    x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
):
    """Compatibility prop boundary that cannot invoke the legacy scorer.

    The route keeps governance, acquisition, and specialist gates intact so a
    direct launch still fails with the most specific upstream blocker. If those
    gates pass and the capability is AVAILABLE, it stops rather than touching
    the retired pitcher-shaped scoring path. The serving production wrapper
    api_prod_market owns the certified discrete-PMF runtime.
    """
    model_identity = _reject_llp_prop_identity(x_wow_model_identity)
    lane = _runtime_capability(PROP_CAPABILITY_KEY)

    evidence = _prop_evidence(req)
    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        detail = dict(evidence)
        detail.setdefault("code", "RUN_INVALID_ACQUISITION_INCOMPLETE")
        detail["failure_class"] = "RUN_INVALID_ACQUISITION_INCOMPLETE"
        detail["specialist_invoked"] = False
        detail["probability_publishable"] = False
        detail["can_execute"] = False
        raise HTTPException(status_code=422, detail=detail)

    specialist = base_api._controlling_specialist_provider(req.sport, req.stat_type)
    if specialist is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SPECIALIST_ROUTING_UNAVAILABLE",
                "evidence_hydration": "PASS",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    if specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
        raise HTTPException(
            status_code=422,
            detail={
                "code": "MODEL_UNAVAILABLE",
                "controlling_specialist": "MODEL_UNAVAILABLE",
                "sport": specialist.get("sport"),
                "canonical_prop_type": specialist.get("canonical_prop_type"),
                "evidence_hydration": "PASS",
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    if lane.get("capability_status") != "AVAILABLE":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PROP_PROBABILITY_UNAVAILABLE",
                "governed_probability_capability": "UNAVAILABLE",
                "governed_probability_status": "NOT_PRODUCED",
                "capability_evidence": lane.get("evidence") or {},
                "evidence_hydration": "PASS",
                "acquisition_evidence": _visible_acquisition_evidence(evidence, req.line),
                "controlling_specialist": specialist.get("controlling_specialist"),
                "backend_traversal": {
                    "requester_model": model_identity,
                    "render": "PASS",
                    "supabase_capability": "PASS",
                    "supabase_evidence": "PASS",
                    "controlling_specialist": "PASS",
                    "governed_model": "BLOCKED",
                    "prediction_ledger_write": "NOT_ATTEMPTED",
                },
                "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    raise HTTPException(
        status_code=409,
        detail={
            "code": "PROP_DISCRETE_RUNTIME_ENTRYPOINT_REQUIRED",
            "message": "Legacy api_prod prop scoring is retired. The governed production prop runtime is api_prod_market.",
            "evidence_hydration": "PASS",
            "controlling_specialist": specialist.get("controlling_specialist"),
            "backend_traversal": {
                "requester_model": model_identity,
                "supabase_capability": "PASS",
                "supabase_evidence": "PASS",
                "controlling_specialist": "PASS",
                "legacy_fitted_params_path": "DISABLED",
                "prediction_ledger_write": "NOT_ATTEMPTED",
            },
            "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
            "probability_publishable": False,
            "can_execute": False,
        },
    )
