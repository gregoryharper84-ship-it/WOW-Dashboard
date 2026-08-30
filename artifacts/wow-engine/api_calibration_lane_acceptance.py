"""Production activation wrapper for calibration/publication lane separation.

WOW-PATCH-2026-08-30-CALIBRATION-PUBLICATION-LANE-SEPARATION.

A sport/market runtime capability and the global governed publication latch are
separate facts. In production today, for example, PROP_PROBABILITY can be
AVAILABLE while governed probability publication is UNAVAILABLE because
Calibration Health is blocked by FORWARD_SHADOW_NOT_COMPLETED.

This wrapper keeps every fail-closed model/identity/evidence rule, but lets a
prospectively certified specialist produce an auditable RAW research result
when the proven blocker scope is calibration/publication only. It never calls a
calibrator on that path, never creates calibrated bounds, never relabels market
probability as model probability, never writes the result to the governed
probability ledger, and never authorizes execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, Header, HTTPException

import api_ncaaf_acceptance as ncaaf_api
from calibration_publication_scope import classify_probability_capability
from market import resolve_market_prior
from prop_discrete_engine import PropCalibrationUnavailable, _directional_probability
from prop_distribution_contract import PropDistributionContractError, derive_line_probabilities
from prop_fitted_provider import CertifiedInference, PropFittedProviderUnavailable, infer_certified_distribution

app = ncaaf_api.app
market_api = ncaaf_api.base.market_api
prod = market_api.prod

_PATCH_ID = "WOW-PATCH-2026-08-30-CALIBRATION-PUBLICATION-LANE-SEPARATION"
_original_runtime_capability = prod._runtime_capability
_original_score_prop = market_api.score_prop


def _global_publication_state() -> tuple[dict[str, Any], dict[str, Any]]:
    gate = prod.base_api._query_deployment_gate_state()
    health = prod.base_api._query_calibration_health()
    return (dict(gate) if isinstance(gate, dict) else {}, dict(health) if isinstance(health, dict) else {})


def _capability_context(capability_key: str) -> dict[str, Any]:
    """Join lane capability with global publication/calibration evidence.

    No state is upgraded here. Missing global evidence stays unavailable and
    therefore cannot use the publication-only exception unless its scope is
    affirmatively proven by blocker evidence.
    """
    row = dict(_original_runtime_capability(capability_key))
    gate, health = _global_publication_state()
    evidence = dict(row.get("evidence") or {})

    health_blockers = health.get("blockers")
    if isinstance(health_blockers, list):
        evidence["global_calibration_blockers"] = list(health_blockers)

    publication_blockers: list[str] = []
    if str(gate.get("ratification_status") or "NOT_RATIFIED") != "RATIFIED":
        publication_blockers.append("PUBLICATION_NOT_RATIFIED")
    if gate.get("production_feature_ready") is False:
        publication_blockers.append("PRODUCTION_FEATURE_READY_FALSE")
    if gate.get("probability_publishable") is False:
        publication_blockers.append("GOVERNED_PROBABILITY_NOT_PUBLISHABLE")
    if publication_blockers:
        evidence["global_publication_blockers"] = publication_blockers

    row["evidence"] = evidence
    row["global_governed_probability_capability"] = str(
        gate.get("governed_probability_capability") or "UNAVAILABLE"
    )
    row["governed_probability_capability"] = row["global_governed_probability_capability"]
    row["governed_publishable"] = bool(gate.get("probability_publishable", False))
    row["calibration_health_status"] = str(
        health.get("calibration_health_status")
        or gate.get("calibration_health_status")
        or "UNAVAILABLE"
    )
    row["calibration_health_assessed_at"] = health.get("assessed_at") or gate.get("calibration_health_assessed_at")
    return row


def _scoped_runtime_capability(capability_key: str) -> dict[str, Any]:
    """Compatibility mapper preserving model routing while blocking publication.

    Existing batch preflight checks ``capability_status == AVAILABLE``. For an
    explicitly calibration/publication-only lock we expose AVAILABLE on that
    legacy routing key *only*. The true lane/source state plus the global
    publication state remain first-class fields and govern all claim labels.
    Unknown/global/model failures retain their hard-stop semantics.
    """
    row = _capability_context(capability_key)
    separation = classify_probability_capability(row)
    row["source_capability_status"] = separation.source_capability_status
    row["routing_capability_status"] = separation.routing_capability_status
    row["specialist_model_capability"] = separation.specialist_model_capability
    row["calibration_capability"] = separation.calibration_capability
    row["governed_publication_capability"] = separation.governed_publication_capability
    row["governed_publishable"] = separation.governed_publishable
    row["failed_contract_scope"] = list(separation.failed_contract_scope)
    row["publication_only_lock"] = separation.publication_only_lock
    row["blocker_codes"] = list(separation.blocker_codes)
    if separation.publication_only_lock:
        # Compatibility alias for PRE-MODEL routing only. It is not publication
        # availability and must never be used as such.
        row["capability_status"] = "AVAILABLE"
        row["capability_status_semantics"] = "RESEARCH_ROUTING_COMPATIBILITY_ALIAS"
    return row


# The already-installed /score-pick-request closure resolves this function at
# request time, so batch/screenshot ingress and single-row scoring share one
# capability mapper.
prod._runtime_capability = _scoped_runtime_capability


def _research_market_lane(req: Any, scored_at: str) -> dict[str, Any]:
    market_prior = resolve_market_prior(
        req.direction,
        market_api._to_market_quote(req.market_side_a),
        market_api._to_market_quote(req.market_side_b),
        as_of=scored_at,
    )
    status = (
        "PASS"
        if market_prior.market_prior_available
        and market_prior.market_prior_quality == "EXACT_TWO_WAY_NO_VIG"
        else "HOLD"
    )
    return {
        "status": status,
        "quality": market_prior.market_prior_quality,
        "market_prior_available": market_prior.market_prior_available,
        "market_implied_probability": market_prior.market_prior_probability,
        "reference_market_probability_raw": market_prior.reference_market_probability_raw,
        "reference_market_side": market_prior.reference_market_side,
        "reference_market_price": market_prior.reference_market_price,
        "market_probability_role": "MARKET_EVIDENCE_ONLY_NOT_MODEL_PROBABILITY",
        "market_prior_weight": 0.0,
        "blocks_model_probability": False,
        "can_execute": False,
    }


def _raw_specialist_evidence(*, req: Any, evidence: dict[str, Any], scored_at: str) -> dict[str, Any]:
    inference_request = market_api._server_owned_inference_request(req, evidence, scored_at)
    inference = infer_certified_distribution(
        prod.get_client(),
        request=inference_request,
        line=req.line,
        features=market_api._model_features(evidence),
    )
    if not isinstance(inference, CertifiedInference):
        raise PropCalibrationUnavailable(
            "PROP_CERTIFIED_INFERENCE_INVALID",
            "Fitted provider must return CertifiedInference with artifact provenance.",
        )
    distribution = inference.distribution
    if not distribution.coverage.in_distribution:
        reasons = ",".join(distribution.coverage.coverage_failures) or "UNSPECIFIED_OOD"
        raise PropCalibrationUnavailable(
            "PROP_MODEL_OUT_OF_DISTRIBUTION",
            f"Certified provider abstained for this candidate: {reasons}",
        )

    line_probs = derive_line_probabilities(distribution, req.line)
    raw_probability = _directional_probability(line_probs, req.direction)
    artifact = inference.artifact
    bundle = artifact.bundle
    return {
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": artifact.model_family,
        "model_artifact_version": bundle.model_artifact_version,
        "model_artifact_checksum": bundle.artifact_checksum,
        "model_bundle_fingerprint": bundle.bundle_fingerprint,
        "model_lifecycle_state": bundle.lifecycle_state,
        "feature_schema_version": bundle.feature_schema_version,
        "feature_transform_version": bundle.feature_transform_version,
        "specialist_version": bundle.specialist_version,
        "certification_id": bundle.certification_id,
        "distribution_type": distribution.distribution_type,
        "probability_more": line_probs.probability_more,
        "probability_less": line_probs.probability_less,
        "push_probability": line_probs.push_probability,
        "raw_model_probability": raw_probability,
        "selected_side": str(req.direction).upper(),
        "coverage": {
            "in_distribution": distribution.coverage.in_distribution,
            "ood_score": distribution.coverage.ood_score,
            "coverage_failures": list(distribution.coverage.coverage_failures),
        },
        "training_rows": artifact.training_rows,
        "model_timestamp": scored_at,
        # No calibration is invoked on this path.
        "calibrated_probability": None,
        "calibrated_probability_lower_bound": None,
        "calibrated_probability_upper_bound": None,
        "calibration_method": None,
        "bounds_method_version": None,
        "probability_claim_status": "SPECIALIST_RAW_RESEARCH_ONLY",
        "probability_publishable": False,
        "governed_publishable": False,
        "can_execute": False,
    }


def _publication_lock_response(req: Any, *, model_identity: str, lane: dict[str, Any]) -> dict[str, Any]:
    separation = classify_probability_capability(lane)
    if not separation.publication_only_lock:
        raise RuntimeError("publication-lock response requested for a non-publication-only capability")

    specialist, route_artifact = market_api._preflight_prop_route(
        req,
        model_identity=model_identity,
        lane=lane,
    )

    evidence = market_api.repair_prop_evidence(
        req,
        primary_fetch=prod._prop_evidence,
        client=prod.get_client(),
    )
    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        detail = dict(evidence)
        detail.setdefault("code", "RUN_INVALID_ACQUISITION_INCOMPLETE")
        detail["failure_class"] = "RUN_INVALID_ACQUISITION_INCOMPLETE"
        detail["failed_contract_scope"] = ["CONFIDENCE"]
        detail["route_preflight"] = "PASS_RESEARCH_ONLY"
        detail["exact_route_artifact"] = route_artifact.get("code")
        detail["controlling_specialist"] = specialist.get("controlling_specialist")
        detail["specialist_invoked"] = False
        detail["probability_publishable"] = False
        detail["governed_publishable"] = False
        detail["terminal_ceiling"] = "RESEARCH_INTEREST"
        detail["can_execute"] = False
        raise HTTPException(status_code=422, detail=detail)

    scored_at = datetime.now(timezone.utc).isoformat()
    try:
        model_evidence = _raw_specialist_evidence(req=req, evidence=evidence, scored_at=scored_at)
    except (PropFittedProviderUnavailable, PropDistributionContractError, PropCalibrationUnavailable) as exc:
        # Genuine specialist/model/provider/coverage failure: preserve existing
        # MODEL_UNAVAILABLE semantics and never substitute market/L10/manual data.
        market_api._raise_model_path_error(exc)
        raise AssertionError("unreachable")

    market_lane = _research_market_lane(req, scored_at)
    effective_snapshot_id = str(evidence.get("source_snapshot_id") or req.source_snapshot_id)
    blockers = list(separation.blocker_codes)
    if "FORWARD_SHADOW_NOT_COMPLETED" not in blockers:
        blockers.append("CALIBRATION_PUBLICATION_BLOCKED")

    return {
        "ok": True,
        "research_only": True,
        "prediction": None,
        "specialist_research": model_evidence,
        "model_evidence": model_evidence,
        "acquisition_evidence": prod._visible_acquisition_evidence(evidence, req.line),
        "acquisition_repair": {
            "status": evidence.get("acquisition_repair_status"),
            "requested_source_snapshot_id": evidence.get("requested_source_snapshot_id") or str(req.source_snapshot_id),
            "effective_source_snapshot_id": evidence.get("effective_source_snapshot_id") or effective_snapshot_id,
            "attempts": evidence.get("acquisition_attempts") or [],
            "can_execute": False,
        },
        "specialist_model_capability": "AVAILABLE",
        "specialist_model_name": specialist.get("controlling_specialist"),
        "specialist_model_status": "COMPLETED_RESEARCH_ONLY",
        "calibration_health_status": lane.get("calibration_health_status") or "BLOCKED_OR_UNKNOWN",
        "calibration_status": "UNKNOWN_OR_BLOCKED",
        "governed_probability_capability": "UNAVAILABLE",
        "governed_publication_capability": "UNAVAILABLE",
        "governed_publishable": False,
        "manual_lane_used": False,
        "manual_confidence_cap": None,
        "failed_contract_scope": list(separation.failed_contract_scope),
        "probability_claim_status": "SPECIALIST_RAW_RESEARCH_ONLY",
        "terminal_ceiling": "MODEL_QUALIFIED_HOLD",
        "blockers": blockers,
        "objective_lanes": {
            "MODEL": {
                "status": "PASS_RESEARCH_ONLY",
                "specialist_model_capability": "AVAILABLE",
                "probability_claim_status": "SPECIALIST_RAW_RESEARCH_ONLY",
                "can_execute": False,
            },
            "CALIBRATION": {
                "status": "BLOCKED",
                "calibration_status": "UNKNOWN_OR_BLOCKED",
                "failed_contract_scope": ["CALIBRATION"],
                "can_execute": False,
            },
            "PUBLICATION": {
                "status": "BLOCKED",
                "governed_publishable": False,
                "failed_contract_scope": ["PUBLICATION"],
                "can_execute": False,
            },
            "MARKET": market_lane,
            "MONEY": {
                "status": "HOLD",
                "money_lane_status": req.money_lane_status,
                "blocks_model_probability": False,
                "can_execute": False,
            },
        },
        "backend_traversal": {
            "requester_model": model_identity,
            "render": "PASS",
            "supabase_capability": "PASS_SCOPED_PUBLICATION_LOCK",
            "supabase_evidence": "PASS",
            "controlling_specialist": "PASS",
            "exact_route_artifact": "PASS",
            "raw_specialist_model": "PASS",
            "dynamic_calibration": "NOT_INVOKED_PUBLICATION_LOCK",
            "governed_probability_publication": "BLOCKED",
            "prediction_ledger_write": "NOT_ATTEMPTED_PUBLICATION_BLOCKED",
        },
        "route_preflight": {
            "status": "PASS_RESEARCH_ONLY",
            "certified_artifact_code": route_artifact.get("code"),
            "source_capability_status": separation.source_capability_status,
            "global_governed_probability_capability": separation.global_governed_probability_capability,
            "routing_capability_status": separation.routing_capability_status,
            "can_execute": False,
        },
        "blocker_message": (
            "Controlling specialist completed, but calibration/governed publication is blocked. "
            "Raw specialist probability is research-only; no calibrated probability, bounds, money label, or final approval is emitted."
        ),
        "model_path": "WOW_PROP_FITTED_MODEL_V1->RAW_DISCRETE_DISTRIBUTION->RESEARCH_ONLY_HOLD",
        "probability_publishable": False,
        "can_execute": False,
    }


def score_prop(req: Any, x_wow_model_identity: Optional[str] = None):
    """Publish normally only when publication is available; otherwise preserve research."""
    model_identity = prod._reject_llp_prop_identity(x_wow_model_identity)
    lane = prod._runtime_capability(prod.PROP_CAPABILITY_KEY)
    separation = classify_probability_capability(lane)
    if separation.publication_only_lock:
        return _publication_lock_response(req, model_identity=model_identity, lane=lane)
    return _original_score_prop(req, x_wow_model_identity=x_wow_model_identity)


# The canonical /score-pick-request closure resolves market_api.score_prop at
# request time, so this assignment gives batch ingress the exact same behavior.
market_api.score_prop = score_prop


# Replace the inherited HTTP /score-prop route with the lane-separated boundary.
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
    dependencies=[Depends(prod._require_action_api_key)],
    operation_id="scoreWowProp",
)
def score_prop_http(
    req: market_api.ScorePropRequest,
    x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
):
    return score_prop(req, x_wow_model_identity=x_wow_model_identity)


# Replace governance so a routing compatibility alias cannot be mistaken for
# governed publication availability by any caller.
app.router.routes[:] = [
    route
    for route in app.router.routes
    if not (
        getattr(route, "path", None) == "/governance"
        and "GET" in (getattr(route, "methods", set()) or set())
    )
]


@app.get("/governance")
def governance():
    payload = dict(prod.governance())
    lane_capabilities = dict(payload.get("lane_capabilities") or {})
    for capability_key in (prod.MLB_EVENT_CAPABILITY_KEY, prod.PROP_CAPABILITY_KEY):
        source = _capability_context(capability_key)
        separation = classify_probability_capability(source)
        existing = dict(lane_capabilities.get(capability_key) or {})
        existing.update(
            {
                "status": separation.source_capability_status,
                "global_governed_probability_capability": separation.global_governed_probability_capability,
                "routing_capability_status": separation.routing_capability_status,
                "specialist_model_capability": separation.specialist_model_capability,
                "calibration_capability": separation.calibration_capability,
                "governed_publication_capability": separation.governed_publication_capability,
                "governed_publishable": separation.governed_publishable,
                "calibration_health_status": source.get("calibration_health_status"),
                "failed_contract_scope": list(separation.failed_contract_scope),
                "blocker_codes": list(separation.blocker_codes),
                "publication_only_lock": separation.publication_only_lock,
                "can_execute": False,
            }
        )
        lane_capabilities[capability_key] = existing
    payload["lane_capabilities"] = lane_capabilities
    payload["calibration_publication_lane_separation"] = {
        "patch_id": _PATCH_ID,
        "status": "ACTIVE",
        "model_unavailable_reserved_for_actual_specialist_failure": True,
        "market_probability_may_be_model_probability": False,
        "can_execute": False,
    }
    payload["can_execute"] = False
    return payload


# This wrapper installs final replacement routes after the inherited OpenAPI
# schema may have been built, so force a fresh schema.
app.openapi_schema = None
