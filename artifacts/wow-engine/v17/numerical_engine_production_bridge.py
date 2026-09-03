"""Production bridge from fitted V17 specialists into the universal numerical engine.

This module never creates a sporting probability. It wraps probability already
computed by the exact controlling specialist, validates the numerical contract,
and adds a uniform auditable envelope. Missing metadata is reported as an
attestation status and never converts a completed model result to MODEL_UNAVAILABLE.

The bridge is intentionally side-effect free until ``install_production_bridges``
is called by the active production entrypoint. can_execute remains false.
"""
from __future__ import annotations

from dataclasses import asdict
from math import isfinite
from typing import Any, Mapping

from v17.certified_numerical_engine import (
    ModelFamily,
    NumericalComputationResult,
    V17Lane,
    VerificationStatus,
)

CAN_EXECUTE = False


def _finite_probability(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) and 0.0 <= parsed <= 1.0 else None


def _serialize(result: NumericalComputationResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["lane"] = result.lane.value
    payload["model_family"] = result.model_family.value
    payload["verification_status"] = result.verification_status.value
    payload["governance_role"] = "NUMERICAL_CERTIFICATION_ONLY"
    payload["creates_sporting_probability"] = False
    payload["can_execute"] = False
    return payload


def certify_native_probability(
    *,
    candidate_id: str,
    lane: V17Lane,
    sport: str,
    market_or_stat: str,
    controlling_specialist: str,
    model_version: str,
    model_family: ModelFamily,
    probability: float,
    computation_method: str,
    computation_version: str,
    feature_vector_version: str | None = None,
    simulation_count: int | None = None,
    random_seed: int | None = None,
    convergence_status: str | None = None,
    distribution_diagnostics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Certify one probability already produced by a controlling specialist."""
    value = _finite_probability(probability)
    if value is None:
        return {
            "status": "MODEL_OUTPUT_INVALID",
            "code": "MODEL_OUTPUT_INVALID",
            "detail": "native_probability_out_of_bounds",
            "creates_sporting_probability": False,
            "can_execute": False,
        }
    required = {
        "candidate_id": candidate_id,
        "sport": sport,
        "market_or_stat": market_or_stat,
        "controlling_specialist": controlling_specialist,
        "model_version": model_version,
        "computation_method": computation_method,
        "computation_version": computation_version,
    }
    missing = sorted(key for key, item in required.items() if not str(item or "").strip())
    if missing:
        return {
            "status": "NOT_ATTESTED_METADATA_INSUFFICIENT",
            "missing_metadata": missing,
            "native_probability_preserved": value,
            "creates_sporting_probability": False,
            "can_execute": False,
        }
    result = NumericalComputationResult(
        candidate_id=str(candidate_id),
        lane=lane,
        sport=str(sport).upper(),
        market_or_stat=str(market_or_stat),
        controlling_specialist=str(controlling_specialist),
        model_version=str(model_version),
        model_family=model_family,
        computation_engine="PYTHON_PRIMARY",
        computation_method=str(computation_method),
        computation_version=str(computation_version),
        feature_vector_version=feature_vector_version,
        raw_probability=value,
        unconditional_probability=value,
        simulation_count=simulation_count,
        random_seed=random_seed,
        convergence_status=convergence_status,
        distribution_diagnostics=dict(distribution_diagnostics or {}),
        verification_status=VerificationStatus.NOT_REQUIRED,
    )
    result.validate_probability_contract()
    return {"status": "PASS", "numerical_result": _serialize(result), "can_execute": False}


def _prop_native_metadata(result: Any, *, direction: str, probability: float, seed: int) -> dict[str, Any]:
    inference = getattr(result, "inference", None)
    artifact = getattr(inference, "artifact", None)
    bundle = getattr(artifact, "bundle", None)
    row = getattr(result, "row", None)
    request = getattr(inference, "request", None)
    sport = getattr(request, "sport", None) or getattr(row, "sport", None) or "UNKNOWN"
    stat = getattr(request, "stat_type", None) or getattr(row, "stat_type", None) or "UNKNOWN"
    model_version = getattr(bundle, "model_artifact_version", None) or getattr(artifact, "model_artifact_version", None) or "UNKNOWN"
    native_family = getattr(artifact, "model_family", None) or "DISCRETE_PMF"
    candidate_id = getattr(row, "prediction_id", None) or getattr(row, "row_key", None) or f"{sport}:{stat}:{direction}"
    diagnostics = {
        "native_model_family": str(native_family),
        "direction": direction,
        "distribution_contract": "CERTIFIED_DISCRETE_PMF",
    }
    return certify_native_probability(
        candidate_id=str(candidate_id),
        lane=V17Lane.PROP,
        sport=str(sport),
        market_or_stat=str(stat),
        controlling_specialist="WOW_PROP_FITTED_MODEL_V1",
        model_version=str(model_version),
        model_family=ModelFamily.DISCRETE_PMF,
        probability=probability,
        computation_method="CERTIFIED_DISCRETE_PMF_NATIVE",
        computation_version="V17_PROP_PMF_BRIDGE_V1",
        random_seed=seed,
        distribution_diagnostics=diagnostics,
    )


def attach_prop_numerical_certification(result: Any, *, seed: int = 0) -> Any:
    """Attach MORE/LESS certifications without changing fitted distribution output."""
    lp = getattr(result, "line_probabilities", None)
    if lp is None:
        return result
    more = _finite_probability(getattr(lp, "probability_more", None))
    less = _finite_probability(getattr(lp, "probability_less", None))
    if more is None or less is None:
        return result
    certifications = {
        "MORE": _prop_native_metadata(result, direction="MORE", probability=more, seed=seed),
        "LESS": _prop_native_metadata(result, direction="LESS", probability=less, seed=seed),
    }
    try:
        object.__setattr__(result, "v17_numerical_engine", certifications)
        return result
    except Exception:
        # Existing V17 response wrapper already returns a mutable namespace in the
        # active runtime. If a lower-level frozen result reaches this seam, leave it
        # untouched rather than mutating model semantics.
        return result


def attach_team_event_numerical_certification(payload: dict[str, Any], *, req: Any) -> dict[str, Any]:
    """Attach side-specific certification to completed team/event probability."""
    out = dict(payload)
    home = _finite_probability(out.get("raw_home_probability") or out.get("calibrated_home_probability"))
    away = _finite_probability(out.get("raw_away_probability") or out.get("calibrated_away_probability"))
    if home is None or away is None:
        out.setdefault("v17_numerical_engine", {
            "status": "NOT_APPLICABLE_NO_COMPLETED_NUMERIC_PACKAGE",
            "creates_sporting_probability": False,
            "can_execute": False,
        })
        return out
    sport = str(getattr(req, "sport", None) or out.get("sport") or "UNKNOWN").upper()
    model_version = str(
        out.get("model_artifact_version")
        or out.get("model_version")
        or out.get("artifact_version")
        or "UNKNOWN"
    )
    candidate = str(getattr(req, "official_event_id", None) or out.get("official_event_id") or out.get("event_key") or "UNKNOWN")
    common = {
        "lane": V17Lane.TEAM_EVENT_ML,
        "sport": sport,
        "market_or_stat": "moneyline",
        "controlling_specialist": str(out.get("controlling_model_identity") or out.get("provider_identity") or "LLP_TEAM_BETTING_ENGINE"),
        "model_version": model_version,
        "model_family": ModelFamily.SPORT_SPECIFIC_EVENT_SIMULATION,
        "computation_method": "CERTIFIED_TEAM_EVENT_NATIVE",
        "computation_version": "V17_TEAM_EVENT_BRIDGE_V1",
        "simulation_count": out.get("simulation_count"),
        "distribution_diagnostics": {"native_code": out.get("code"), "probabilities_modified_by_bridge": False},
    }
    out["v17_numerical_engine"] = {
        "status": "PASS",
        "home": certify_native_probability(candidate_id=f"{candidate}:HOME", probability=home, **common),
        "away": certify_native_probability(candidate_id=f"{candidate}:AWAY", probability=away, **common),
        "probabilities_modified_by_bridge": False,
        "creates_sporting_probability": False,
        "can_execute": False,
    }
    return out


def install_production_bridges(*, market_api: Any, team_event_module: Any) -> bool:
    """Install idempotent wrappers at the common production prop/ML boundaries."""
    changed = False
    if not getattr(market_api, "_v17_certified_numerical_bridge_installed", False):
        original = getattr(market_api, "score_discrete_prop_end_to_end", None)
        if callable(original):
            def prop_bridge(*args: Any, **kwargs: Any):
                result = original(*args, **kwargs)
                return attach_prop_numerical_certification(result, seed=int(kwargs.get("seed", 0)))
            market_api.score_discrete_prop_end_to_end = prop_bridge
            market_api._v17_certified_numerical_bridge_installed = True
            changed = True

    if not getattr(team_event_module, "_v17_certified_numerical_bridge_installed", False):
        original_team = getattr(team_event_module, "score_team_event_request", None)
        if callable(original_team):
            def team_bridge(req: Any, *args: Any, **kwargs: Any):
                result = original_team(req, *args, **kwargs)
                return attach_team_event_numerical_certification(result, req=req)
            team_event_module.score_team_event_request = team_bridge
            team_event_module._v17_certified_numerical_bridge_installed = True
            changed = True
    return changed


__all__ = [
    "attach_prop_numerical_certification",
    "attach_team_event_numerical_certification",
    "certify_native_probability",
    "install_production_bridges",
]
