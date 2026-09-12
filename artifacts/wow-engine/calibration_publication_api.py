"""Production route patch for calibration/publication lane separation.

Installed as the outermost API wrapper. The normal governed/calibrated scorer is
left untouched whenever publication capability is healthy. Only an explicitly
calibration/publication-scoped lock is permitted to enter the raw specialist
research path; unknown/global/model blockers still fail closed.

No raw research result is persisted as a governed prediction and no calibrated
probability/bounds are invented. Market evidence remains objective-separated.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Iterable, Optional

from fastapi import Depends, Header, HTTPException

from calibration_publication_lane import (
    PUBLICATION_SCOPED_BLOCKERS,
    resolve_lane_separation,
)
from market import resolve_market_prior
from prop_distribution_contract import PropDistributionContractError, derive_line_probabilities
from prop_fitted_provider import CertifiedInference, PropFittedProviderUnavailable, infer_certified_distribution


_RECOGNIZED_PUBLICATION_PREFIXES = ("FORWARD_SHADOW_",)
_CAPABILITY_ABSENCE_CODES = {
    "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
    "PROP_MODEL_FAMILY_ADAPTER_UNAVAILABLE",
    "PROP_BUNDLE_NOT_CERTIFIED",
}
_OUTPUT_INVALID_CODES = {
    "PROP_MODEL_ADAPTER_INVALID_OUTPUT",
    "PROP_CERTIFIED_INFERENCE_INVALID",
    "PROP_MODEL_ARTIFACT_PAYLOAD_INVALID",
    "PROP_MODEL_ARTIFACT_METADATA_INVALID",
    "PROP_PROVIDER_IDENTITY_MISMATCH",
}


def _normalize_blocker(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _collect_blockers(value: Any) -> list[str]:
    """Collect explicit blocker/reason/status_reason values without guessing."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_upper = str(key).upper()
            if key_upper in {"BLOCKER", "BLOCKER_CODE", "REASON", "STATUS_REASON", "BLOCKERS"}:
                if isinstance(child, (list, tuple, set)):
                    for item in child:
                        normalized = _normalize_blocker(item)
                        if normalized:
                            found.append(normalized)
                else:
                    normalized = _normalize_blocker(child)
                    if normalized:
                        found.append(normalized)
            elif isinstance(child, dict):
                found.extend(_collect_blockers(child))
    return list(dict.fromkeys(found))


def _is_known_publication_blocker(blocker: str) -> bool:
    return blocker in PUBLICATION_SCOPED_BLOCKERS or blocker.startswith(_RECOGNIZED_PUBLICATION_PREFIXES)


def _publication_only(blockers: Iterable[str]) -> bool:
    items = tuple(dict.fromkeys(_normalize_blocker(x) for x in blockers if _normalize_blocker(x)))
    return bool(items) and all(_is_known_publication_blocker(item) for item in items)


def _governed_preflight(market_api: Any) -> dict[str, Any]:
    """Read the canonical publication preflight. Unreachable evidence fails closed."""
    try:
        result = market_api.prod.get_client().rpc("wow_governed_probability_preflight", {}).execute()
    except Exception:
        return {
            "ok": False,
            "code": "GOVERNED_PROBABILITY_PREFLIGHT_UNAVAILABLE",
            "governed_probability_capability": "UNAVAILABLE",
            "calibration_health_status": "UNKNOWN",
            "probability_publishable": False,
            "blockers": ["GOVERNED_PROBABILITY_PREFLIGHT_UNAVAILABLE"],
            "can_execute": False,
        }
    payload = result.data
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "code": "GOVERNED_PROBABILITY_PREFLIGHT_INVALID_RESPONSE",
            "governed_probability_capability": "UNAVAILABLE",
            "calibration_health_status": "UNKNOWN",
            "probability_publishable": False,
            "blockers": ["GOVERNED_PROBABILITY_PREFLIGHT_INVALID_RESPONSE"],
            "can_execute": False,
        }
    return payload


def _requested_scope(req: Any) -> dict[str, str]:
    return {
        "sport": str(getattr(req, "sport", "") or "").strip().upper(),
        "stat_type": str(getattr(req, "stat_type", "") or "").strip().upper(),
    }


def _failure(
    *,
    req: Any,
    status_code: int,
    code: str,
    blocker_code: str,
    specialist_name: str | None,
    specialist_capability: str,
    specialist_status: str,
    extra: dict[str, Any] | None = None,
) -> HTTPException:
    detail: dict[str, Any] = {
        "code": code,
        "blocker_code": blocker_code,
        "requested_scope": _requested_scope(req),
        "specialist_model_capability": specialist_capability,
        "specialist_model_name": specialist_name,
        "specialist_model_status": specialist_status,
        "failed_contract_scope": ["CONFIDENCE"],
        "probability_claim_status": code,
        "probability_publishable": False,
        "governed_publishable": False,
        "can_execute": False,
    }
    if extra:
        detail.update(extra)
    return HTTPException(status_code=status_code, detail=detail)


