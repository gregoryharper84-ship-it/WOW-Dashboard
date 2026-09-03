"""Strict market adapter for V17 forward prop cohort capture.

The production /score-prop publication wrapper may intentionally return a
PROP_PROBABILITY_UNAVAILABLE hold while a fitted specialist is still available
for research-only calibration collection. This adapter permits a raw fitted
research fallback only for the exact typed V17 state proving that distinction.
It never converts a research result into a publishable probability.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

import calibration_publication_api as lane_patch


_ALLOWED_FAILED_SCOPE = {"CALIBRATION", "PUBLICATION"}
_ALLOWED_CALIBRATION_HEALTH = {"PASS", "AVAILABLE", "HEALTHY"}


def _typed_research_only_state(preflight: dict[str, Any], blockers: list[str]) -> bool:
    """Return True only for a model-available, publication-blocked V17 hold.

    Empty blockers are accepted only because the preflight itself must carry the
    complete typed state below. Unknown/global/confidence failures remain closed.
    """
    scope = {str(item).strip().upper() for item in (preflight.get("failed_contract_scope") or [])}
    return bool(
        preflight.get("ok") is True
        and str(preflight.get("governed_probability_capability") or "").upper() == "AVAILABLE"
        and str(preflight.get("specialist_model_capability") or "").upper() == "AVAILABLE"
        and str(preflight.get("calibration_health_status") or "").upper() in _ALLOWED_CALIBRATION_HEALTH
        and preflight.get("probability_publishable") is False
        and preflight.get("governed_publishable") is False
        and str(preflight.get("probability_claim_status") or "").upper() == "CALIBRATION_BLOCKED_NO_PUBLISH"
        and str(preflight.get("terminal_ceiling") or "").upper() == "MODEL_QUALIFIED_HOLD"
        and scope.issubset(_ALLOWED_FAILED_SCOPE)
        and (not blockers or lane_patch._publication_only(blockers))
    )


class ForwardCohortMarketAdapter:
    """Proxy market API with one narrowly typed raw-research fallback."""

    def __init__(self, market_api: Any):
        self._market_api = market_api
        self.ScorePropRequest = market_api.ScorePropRequest

    def score_prop(self, req: Any, x_wow_model_identity: str | None = None) -> dict[str, Any]:
        try:
            return self._market_api.score_prop(req, x_wow_model_identity)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            if exc.status_code != 409 or str(detail.get("code") or "").upper() != "PROP_PROBABILITY_UNAVAILABLE":
                raise

            preflight = lane_patch._governed_preflight(self._market_api)
            lane = self._market_api.prod._runtime_capability(self._market_api.prod.PROP_CAPABILITY_KEY)
            blockers = list(dict.fromkeys([
                *lane_patch._collect_blockers(lane.get("evidence") or {}),
                *lane_patch._collect_blockers(preflight),
            ]))
            if not _typed_research_only_state(preflight, blockers):
                raise

            model_identity = self._market_api.prod._reject_llp_prop_identity(x_wow_model_identity)
            return lane_patch._raw_specialist_research(
                self._market_api,
                req,
                model_identity=model_identity,
                lane=lane,
                preflight=preflight,
                blockers=blockers,
            )
