"""V17 bridge that restores certified Phase-A row scoring without promoting Phase B/C.

The accepted production wrapper owns a global publication preflight.  During
Phase A that global state can be MODEL_QUALIFIED_HOLD even though the certified
row scorer is capable of producing conservative empirical-Bayes shrinkage with
real bootstrap bounds.  This bridge permits the original governed row scorer to
run only for that exact typed Phase-A state.  It never sets publication flags,
never manufactures bounds, never changes 200/500 readiness thresholds, and
never grants MONEY_QUALIFIED / FINAL_APPROVED authority.

WOW-PATCH-2026-09-02-V17-PHASE-A-ROW-PUBLICATION
can_execute=false remains authoritative.
"""
from __future__ import annotations

import sys
from typing import Any, Optional

from fastapi import Header

import calibration_publication_api as lane_patch

_PHASE_A = "PHASE_A_PRECALIBRATION_SHRINKAGE"
_HEALTHY = {"PASS", "AVAILABLE", "HEALTHY"}
_ALLOWED_SCOPE = {"CALIBRATION", "PUBLICATION"}


def phase_a_row_scoring_permitted(preflight: dict[str, Any]) -> bool:
    """Prove the narrow state where the certified row scorer may still run."""
    evidence = preflight.get("capability_evidence") or {}
    blockers = [str(x).strip().upper() for x in (preflight.get("blockers") or []) if str(x).strip()]
    scope = {str(x).strip().upper() for x in (preflight.get("failed_contract_scope") or []) if str(x).strip()}
    return bool(
        preflight.get("ok") is True
        and str(preflight.get("specialist_model_capability") or "").upper() == "AVAILABLE"
        and str(preflight.get("governed_probability_capability") or "").upper() == "AVAILABLE"
        and str(preflight.get("calibration_health_status") or "").upper() in _HEALTHY
        and str(evidence.get("calibration_phase") or "").upper() == _PHASE_A
        and evidence.get("money_qualified_allowed") is False
        and evidence.get("final_approved_allowed") is False
        and preflight.get("probability_publishable") is False
        and preflight.get("governed_publishable") is False
        and str(preflight.get("probability_claim_status") or "").upper() == "CALIBRATION_BLOCKED_NO_PUBLISH"
        and str(preflight.get("terminal_ceiling") or "").upper() == "MODEL_QUALIFIED_HOLD"
        and not blockers
        and scope.issubset(_ALLOWED_SCOPE)
    )


def install_phase_a_row_publication(
    app: Any,
    *,
    auth_dependency: Any,
    market_api: Any,
) -> bool:
    """Install one idempotent bridge over the accepted production prop wrapper."""
    if getattr(market_api, "_wow_v17_phase_a_row_publication_installed", False):
        return True

    accepted = sys.modules.get("api_ncaaf_acceptance")
    original = getattr(accepted, "_original_market_score_prop", None) if accepted is not None else None
    if not callable(original):
        # Fail closed.  The bridge must never guess at or synthesize a scorer.
        return False

    fallback = market_api.score_prop

    def score_prop_phase_a(
        req: Any,
        x_wow_model_identity: Optional[str] = None,
    ) -> dict[str, Any]:
        preflight = lane_patch._governed_preflight(market_api)
        if phase_a_row_scoring_permitted(preflight):
            # The original scorer remains sole owner of inference, calibration,
            # predictive bounds, persistence, and probability_publishable.
            return original(req, x_wow_model_identity)
        return fallback(req, x_wow_model_identity)

    market_api.score_prop = score_prop_phase_a
    market_api._wow_v17_phase_a_row_publication_installed = True

    # Keep HTTP Action traffic and in-process Pick Request / Daily traffic on
    # the same function.  No alternate scoring authority is introduced.
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
        dependencies=[auth_dependency],
        operation_id="scoreWowProp",
    )
    def score_prop_phase_a_route(
        req: market_api.ScorePropRequest,
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        return market_api.score_prop(req, x_wow_model_identity)

    return True
