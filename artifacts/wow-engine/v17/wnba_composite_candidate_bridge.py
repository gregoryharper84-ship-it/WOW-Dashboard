"""Evidence-only runtime bridge for WNBA P/R/A composite fitted candidates.

The bridge exposes the exact candidate artifact for immutable pregame evidence
collection. It never invents calibrated probability/bounds and cannot publish,
rank, price, certify, promote, or execute.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import HTTPException

from v17.wnba_composite_fitted_candidate import (
    CONTROLLING_SPECIALIST,
    MODEL_FAMILY,
    MIN_STANDARD_SIMULATIONS,
    WNBACompositeCandidateError,
    canonical_stat,
    simulate_exact_line,
)

ARTIFACT_FORMAT = "JSON_JOINT_EMPIRICAL_RESIDUAL_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
ALLOWED_LIFECYCLES = {"CANDIDATE", "SHADOW"}
RESEARCH_STATS = frozenset({"PRA", "POINTS_REBOUNDS", "POINTS_ASSISTS", "REBOUNDS_ASSISTS"})


class WNBACompositeCandidateBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, failure_class: str = "MODEL_SCORER_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class


def is_wnba_composite_candidate_request(req: Any) -> bool:
    if str(getattr(req, "sport", "") or "").strip().upper() != "WNBA":
        return False
    try:
        return canonical_stat(str(getattr(req, "stat_type", "") or "")) in RESEARCH_STATS
    except WNBACompositeCandidateError:
        return False


def _candidate_artifact(db: Any, *, stat_type: str) -> dict[str, Any]:
    stat = canonical_stat(stat_type)
    try:
        result = (
            db.table("wow_prop_fitted_model_artifacts")
            .select("*")
            .eq("sport", "WNBA")
            .eq("stat_type", stat)
            .eq("model_family", MODEL_FAMILY)
            .eq("feature_schema_version", FEATURE_SCHEMA_VERSION)
            .eq("candidate_research_active", True)
            .limit(2)
            .execute()
        )
    except Exception as exc:
        raise WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_CANDIDATE_ARTIFACT_REGISTRY_UNAVAILABLE",
            "WNBA composite candidate artifact registry lookup failed",
        ) from exc
    rows = [dict(row) for row in (result.data or [])]
    if not rows:
        raise WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_CANDIDATE_ARTIFACT_NOT_FOUND",
            f"No activated research candidate exists for WNBA {stat}",
            failure_class="MODEL_UNAVAILABLE",
        )
    if len(rows) != 1:
        raise WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_CANDIDATE_ARTIFACT_AMBIGUOUS",
            f"Multiple research candidates resolved for WNBA {stat}",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    row = rows[0]
    lifecycle = str(row.get("lifecycle_state") or "").strip().upper()
    blockers: list[str] = []
    if lifecycle not in ALLOWED_LIFECYCLES:
        blockers.append("WNBA_COMPOSITE_CANDIDATE_LIFECYCLE_INVALID")
    if row.get("candidate_research_active") is not True:
        blockers.append("WNBA_COMPOSITE_CANDIDATE_NOT_RESEARCH_ACTIVE")
    if any(row.get(key) is True for key in ("promoted", "active", "probability_publishable", "can_execute")):
        blockers.append("WNBA_COMPOSITE_CANDIDATE_AUTHORITY_CONFLICT")
    if str(row.get("artifact_format") or "") != ARTIFACT_FORMAT:
        blockers.append("WNBA_COMPOSITE_CANDIDATE_ARTIFACT_FORMAT_UNSUPPORTED")
    if str(row.get("specialist_version") or "").split("@", 1)[0] != CONTROLLING_SPECIALIST:
        blockers.append("WNBA_COMPOSITE_CANDIDATE_SPECIALIST_MISMATCH")
    payload = row.get("artifact_payload")
    if not isinstance(payload, Mapping):
        blockers.append("WNBA_COMPOSITE_CANDIDATE_PAYLOAD_INVALID")
    elif str(payload.get("exact_route_stat_type") or "").upper() != stat:
        blockers.append("WNBA_COMPOSITE_CANDIDATE_ROUTE_IDENTITY_MISMATCH")
    if blockers:
        raise WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_CANDIDATE_ARTIFACT_INVALID",
            ",".join(blockers),
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return row


def candidate_preflight(market_api: Any, sport: Any, stat_type: Any, production_route: Any) -> dict[str, Any] | None:
    if str(sport or "").strip().upper() != "WNBA":
        return None
    try:
        stat = canonical_stat(str(stat_type or ""))
    except WNBACompositeCandidateError:
        return None
    if stat not in RESEARCH_STATS:
        return None
    try:
        artifact = _candidate_artifact(market_api.prod.get_client(), stat_type=stat)
    except WNBACompositeCandidateBridgeError:
        return None
    original = dict(production_route) if isinstance(production_route, dict) else {}
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "preflight_compatibility_mode": "WNBA_COMPOSITE_RESEARCH_EVIDENCE_ONLY",
        "actual_artifact_lifecycle": str(artifact.get("lifecycle_state") or "").upper(),
        "actual_certification_status": "CANDIDATE_ONLY",
        "original_production_route_code": original.get("code"),
        "artifact_id": artifact.get("artifact_id"),
        "model_family": MODEL_FAMILY,
        "model_artifact_version": artifact.get("model_artifact_version"),
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _http_failure(req: Any, exc: WNBACompositeCandidateBridgeError) -> HTTPException:
    status = 409
    if exc.failure_class == "MODEL_INPUTS_INSUFFICIENT":
        status = 422
    elif exc.failure_class == "MODEL_SCORER_FAILED":
        status = 503
    return HTTPException(status_code=status, detail={
        "code": exc.failure_class,
        "blocker_code": exc.code,
        "requested_scope": {
            "sport": str(getattr(req, "sport", "") or "").upper(),
            "stat_type": str(getattr(req, "stat_type", "") or "").upper(),
        },
        "specialist_model_capability": "UNAVAILABLE" if exc.failure_class == "MODEL_UNAVAILABLE" else "AVAILABLE",
        "specialist_model_name": CONTROLLING_SPECIALIST,
        "specialist_model_status": exc.failure_class,
        "probability_claim_status": exc.failure_class,
        "probability_publishable": False,
        "governed_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    })


def score_wnba_composite_candidate_research(market_api: Any, req: Any, *, model_identity: str) -> dict[str, Any]:
    if not is_wnba_composite_candidate_request(req):
        raise WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_ROUTE_UNSUPPORTED", "Request is not a declared WNBA composite candidate route"
        )
    stat = canonical_stat(str(getattr(req, "stat_type", "") or ""))
    try:
        evidence = market_api.repair_prop_evidence(
            req,
            primary_fetch=market_api.prod._prop_evidence,
            client=market_api.prod.get_client(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_failure(req, WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_EVIDENCE_BRIDGE_FAILED",
            "WNBA composite evidence retrieval failed",
            failure_class="MODEL_SCORER_FAILED",
        )) from exc
    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        raise _http_failure(req, WNBACompositeCandidateBridgeError(
            str(evidence.get("code") or "WNBA_COMPOSITE_EVIDENCE_INCOMPLETE"),
            "WNBA composite candidate inputs are incomplete",
            failure_class="MODEL_INPUTS_INSUFFICIENT",
        ))

    try:
        artifact = _candidate_artifact(market_api.prod.get_client(), stat_type=stat)
        payload = artifact.get("artifact_payload") or {}
        output = simulate_exact_line(
            payload,
            player=str(getattr(req, "player", "") or "").strip(),
            stat_type=stat,
            exact_line=float(getattr(req, "line")),
            side=str(getattr(req, "direction", "")),
            simulation_count=MIN_STANDARD_SIMULATIONS,
            seed=int(getattr(req, "seed", 0) or 0),
        )
        output.update({
            "candidate_family": "WNBA_COMPOSITE",
            "model_version": str(artifact.get("model_artifact_version") or ""),
            "model_source_sha256": str(artifact.get("training_dataset_hash") or "").lower(),
            "model_artifact_checksum": str(artifact.get("artifact_checksum") or "").lower(),
            "model_lifecycle_state": str(artifact.get("lifecycle_state") or "").upper(),
            "model_timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except WNBACompositeCandidateBridgeError as exc:
        raise _http_failure(req, exc) from exc
    except WNBACompositeCandidateError as exc:
        raise _http_failure(req, WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_CANDIDATE_SCORER_INVALID",
            str(exc),
            failure_class="MODEL_OUTPUT_INVALID",
        )) from exc
    except Exception as exc:
        raise _http_failure(req, WNBACompositeCandidateBridgeError(
            "WNBA_COMPOSITE_CANDIDATE_SCORER_EXCEPTION",
            "WNBA composite candidate scorer threw an unexpected exception",
            failure_class="MODEL_SCORER_FAILED",
        )) from exc

    return {
        "ok": True,
        "research_only": True,
        "candidate_evidence_collection_eligible": True,
        "candidate_model_output": output,
        "requested_scope": {"sport": "WNBA", "stat_type": stat},
        "objective_lanes": {
            "MODEL": {"status": "PASS_RESEARCH_ONLY", "specialist_invoked": True, "can_execute": False},
            "CALIBRATION": {"status": "HOLD", "reason": "WNBA_COMPOSITE_CANDIDATE_UNCALIBRATED", "can_execute": False},
            "PUBLICATION": {"status": "BLOCKED", "governed_publishable": False, "can_execute": False},
            "MONEY": {"status": "HOLD", "reason": "CALIBRATED_LOWER_BOUND_UNAVAILABLE", "can_execute": False},
        },
        "backend_traversal": {
            "requester_model": model_identity,
            "render": "PASS",
            "supabase_evidence": "PASS",
            "controlling_specialist": "PASS",
            "candidate_artifact": "PASS",
            "candidate_raw_model": "PASS",
            "calibration": "NOT_CERTIFIED",
            "governed_publication": "BLOCKED",
            "prediction_ledger_write": "NOT_ATTEMPTED_BY_SCORER",
        },
        "probability_publishable": False,
        "governed_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "RESEARCH_STATS", "WNBACompositeCandidateBridgeError", "candidate_preflight",
    "is_wnba_composite_candidate_request", "score_wnba_composite_candidate_research",
]