def _selected_raw_probability(line_probs: Any, direction: str) -> float:
    side = str(direction or "").strip().upper()
    if side == "MORE":
        value = line_probs.probability_more
    elif side == "LESS":
        value = line_probs.probability_less
    else:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "PROP_DIRECTION_INVALID",
                "failed_contract_scope": ["CONFIDENCE"],
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_OUTPUT_INVALID",
                "blocker_code": "PROP_RAW_PROBABILITY_INVALID",
                "failed_contract_scope": ["CONFIDENCE"],
                "probability_claim_status": "MODEL_OUTPUT_INVALID",
                "probability_publishable": False,
                "can_execute": False,
            },
        ) from exc
    if not isfinite(value) or not (0.0 < value < 1.0):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MODEL_OUTPUT_INVALID",
                "blocker_code": "PROP_RAW_PROBABILITY_INVALID",
                "failed_contract_scope": ["CONFIDENCE"],
                "probability_claim_status": "MODEL_OUTPUT_INVALID",
                "probability_publishable": False,
                "can_execute": False,
            },
        )
    return value


def _classify_provider_failure(exc: Exception) -> tuple[str, str, str, int]:
    blocker = str(getattr(exc, "code", None) or "PROP_CERTIFIED_MODEL_SCORER_FAILED").upper()
    if blocker in _CAPABILITY_ABSENCE_CODES:
        return "MODEL_UNAVAILABLE", "UNAVAILABLE", "CAPABILITY_UNAVAILABLE", 409
    if blocker in _OUTPUT_INVALID_CODES:
        return "MODEL_OUTPUT_INVALID", "AVAILABLE", "OUTPUT_INVALID", 409
    if isinstance(exc, PropDistributionContractError):
        if blocker == "PROP_MODEL_ADAPTER_INVALID_OUTPUT" or "OUTPUT" in blocker:
            return "MODEL_OUTPUT_INVALID", "AVAILABLE", "OUTPUT_INVALID", 409
        return "MODEL_INPUTS_INSUFFICIENT", "AVAILABLE", "ABSTAINED_INPUT_CONTRACT", 422
    return "MODEL_SCORER_FAILED", "AVAILABLE", "SCORER_FAILED", 503


