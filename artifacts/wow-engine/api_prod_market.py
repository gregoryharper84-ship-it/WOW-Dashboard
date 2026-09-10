"""V17 exact-capability guard for the governed WOW market scorer.

The underlying market/scoring implementation is preserved byte-for-byte in
``api_prod_market_legacy``. This module overrides only the V17 route-resolution
and typed model-failure boundary, then exposes the original FastAPI app. This
keeps the repair narrow while preventing aggregate prop health from implying
exact NFL stat-family readiness.
"""
from __future__ import annotations

import sys
import types
from typing import Any, Optional

from fastapi import HTTPException

import api_prod_market_legacy as _legacy
from prop_discrete_engine import PropCalibrationUnavailable
from prop_distribution_contract import PropDistributionContractError
from prop_fitted_provider import (
    PropFittedProviderUnavailable,
    capability_key,
    canonical_prop_period,
)

PROP_FEATURE_SCHEMA_VERSION = _legacy.PROP_FEATURE_SCHEMA_VERSION
_ORIGINAL_PREFLIGHT = _legacy._preflight_prop_route
_ORIGINAL_SCORE_DISCRETE = _legacy.score_discrete_prop_end_to_end


def _prop_period(stat_type: str) -> str:
    """Use the same canonical period normalization as the fitted provider."""
    return canonical_prop_period(stat_type)


