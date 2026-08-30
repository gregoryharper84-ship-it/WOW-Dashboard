"""Outermost WOW production API with calibration/publication lane separation.

Keeps the full current NCAAF/prop/settlement/pick-request route stack, replacing
only POST /score-prop after every inherited wrapper has been installed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException

import api_ncaaf_acceptance as base
import calibration_publication_api as lane_patch

app = base.app
market_api = base.base.market_api

# Replace the inherited score route only after every lower wrapper has installed
# its routes. Publication health is checked independently of aggregate specialist
# capability; an AVAILABLE specialist lane therefore cannot accidentally publish
# through a blocked calibration-health gate.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if not (
        getattr(route, "path", None) == "/score-prop"
        and "POST" in (getattr(route, "methods", set()) or set())
    )
]


@app.post(
    "/score-prop",
    dependencies=[Depends(market_api.prod._require_action_api_key)],
    operation_id="scoreWowProp",
)
def score_prop_lane_separated(
    req: market_api.ScorePropRequest,
    x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
):
    model_identity = market_api.prod._reject_llp_prop_identity(x_wow_model_identity)
    lane = market_api.prod._runtime_capability(market_api.prod.PROP_CAPABILITY_KEY)
    preflight = lane_patch._governed_preflight(market_api)
    blockers = list(dict.fromkeys([
        *lane_patch._collect_blockers(lane.get("evidence") or {}),
        *lane_patch._collect_blockers(preflight),
    ]))

    if preflight.get("governed_publishable") is True or preflight.get("probability_publishable") is True:
        # Healthy publication path: use the existing calibrated scorer and all
        # of its immutable prediction/market/money safeguards unchanged.
        return market_api.score_prop(req, x_wow_model_identity)

    if lane_patch._publication_only(blockers):
        # Calibration/publication lock only: preserve certified raw specialist
        # research, suppress calibrated claims/bounds and skip governed write.
        return lane_patch._raw_specialist_research(
            market_api,
            req,
            model_identity=model_identity,
            lane=lane,
            preflight=preflight,
            blockers=blockers,
        )

    # Unknown/global/model capability failures remain fail-closed. We do not
    # guess their scope merely to keep a row alive.
    raise HTTPException(
        status_code=409,
        detail={
            "code": "PROP_PROBABILITY_UNAVAILABLE",
            "governed_probability_capability": lane.get("capability_status") or "UNAVAILABLE",
            "governed_publication_capability": preflight.get("governed_publication_capability") or "UNAVAILABLE",
            "specialist_model_capability": preflight.get("specialist_model_capability") or "NOT_EVALUATED",
            "failed_contract_scope": preflight.get("failed_contract_scope") or ["GLOBAL"],
            "probability_claim_status": preflight.get("probability_claim_status") or "MODEL_UNAVAILABLE",
            "capability_evidence": lane.get("evidence") or {},
            "preflight": preflight,
            "blockers": blockers or ["UNCLASSIFIED_CAPABILITY_FAILURE"],
            "probability_publishable": False,
            "governed_publishable": False,
            "can_execute": False,
        },
    )