def _raw_specialist_research(
    market_api: Any,
    req: Any,
    *,
    model_identity: str,
    lane: dict[str, Any],
    preflight: dict[str, Any],
    blockers: list[str],
) -> dict[str, Any]:
    """Run certified raw specialist inference only; never run calibration/persistence."""
    specialist = market_api.prod.base_api._controlling_specialist_provider(req.sport, req.stat_type)
    if specialist is None:
        raise _failure(
            req=req,
            status_code=503,
            code="SPECIALIST_ROUTING_UNAVAILABLE",
            blocker_code="NO_CONTROLLING_SPECIALIST_ROUTE",
            specialist_name=None,
            specialist_capability="UNAVAILABLE",
            specialist_status="ROUTING_UNAVAILABLE",
        )
    specialist_name = specialist.get("controlling_specialist")
    if specialist_name == "MODEL_UNAVAILABLE":
        raise _failure(
            req=req,
            status_code=422,
            code="MODEL_UNAVAILABLE",
            blocker_code="NO_CERTIFIED_FITTED_SPECIALIST",
            specialist_name=specialist_name,
            specialist_capability="UNAVAILABLE",
            specialist_status="UNAVAILABLE",
        )

    route_artifact = market_api._prop_route_artifact(req.sport, req.stat_type)
    if route_artifact.get("ok") is not True or route_artifact.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
        blocker_code = str(route_artifact.get("code") or "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND").upper()
        if blocker_code in _CAPABILITY_ABSENCE_CODES or blocker_code == "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND":
            failure_code = "MODEL_UNAVAILABLE"
            capability = "UNAVAILABLE"
            status = "ROUTE_ARTIFACT_UNAVAILABLE"
            http_status = 409
        else:
            failure_code = "MODEL_SCORER_FAILED"
            capability = "AVAILABLE"
            status = "ROUTE_ARTIFACT_LOOKUP_FAILED"
            http_status = 503
        raise _failure(
            req=req,
            status_code=http_status,
            code=failure_code,
            blocker_code=blocker_code,
            specialist_name=specialist_name,
            specialist_capability=capability,
            specialist_status=status,
            extra={"route_artifact_evidence": route_artifact},
        )

    evidence = market_api.repair_prop_evidence(
        req,
        primary_fetch=market_api.prod._prop_evidence,
        client=market_api.prod.get_client(),
    )
    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        detail = dict(evidence)
        detail["code"] = "MODEL_INPUTS_INSUFFICIENT"
        detail["blocker_code"] = str(evidence.get("code") or "RUN_INVALID_ACQUISITION_INCOMPLETE")
        detail["failure_class"] = "MODEL_INPUTS_INSUFFICIENT"
        detail["requested_scope"] = _requested_scope(req)
        detail["failed_contract_scope"] = ["CONFIDENCE"]
        detail["specialist_model_capability"] = "AVAILABLE"
        detail["specialist_model_name"] = specialist_name
        detail["specialist_model_status"] = "NOT_INVOKED_MANDATORY_INPUTS_INCOMPLETE"
        detail["probability_claim_status"] = "MODEL_INPUTS_INSUFFICIENT"
        detail["specialist_invoked"] = False
        detail["probability_publishable"] = False
        detail["governed_publishable"] = False
        detail["can_execute"] = False
        raise HTTPException(status_code=422, detail=detail)

    scored_at = datetime.now(timezone.utc).isoformat()
    inference_request = market_api._server_owned_inference_request(req, evidence, scored_at)
    features = market_api._model_features(evidence)
    try:
        inference = infer_certified_distribution(
            market_api.prod.get_client(),
            request=inference_request,
            line=req.line,
            features=features,
        )
    except (PropFittedProviderUnavailable, PropDistributionContractError) as exc:
        failure_code, capability, status, http_status = _classify_provider_failure(exc)
        raise _failure(
            req=req,
            status_code=http_status,
            code=failure_code,
            blocker_code=str(getattr(exc, "code", None) or "PROP_CERTIFIED_MODEL_SCORER_FAILED"),
            specialist_name=specialist_name,
            specialist_capability=capability,
            specialist_status=status,
        ) from exc
    except Exception as exc:
        raise _failure(
            req=req,
            status_code=503,
            code="MODEL_SCORER_FAILED",
            blocker_code="PROP_CERTIFIED_MODEL_SCORER_EXCEPTION",
            specialist_name=specialist_name,
            specialist_capability="AVAILABLE",
            specialist_status="SCORER_FAILED",
            extra={"error_type": type(exc).__name__},
        ) from exc

    if not isinstance(inference, CertifiedInference):
        raise _failure(
            req=req,
            status_code=409,
            code="MODEL_OUTPUT_INVALID",
            blocker_code="PROP_CERTIFIED_INFERENCE_INVALID",
            specialist_name=specialist_name,
            specialist_capability="AVAILABLE",
            specialist_status="OUTPUT_INVALID",
        )
    if not inference.distribution.coverage.in_distribution:
        raise _failure(
            req=req,
            status_code=422,
            code="MODEL_INPUTS_INSUFFICIENT",
            blocker_code="PROP_MODEL_OUT_OF_DISTRIBUTION",
            specialist_name=specialist_name,
            specialist_capability="AVAILABLE",
            specialist_status="ABSTAINED_OUT_OF_DISTRIBUTION",
            extra={"coverage_failures": list(inference.distribution.coverage.coverage_failures)},
        )

    try:
        line_probs = derive_line_probabilities(inference.distribution, req.line)
        raw_probability = _selected_raw_probability(line_probs, req.direction)
    except HTTPException:
        raise
    except Exception as exc:
        raise _failure(
            req=req,
            status_code=409,
            code="MODEL_OUTPUT_INVALID",
            blocker_code=str(getattr(exc, "code", None) or "PROP_LINE_PROBABILITY_DERIVATION_INVALID"),
            specialist_name=specialist_name,
            specialist_capability="AVAILABLE",
            specialist_status="OUTPUT_INVALID",
        ) from exc

    market_prior = resolve_market_prior(
        req.direction,
        market_api._to_market_quote(req.market_side_a),
        market_api._to_market_quote(req.market_side_b),
        as_of=scored_at,
    )

    calibration_health = (
        preflight.get("calibration_health_status")
        or preflight.get("calibration_status")
        or "BLOCKED"
    )
    capability = preflight.get("governed_probability_capability") or lane.get("capability_status") or "UNAVAILABLE"
    decision = resolve_lane_separation(
        specialist_available=True,
        specialist_name=specialist_name,
        specialist_output_complete=True,
        calibration_health_status=str(calibration_health),
        governed_probability_capability=str(capability),
        blockers=blockers,
        manual_lane_permitted=False,
        manual_lane_used=False,
        existing_ceiling="MODEL_QUALIFIED_HOLD",
    )

    artifact = inference.artifact
    bundle = artifact.bundle
    market_status = "PASS" if market_prior.market_prior_available and market_prior.market_prior_quality == "EXACT_TWO_WAY_NO_VIG" else "HOLD"

    return {
        "ok": True,
        **decision.as_dict(),
        "requested_scope": _requested_scope(req),
        "probability_publishable": False,
        "governed_publishable": False,
        "research_only": True,
        "research_model_output": {
            "raw_specialist_probability": raw_probability,
            "raw_probability_more": line_probs.probability_more,
            "raw_probability_less": line_probs.probability_less,
            "push_probability": line_probs.push_probability,
            "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
            "model_family": artifact.model_family,
            "model_artifact_version": bundle.model_artifact_version,
            "model_artifact_checksum": bundle.artifact_checksum,
            "specialist_version": bundle.specialist_version,
            "certification_id": bundle.certification_id,
            "distribution_type": inference.distribution.distribution_type,
            "calibrated_probability": None,
            "calibrated_probability_lower_bound": None,
            "calibrated_probability_upper_bound": None,
            "calibration_status": "UNKNOWN_OR_BLOCKED",
            "model_timestamp": scored_at,
        },
        "market_evidence": {
            "status": market_status,
            "quality": market_prior.market_prior_quality,
            "market_prior_available": market_prior.market_prior_available,
            "market_probability": market_prior.market_prior_probability,
            "reference_market_probability_raw": market_prior.reference_market_probability_raw,
            "reference_market_side": market_prior.reference_market_side,
            "reference_market_price": market_prior.reference_market_price,
            "relabeled_as_model_probability": False,
            "blocks_raw_specialist_research": False,
        },
        "acquisition_evidence": market_api.prod._visible_acquisition_evidence(evidence, req.line),
        "objective_lanes": {
            "MODEL": {"status": "PASS_RESEARCH_ONLY", "specialist_invoked": True, "can_execute": False},
            "CALIBRATION": {"status": "HOLD", "reason": "GOVERNED_CALIBRATION_BLOCKED", "can_execute": False},
            "PUBLICATION": {"status": "BLOCKED", "governed_publishable": False, "can_execute": False},
            "MARKET": {"status": market_status, "blocks_raw_specialist_research": False, "can_execute": False},
            "MONEY": {"status": "HOLD", "reason": "CALIBRATED_LOWER_BOUND_UNAVAILABLE", "can_execute": False},
        },
        "backend_traversal": {
            "requester_model": model_identity,
            "render": "PASS",
            "supabase_capability": "PUBLICATION_BLOCKED_MODEL_LANE_CONTINUES",
            "supabase_evidence": "PASS",
            "controlling_specialist": "PASS",
            "exact_route_artifact": "PASS",
            "raw_specialist_model": "PASS",
            "calibration": "BLOCKED",
            "governed_publication": "BLOCKED",
            "prediction_ledger_write": "NOT_ATTEMPTED_PUBLICATION_LOCK",
        },
        "preflight": preflight,
        "can_execute": False,
    }


