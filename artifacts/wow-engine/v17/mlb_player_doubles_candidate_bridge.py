"""Evidence-only runtime bridge for the MLB player-doubles fitted challenger.

The exact route is intentionally research-only. It may invoke a fitted challenger
and collect forward evidence, but it cannot publish/rank/execute a probability.
A future certified production artifact always takes precedence at the outer route.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from fastapi import HTTPException

from v17.mlb_player_doubles_candidate import (
    ARTIFACT_FORMAT,
    CONTROLLING_SPECIALIST,
    FEATURE_SCHEMA_VERSION,
    MLBPlayerDoublesCandidateError,
    MODEL_FAMILY,
    SPORT,
    STAT_TYPE,
    score_candidate,
    validate_artifact_payload,
)

ALLOWED_LIFECYCLES = {"CANDIDATE", "SHADOW"}


class MLBPlayerDoublesBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, failure_class: str = "MODEL_SCORER_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class


def is_mlb_player_doubles_candidate_request(req: Any) -> bool:
    return (
        str(getattr(req, "sport", "") or "").strip().upper() == SPORT
        and str(getattr(req, "stat_type", "") or "").strip().upper() == STAT_TYPE
    )


def _candidate_artifact(db: Any) -> dict[str, Any]:
    try:
        result = (
            db.table("wow_prop_fitted_model_artifacts")
            .select("*")
            .eq("sport", SPORT)
            .eq("stat_type", STAT_TYPE)
            .eq("model_family", MODEL_FAMILY)
            .eq("feature_schema_version", FEATURE_SCHEMA_VERSION)
            .eq("candidate_research_active", True)
            .limit(2)
            .execute()
        )
    except Exception as exc:
        raise MLBPlayerDoublesBridgeError(
            "MLB_DOUBLES_CANDIDATE_ARTIFACT_REGISTRY_UNAVAILABLE",
            "MLB doubles candidate artifact registry lookup failed",
        ) from exc
    rows = [dict(row) for row in (getattr(result, "data", None) or [])]
    if not rows:
        raise MLBPlayerDoublesBridgeError(
            "MLB_DOUBLES_CANDIDATE_ARTIFACT_NOT_FOUND",
            "No activated MLB player-doubles research candidate exists",
            failure_class="MODEL_UNAVAILABLE",
        )
    if len(rows) != 1:
        raise MLBPlayerDoublesBridgeError(
            "MLB_DOUBLES_CANDIDATE_ARTIFACT_AMBIGUOUS",
            "Multiple activated MLB player-doubles candidates resolved",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    row = rows[0]
    lifecycle = str(row.get("lifecycle_state") or "").strip().upper()
    blockers: list[str] = []
    if lifecycle not in ALLOWED_LIFECYCLES:
        blockers.append("MLB_DOUBLES_CANDIDATE_LIFECYCLE_INVALID")
    if row.get("candidate_research_active") is not True:
        blockers.append("MLB_DOUBLES_CANDIDATE_NOT_RESEARCH_ACTIVE")
    if any(row.get(key) is True for key in ("promoted", "active", "probability_publishable", "can_execute")):
        blockers.append("MLB_DOUBLES_CANDIDATE_AUTHORITY_CONFLICT")
    if str(row.get("artifact_format") or "") != ARTIFACT_FORMAT:
        blockers.append("MLB_DOUBLES_CANDIDATE_ARTIFACT_FORMAT_UNSUPPORTED")
    if str(row.get("specialist_version") or "").split("@", 1)[0] != CONTROLLING_SPECIALIST:
        blockers.append("MLB_DOUBLES_CANDIDATE_SPECIALIST_MISMATCH")
    payload = row.get("artifact_payload")
    if not isinstance(payload, Mapping):
        blockers.append("MLB_DOUBLES_CANDIDATE_PAYLOAD_INVALID")
    else:
        try:
            validate_artifact_payload(payload)
        except MLBPlayerDoublesCandidateError as exc:
            blockers.append(exc.code)
    if blockers:
        raise MLBPlayerDoublesBridgeError(
            "MLB_DOUBLES_CANDIDATE_ARTIFACT_INVALID",
            ",".join(dict.fromkeys(blockers)),
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return row


def candidate_preflight(market_api: Any, sport: Any, stat_type: Any, production_route: Any) -> dict[str, Any] | None:
    if str(sport or "").strip().upper() != SPORT or str(stat_type or "").strip().upper() != STAT_TYPE:
        return None
    if isinstance(production_route, dict) and production_route.get("ok") is True:
        return None
    try:
        artifact = _candidate_artifact(market_api.prod.get_client())
    except MLBPlayerDoublesBridgeError:
        return None
    original = dict(production_route) if isinstance(production_route, dict) else {}
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "preflight_compatibility_mode": "MLB_PLAYER_DOUBLES_RESEARCH_EVIDENCE_ONLY",
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


def _http_failure(req: Any, exc: MLBPlayerDoublesBridgeError) -> HTTPException:
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


def score_mlb_player_doubles_candidate_research(market_api: Any, req: Any, *, model_identity: str) -> dict[str, Any]:
    if not is_mlb_player_doubles_candidate_request(req):
        raise MLBPlayerDoublesBridgeError("MLB_DOUBLES_ROUTE_UNSUPPORTED", "request is not MLB PLAYER_DOUBLES")
    try:
        evidence = market_api.repair_prop_evidence(
            req,
            primary_fetch=market_api.prod._prop_evidence,
            client=market_api.prod.get_client(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_failure(req, MLBPlayerDoublesBridgeError(
            "MLB_DOUBLES_EVIDENCE_BRIDGE_FAILED",
            "MLB doubles evidence retrieval failed",
            failure_class="MODEL_SCORER_FAILED",
        )) from exc
    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        raise _http_failure(req, MLBPlayerDoublesBridgeError(
            str(evidence.get("code") or "MLB_DOUBLES_EVIDENCE_INCOMPLETE"),
            "MLB doubles candidate inputs are incomplete",
            failure_class="MODEL_INPUTS_INSUFFICIENT",
        ))

    try:
        artifact = _candidate_artifact(market_api.prod.get_client())
        output = score_candidate(
            artifact["artifact_payload"],
            player=str(getattr(req, "player", "") or "").strip(),
            line=float(getattr(req, "line")),
            direction=str(getattr(req, "direction", "")),
        )
        output.update({
            "model_version": str(artifact.get("model_artifact_version") or ""),
            "model_source_sha256": str(artifact.get("training_dataset_hash") or "").lower(),
            "model_artifact_checksum": str(artifact.get("artifact_checksum") or "").lower(),
            "model_lifecycle_state": str(artifact.get("lifecycle_state") or "").upper(),
            "model_timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except MLBPlayerDoublesBridgeError as exc:
        raise _http_failure(req, exc) from exc
    except MLBPlayerDoublesCandidateError as exc:
        failure_class = "MODEL_INPUTS_INSUFFICIENT" if exc.code in {
            "MLB_DOUBLES_LINE_OUT_OF_DOMAIN", "PROP_DIRECTION_INVALID", "PROP_PLAYER_IDENTITY_UNRESOLVED"
        } else "MODEL_OUTPUT_INVALID"
        raise _http_failure(req, MLBPlayerDoublesBridgeError(exc.code, str(exc), failure_class=failure_class)) from exc
    except Exception as exc:
        raise _http_failure(req, MLBPlayerDoublesBridgeError(
            "MLB_DOUBLES_CANDIDATE_SCORER_EXCEPTION",
            "MLB doubles candidate scorer threw an unexpected exception",
            failure_class="MODEL_SCORER_FAILED",
        )) from exc

    return {
        "ok": True,
        "research_only": True,
        "candidate_evidence_collection_eligible": True,
        "candidate_model_output": output,
        "requested_scope": {"sport": SPORT, "stat_type": STAT_TYPE},
        "objective_lanes": {
            "MODEL": {"status": "PASS_RESEARCH_ONLY", "specialist_invoked": True, "can_execute": False},
            "CALIBRATION": {"status": "HOLD", "reason": "MLB_PLAYER_DOUBLES_FORWARD_CALIBRATION_REQUIRED", "can_execute": False},
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
            "calibration": "NOT_FORWARD_CERTIFIED",
            "governed_publication": "BLOCKED",
            "prediction_ledger_write": "NOT_ATTEMPTED_BY_SCORER",
        },
        "probability_publishable": False,
        "governed_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "MLBPlayerDoublesBridgeError",
    "candidate_preflight",
    "is_mlb_player_doubles_candidate_request",
    "score_mlb_player_doubles_candidate_research",
]