def _prop_route_artifact(
    sport: str,
    stat_type: str,
    *,
    league: Optional[str] = None,
    market_family: Optional[str] = None,
    period: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve one exact five-field V17 capability through the v2 registry."""
    try:
        exact = capability_key(
            sport=sport,
            league=league,
            market_family=market_family,
            stat_type=stat_type,
            period=period,
        )
    except PropFittedProviderUnavailable as exc:
        return {
            "ok": False,
            "code": exc.code,
            "blocking_scope": "CAPABILITY",
            "specialist_selected": False,
            "specialist_invoked": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    try:
        result = _legacy.prod.get_client().rpc(
            "wow_prop_certified_model_artifact_v2",
            {
                "p_sport": exact.sport,
                "p_league": exact.league,
                "p_market_family": exact.market_family,
                "p_stat_type": exact.stat_type,
                "p_period": exact.period,
                "p_feature_schema_version": PROP_FEATURE_SCHEMA_VERSION,
            },
        ).execute()
    except Exception:
        return {
            "ok": False,
            "code": "PROP_MODEL_REGISTRY_UNAVAILABLE",
            "blocking_scope": "INFRASTRUCTURE",
            "specialist_selected": False,
            "specialist_invoked": False,
            "probability_publishable": False,
            "can_execute": False,
        }

    payload = result.data
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "code": "PROP_MODEL_REGISTRY_INVALID_RESPONSE",
            "blocking_scope": "INFRASTRUCTURE",
            "specialist_selected": False,
            "specialist_invoked": False,
            "probability_publishable": False,
            "can_execute": False,
        }
    return payload


def _route_failure_contract(route_artifact: dict[str, Any]) -> tuple[int, str, str]:
    """Map registry failures without abusing MODEL_UNAVAILABLE."""
    blocker = str(route_artifact.get("code") or "PROP_MODEL_REGISTRY_INVALID_RESPONSE")
    if blocker == "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND":
        return 409, "MODEL_UNAVAILABLE", "CAPABILITY"
    if blocker == "SPECIALIST_ROUTING_CONFLICT":
        return 409, "SPECIALIST_ROUTING_CONFLICT", "CAPABILITY"
    if blocker in {"PROP_FEATURE_SCHEMA_MISMATCH", "PROP_CAPABILITY_IDENTITY_INCOMPLETE"}:
        return 422, "MODEL_INPUTS_INSUFFICIENT", str(route_artifact.get("blocking_scope") or "EVIDENCE")
    if blocker in {"PROP_MODEL_REGISTRY_UNAVAILABLE", "PROP_MODEL_REGISTRY_INVALID_RESPONSE"}:
        return 503, blocker, "INFRASTRUCTURE"
    return 422, "MODEL_OUTPUT_INVALID", str(route_artifact.get("blocking_scope") or "MODEL_OUTPUT")


def _exact_requested_route(req: Any) -> dict[str, Any]:
    try:
        exact = capability_key(
            sport=req.sport,
            league=getattr(req, "league", None),
            market_family=getattr(req, "market_family", None),
            stat_type=req.stat_type,
            period=getattr(req, "period", None),
        )
        return {
            "sport": exact.sport,
            "league": exact.league,
            "market_family": exact.market_family,
            "stat_type": exact.stat_type,
            "period": exact.period,
            "feature_schema_version": PROP_FEATURE_SCHEMA_VERSION,
        }
    except PropFittedProviderUnavailable:
        return {
            "sport": str(getattr(req, "sport", "") or "").upper(),
            "league": str(getattr(req, "league", "") or "").upper(),
            "market_family": str(getattr(req, "market_family", "") or "PLAYER_PROP").upper(),
            "stat_type": str(getattr(req, "stat_type", "") or "").upper(),
            "period": str(getattr(req, "period", "") or "").upper(),
            "feature_schema_version": PROP_FEATURE_SCHEMA_VERSION,
        }


def _preflight_prop_route(req: Any, *, model_identity: str, lane: dict[str, Any]):
    """Preserve legacy preflight while enforcing exact typed failure semantics."""
    try:
        return _ORIGINAL_PREFLIGHT(req, model_identity=model_identity, lane=lane)
    except HTTPException as exc:
        detail = dict(exc.detail) if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        route_artifact = detail.get("route_artifact_evidence")
        if isinstance(route_artifact, dict):
            status_code, outer_code, blocking_scope = _route_failure_contract(route_artifact)
            detail["code"] = outer_code
            detail["blocker_code"] = route_artifact.get("code") or "PROP_MODEL_REGISTRY_INVALID_RESPONSE"
            detail["blocking_scope"] = blocking_scope
            detail["requested_route"] = _exact_requested_route(req)
            detail["specialist_selected"] = bool(route_artifact.get("specialist_selected", False))
            detail["specialist_invoked"] = False
            detail["probability_publishable"] = False
            detail["can_execute"] = False
            raise HTTPException(status_code=status_code, detail=detail) from exc

        if detail.get("code") == "MODEL_UNAVAILABLE":
            detail.setdefault("blocking_scope", "CAPABILITY")
            detail.setdefault("specialist_selected", False)
            detail["specialist_invoked"] = False
            detail["probability_publishable"] = False
            detail["can_execute"] = False
            detail.setdefault("requested_route", _exact_requested_route(req))
            raise HTTPException(status_code=exc.status_code, detail=detail) from exc
        raise


def _raise_model_path_error(exc: Exception) -> None:
    """Publish the V17 typed model outcome while retaining the raw blocker."""
    blocker = str(getattr(exc, "code", None) or "PROP_DISCRETE_MODEL_UNAVAILABLE")
    specialist_selected = True
    specialist_invoked = False

    if blocker == "MODEL_SCORER_FAILED":
        status_code, code, scope = 500, "MODEL_SCORER_FAILED", "SCORER"
        specialist_invoked = True
    elif isinstance(exc, PropDistributionContractError):
        status_code, code, scope = 422, "MODEL_OUTPUT_INVALID", "MODEL_OUTPUT"
        specialist_invoked = True
    elif blocker == "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND":
        status_code, code, scope = 409, "MODEL_UNAVAILABLE", "CAPABILITY"
        specialist_selected = False
    elif blocker in {"PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE", "PROP_CALIBRATOR_ADAPTER_UNAVAILABLE"}:
        status_code, code, scope = 409, "MODEL_UNAVAILABLE", "CAPABILITY"
    elif isinstance(exc, PropCalibrationUnavailable) or blocker in {
        "PROP_FEATURE_SCHEMA_MISMATCH",
        "MODEL_CALIBRATOR_BUNDLE_MISMATCH",
    }:
        status_code, code, scope = 422, "MODEL_INPUTS_INSUFFICIENT", "EVIDENCE"
    elif blocker in {"PROP_MODEL_REGISTRY_UNAVAILABLE", "PROP_MODEL_REGISTRY_INVALID_RESPONSE"}:
        status_code, code, scope = 503, blocker, "INFRASTRUCTURE"
    else:
        status_code, code, scope = 422, "MODEL_OUTPUT_INVALID", "MODEL_OUTPUT"

    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "blocker_code": blocker,
            "message": str(exc),
            "blocking_scope": scope,
            "specialist_selected": specialist_selected,
            "specialist_invoked": specialist_invoked,
            "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->CALIBRATION->PERSISTENCE",
            "probability_publishable": False,
            "can_execute": False,
        },
    ) from exc


def _guarded_score_discrete_prop_end_to_end(*args: Any, **kwargs: Any):
    """Convert an invoked scorer crash into MODEL_SCORER_FAILED, never unavailable."""
    try:
        return _ORIGINAL_SCORE_DISCRETE(*args, **kwargs)
    except (PropFittedProviderUnavailable, PropDistributionContractError, PropCalibrationUnavailable):
        raise
    except Exception as exc:
        raise PropFittedProviderUnavailable(
            "MODEL_SCORER_FAILED",
            "The selected governed prop scorer failed after invocation.",
        ) from exc


# Patch only the legacy module globals that the already-registered score_prop
# endpoint resolves at call time. All other market/scoring behavior is preserved.
_legacy._prop_period = _prop_period
_legacy._prop_route_artifact = _prop_route_artifact
_legacy._preflight_prop_route = _preflight_prop_route
_legacy._raise_model_path_error = _raise_model_path_error
_legacy.score_discrete_prop_end_to_end = _guarded_score_discrete_prop_end_to_end

app = _legacy.app
ScorePropRequest = _legacy.ScorePropRequest
score_prop = _legacy.score_prop


class _FacadeModule(types.ModuleType):
    """Keep the historical public module seam writable for tests and callers.

    Before the V17 exact-capability repair, ``api_prod_market`` owned the runtime
    globals directly. Existing tests and integration harnesses monkeypatch those
    public globals. The compatibility split into ``api_prod_market_legacy`` must
    therefore forward writes for legacy-owned symbols, otherwise the FastAPI
    endpoint keeps executing stale legacy globals and silently ignores overrides.
    """

    _LOCAL_ONLY = {
        "_legacy",
        "app",
        "ScorePropRequest",
        "score_prop",
        "PROP_FEATURE_SCHEMA_VERSION",
        "_ORIGINAL_PREFLIGHT",
        "_ORIGINAL_SCORE_DISCRETE",
        "_FacadeModule",
    }

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in self._LOCAL_ONLY and hasattr(_legacy, name):
            setattr(_legacy, name, value)
        super().__setattr__(name, value)


def __getattr__(name: str):
    return getattr(_legacy, name)


# Preserve the original monkeypatch/integration contract without widening the
# production scoring surface. Attribute writes on this facade now update the
# legacy globals that the already-registered endpoint resolves at call time.
sys.modules[__name__].__class__ = _FacadeModule