def install_calibration_publication_lane_separation(app: Any, market_api: Any) -> None:
    """Replace only POST /score-prop at the outermost production app."""
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

        if lane.get("capability_status") == "AVAILABLE":
            return market_api.score_prop(req, x_wow_model_identity)

        preflight = _governed_preflight(market_api)
        blockers = list(dict.fromkeys([
            *_collect_blockers(lane.get("evidence") or {}),
            *_collect_blockers(preflight),
        ]))

        if not _publication_only(blockers):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PROP_PROBABILITY_UNAVAILABLE",
                    "requested_scope": _requested_scope(req),
                    "governed_probability_capability": lane.get("capability_status") or "UNAVAILABLE",
                    "specialist_model_capability": "NOT_EVALUATED",
                    "failed_contract_scope": ["GLOBAL"],
                    "probability_claim_status": "CALIBRATION_BLOCKED_NO_PUBLISH" if blockers else "MODEL_UNAVAILABLE",
                    "capability_evidence": lane.get("evidence") or {},
                    "preflight": preflight,
                    "blockers": blockers or ["UNCLASSIFIED_CAPABILITY_FAILURE"],
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )

        return _raw_specialist_research(
            market_api,
            req,
            model_identity=model_identity,
            lane=lane,
            preflight=preflight,
            blockers=blockers,
        )