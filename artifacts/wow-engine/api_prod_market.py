"""Market-lane compatibility layer for the governed WOW production API.

Builds on api_prod and replaces only POST /score-prop so exact two-way market
quotes can traverse the existing engine. Missing/invalid market evidence is a
MARKET HOLD, never a reason to erase an otherwise publishable sporting-model
probability. can_execute remains false unconditionally.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

import api_prod as prod
from market import MarketQuote


class MarketQuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: str
    american_odds: float
    line: float
    settlement_basis: str
    retrieved_at: str
    participant: str
    stat: str
    period: str
    event_id: str


class ScorePropRequest(prod.ScorePropRequest):
    market_side_a: Optional[MarketQuoteInput] = None
    market_side_b: Optional[MarketQuoteInput] = None


app = FastAPI(
    title=prod.app.title,
    version=prod.app.version,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.router.routes.extend(
    route
    for route in prod.app.router.routes
    if not (
        getattr(route, "path", None) == "/score-prop"
        and "POST" in (getattr(route, "methods", set()) or set())
    )
)
app.exception_handlers.update(prod.app.exception_handlers)


def _to_market_quote(value: Optional[MarketQuoteInput]) -> Optional[MarketQuote]:
    if value is None:
        return None
    return MarketQuote(**value.model_dump())


def _market_lane(row: Any) -> dict[str, Any]:
    available = bool(getattr(row, "market_prior_available", False))
    quality = getattr(row, "market_prior_quality", None) or "NO_QUALIFYING_MARKET"
    status = "PASS" if available and quality == "EXACT_TWO_WAY_NO_VIG" else "HOLD"
    return {
        "status": status,
        "quality": quality,
        "market_prior_available": available,
        "market_prior_probability": getattr(row, "market_prior_probability", None),
        "market_prior_weight": getattr(row, "market_prior_weight", 0.0),
        "market_prior_weight_source": getattr(row, "market_prior_weight_source", None),
        "reference_market_probability_raw": getattr(row, "reference_market_probability_raw", None),
        "reference_market_side": getattr(row, "reference_market_side", None),
        "reference_market_price": getattr(row, "reference_market_price", None),
        "blocks_model_probability": False,
        "can_execute": False,
    }


def _money_lane(row: Any) -> dict[str, Any]:
    status = getattr(row, "money_lane_status", None) or "PAYOUT_UNRESOLVED"
    return {
        "status": "PASS" if status == "RESOLVED" else "HOLD",
        "money_lane_status": status,
        "blocks_model_probability": False,
        "can_execute": False,
    }


@app.post(
    "/score-prop",
    dependencies=[Depends(prod._require_action_api_key)],
    operation_id="scoreWowProp",
)
def score_prop(
    req: ScorePropRequest,
    x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
):
    """Governed player-prop scoring with explicit objective separation."""
    model_identity = prod._reject_llp_prop_identity(x_wow_model_identity)
    lane = prod._runtime_capability(prod.PROP_CAPABILITY_KEY)

    evidence = prod._prop_evidence(req)
    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        detail = dict(evidence)
        detail.setdefault("code", "RUN_INVALID_ACQUISITION_INCOMPLETE")
        detail["failure_class"] = "RUN_INVALID_ACQUISITION_INCOMPLETE"
        detail["specialist_invoked"] = False
        detail["probability_publishable"] = False
        detail["can_execute"] = False
        raise HTTPException(status_code=422, detail=detail)

    specialist = prod.base_api._controlling_specialist_provider(req.sport, req.stat_type)
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
                "acquisition_evidence": prod._visible_acquisition_evidence(evidence, req.line),
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
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    bundle = prod.base_api._fitted_params_provider(req.sport, req.stat_type)
    if bundle is None:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "PROP_FITTED_PROVIDER_UNAVAILABLE",
                "message": "Real fitted per-sport parameters are not wired for this governed prop lane.",
                "evidence_hydration": "PASS",
                "controlling_specialist": specialist.get("controlling_specialist"),
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    scored_at = datetime.now(timezone.utc).isoformat()
    draws = max(prod.base_api.MIN_SIMULATION_DRAWS, specialist.get("min_event_tree_simulations") or 0)
    result = prod.base_api.score_prop_end_to_end(
        event_id=req.event_id,
        event_start_time=req.event_start_time,
        sport=req.sport,
        stat_type=req.stat_type,
        line=req.line,
        direction=req.direction,
        source_snapshot_id=req.source_snapshot_id,
        cohort=bundle.cohort,
        pitcher=bundle.pitcher,
        regime_params=bundle.regime_params,
        resample_fn=bundle.resample_fn,
        n_eff=bundle.n_eff,
        seed=req.seed,
        candidate_direction=req.direction,
        market_side_a=_to_market_quote(req.market_side_a),
        market_side_b=_to_market_quote(req.market_side_b),
        scored_at=scored_at,
        parent_cohort=bundle.parent_cohort,
        settled_n_in_cohort=bundle.settled_n_in_cohort,
        money_lane_status=req.money_lane_status,
        draws=draws,
    )

    if not result.row.probability_publishable:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_MODEL_NOT_PUBLISHABLE",
                "data_gaps": result.row.data_gaps,
                "error": result.error,
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    persisted = prod.base_api._persist_fn(result.row)
    if not isinstance(persisted, dict) or not persisted.get("prediction_id"):
        raise HTTPException(
            status_code=500,
            detail={
                "code": "PROP_PREDICTION_LEDGER_WRITE_UNPROVEN",
                "probability_publishable": False,
                "can_execute": False,
            },
        )

    market_lane = _market_lane(result.row)
    money_lane = _money_lane(result.row)
    return {
        "ok": True,
        "prediction": persisted,
        "acquisition_evidence": prod._visible_acquisition_evidence(evidence, req.line),
        "model_evidence": prod._visible_model_evidence(result.row),
        "evidence": evidence,
        "objective_lanes": {
            "MODEL": {
                "status": "PASS",
                "probability_publishable": True,
                "can_execute": False,
            },
            "MARKET": market_lane,
            "MONEY": money_lane,
        },
        "backend_traversal": {
            "requester_model": model_identity,
            "render": "PASS",
            "supabase_capability": "PASS",
            "supabase_evidence": "PASS",
            "controlling_specialist": "PASS",
            "governed_model": "PASS",
            "prediction_ledger_write": "PASS",
        },
        "probability_publishable": True,
        "can_execute": False,
    }
