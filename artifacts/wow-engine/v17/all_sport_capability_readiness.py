"""Fail-closed operational readiness overlay for every V17 team/event sport.

Registration/certification identity is not the same as an autonomously usable
production probability lane. This overlay keeps those concepts separate so a
bridge cannot advertise ``model_capability_ready=true`` merely because its
adapter imports and its specialist identity is certified while required runtime
dependencies (for example calibration or backend hydration) are still external.

The overlay does not change any fitted model, calibration coefficient, sporting
probability, ranking rule, or terminal authority. It only makes capability
health truthful and same-shaped across the full twelve-sport catalog.
"""
from __future__ import annotations

from typing import Any, Mapping

from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
)

CAN_EXECUTE = False
READINESS_CONTRACT_VERSION = "V17_TEAM_EVENT_OPERATIONAL_READINESS_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

_REQUEST_DEPENDENT_MULTISPORT = frozenset(
    {"WNBA", "NHL", "SOCCER", "TENNIS", "MMA"}
)

_HYDRATION_MODE = {
    "MLB": "SERVER_CANONICAL_MLB_LEDGER",
    "NFL": "NFL_SPORT_SPECIFIC_PUBLICATION_CHAIN",
    "WNBA": "REQUEST_OR_DISCOVERY_EVIDENCE",
    "NHL": "REQUEST_OR_DISCOVERY_EVIDENCE",
    "SOCCER": "REQUEST_OR_DISCOVERY_EVIDENCE",
    "TENNIS": "REQUEST_OR_DISCOVERY_EVIDENCE",
    "MMA": "REQUEST_OR_DISCOVERY_EVIDENCE",
    "NBA": "MODEL_DEVELOPMENT_LANE",
    "NCAAF": "MODEL_DEVELOPMENT_LANE",
    "NCAAB": "MODEL_DEVELOPMENT_LANE",
    "PGA": "MODEL_DEVELOPMENT_LANE",
    "BOXING": "MODEL_DEVELOPMENT_LANE",
}

_CALIBRATION_MODE = {
    "MLB": "BRIDGE_OWNED",
    "NFL": "BRIDGE_OWNED_CHAMPION",
    "WNBA": "REQUEST_SUPPLIED_CERTIFIED_ARTIFACT",
    "NHL": "REQUEST_SUPPLIED_CERTIFIED_ARTIFACT",
    "SOCCER": "REQUEST_SUPPLIED_CERTIFIED_ARTIFACT",
    "TENNIS": "REQUEST_SUPPLIED_CERTIFIED_ARTIFACT",
    "MMA": "REQUEST_SUPPLIED_CERTIFIED_ARTIFACT",
    "NBA": "UNAVAILABLE",
    "NCAAF": "UNAVAILABLE",
    "NCAAB": "UNAVAILABLE",
    "PGA": "UNAVAILABLE",
    "BOXING": "UNAVAILABLE",
}


def _nfl_runtime_champion_probe() -> dict[str, Any]:
    """Prove the exact live NFL champion and calibrator through its own loader.

    The NFL loader already enforces one active/promoted champion, matching model
    family/version, active promoted PASS calibrator and artifact integrity. Health
    reuses that authority rather than duplicating or weakening its certification
    contract. Any unavailable DB/runtime condition fails closed.
    """
    try:
        import api_prod_market_acceptance as production_base
        from nfl_event_model_v17 import load_champion_model

        get_client = getattr(getattr(production_base, "market_api", None), "prod", None)
        get_client = getattr(get_client, "get_client", None)
        if not callable(get_client):
            raise RuntimeError("NFL_READINESS_DB_CLIENT_UNAVAILABLE")
        champion = load_champion_model(get_client())
        return {
            "runtime_artifact_certification_status": "PASS",
            "runtime_artifact_certification_code": "NFL_ACTIVE_PROMOTED_CHAMPION_PROVEN",
            "runtime_model_artifact_version": champion.model_artifact_version,
            "runtime_calibration_version": champion.calibration_version,
            "runtime_calibration_training_n": champion.calibration_training_n,
            "runtime_dependency_probe_error": None,
            "can_execute": False,
        }
    except Exception as exc:  # noqa: BLE001 - health must fail closed
        return {
            "runtime_artifact_certification_status": "UNPROVEN",
            "runtime_artifact_certification_code": "NFL_ACTIVE_PROMOTED_CHAMPION_NOT_PROVEN",
            "runtime_model_artifact_version": None,
            "runtime_calibration_version": None,
            "runtime_calibration_training_n": None,
            "runtime_dependency_probe_error": type(exc).__name__,
            "can_execute": False,
        }


def _readiness(
    sport: str,
    health: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = str(sport or "").upper()
    registered = bool(health.get("registered"))
    scorer = bool(health.get("scorer_resolvable"))
    certification = str(health.get("certification_status") or "NOT_CERTIFIED")
    # Older parity callers intentionally provide only the historic three-field
    # capability shape. Preserve MLB's already-certified artifact semantics when
    # that field is omitted, while a present explicit false still fails closed.
    if "model_artifact_present" in health:
        model_artifact = bool(health.get("model_artifact_present"))
    else:
        model_artifact = bool(normalized == "MLB" and certification == "CERTIFIED")
    runtime_artifact_certified = (
        str(health.get("runtime_artifact_certification_status") or "") == "PASS"
    )

    effective_certified = certification == "CERTIFIED" or (
        normalized == "NFL" and runtime_artifact_certified
    )

    blockers: list[str] = []
    if not registered:
        blockers.append("TEAM_EVENT_BRIDGE_NOT_REGISTERED")
    if registered and not scorer:
        blockers.append("TEAM_EVENT_SCORER_NOT_RESOLVABLE")
    if registered and not model_artifact:
        blockers.append("TEAM_EVENT_MODEL_ARTIFACT_NOT_RESOLVABLE")
    if normalized == "NFL" and not runtime_artifact_certified:
        blockers.append("NFL_ACTIVE_PROMOTED_CHAMPION_NOT_PROVEN")
    elif not effective_certified:
        blockers.append("TEAM_EVENT_CERTIFICATION_NOT_ACTIVE")

    request_scoring_path_ready = bool(
        registered and scorer and model_artifact and effective_certified
    )

    calibration_mode = _CALIBRATION_MODE.get(normalized, "UNAVAILABLE")
    hydration_mode = _HYDRATION_MODE.get(normalized, "UNASSIGNED")
    calibration_dependency_satisfied = normalized == "MLB" or (
        normalized == "NFL" and runtime_artifact_certified
    )
    hydration_dependency_satisfied = normalized in {"MLB", "NFL"} and (
        normalized != "NFL" or runtime_artifact_certified
    )

    if normalized in _REQUEST_DEPENDENT_MULTISPORT and request_scoring_path_ready:
        # These exact scorers can be invoked when the request supplies valid
        # sporting evidence and a matching calibration artifact, but production
        # health must not call them autonomous until server ownership is bound.
        blockers.extend(
            [
                "SERVER_CALIBRATION_ARTIFACT_NOT_BOUND",
                "BACKEND_SPORT_EVIDENCE_HYDRATOR_NOT_BOUND",
            ]
        )

    autonomous_ready = bool(request_scoring_path_ready and not blockers)
    if autonomous_ready:
        operational_status = "READY"
        operational_certification = "CERTIFIED_OPERATIONAL"
    elif request_scoring_path_ready and normalized in _REQUEST_DEPENDENT_MULTISPORT:
        operational_status = "CERTIFIED_REQUEST_DEPENDENT"
        operational_certification = "CERTIFIED_DEPENDENCIES_UNBOUND"
    elif registered:
        operational_status = "REGISTERED_NOT_READY"
        operational_certification = certification
    else:
        operational_status = "UNAVAILABLE"
        operational_certification = certification

    return {
        "readiness_contract_version": READINESS_CONTRACT_VERSION,
        "certification_identity_status": certification,
        "runtime_artifact_certification_status": str(
            health.get("runtime_artifact_certification_status") or "NOT_APPLICABLE"
        ),
        "runtime_artifact_certification_code": health.get(
            "runtime_artifact_certification_code"
        ),
        "runtime_model_artifact_version": health.get("runtime_model_artifact_version"),
        "runtime_calibration_version": health.get("runtime_calibration_version"),
        "runtime_calibration_training_n": health.get("runtime_calibration_training_n"),
        "operational_certification_status": operational_certification,
        "operational_readiness_status": operational_status,
        "model_capability_ready": autonomous_ready,
        "request_scoring_path_ready": request_scoring_path_ready,
        "readiness_blockers": list(dict.fromkeys(blockers)),
        "model_artifact_dependency_satisfied": model_artifact,
        "calibration_dependency_satisfied": calibration_dependency_satisfied,
        "hydration_dependency_satisfied": hydration_dependency_satisfied,
        "calibration_mode": calibration_mode,
        "hydration_mode": hydration_mode,
        "request_evidence_injection_supported": bool(
            TEAM_EVENT_INPUT_CONTRACTS.get(normalized)
        ),
        "probability_publishable_scope": "ROW_NOT_GLOBAL_CAPABILITY",
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def install_all_sport_capability_readiness() -> dict[str, Any]:
    """Install a final health/readiness overlay before sport parity captures it."""
    import v17.team_event_bridge_runtime as bridges
    import v17.team_event_sport_parity as parity

    if getattr(bridges, "_v17_all_sport_capability_readiness_installed", False):
        return {"status": "ALREADY_INSTALLED", "can_execute": False}

    original_health = bridges.team_event_bridge_health
    original_state_for = parity._state_for

    def readiness_health() -> dict[str, dict[str, Any]]:
        base = original_health()
        out: dict[str, dict[str, Any]] = {}
        for sport in EXPECTED_TEAM_EVENT_SPORTS:
            row = dict(base.get(sport) or {})
            if sport == "NFL" and row.get("registered"):
                row.update(_nfl_runtime_champion_probe())
            row.update(_readiness(sport, row))
            out[sport] = row
        for sport, value in base.items():
            if sport not in out:
                row = dict(value)
                row.update(_readiness(sport, row))
                out[sport] = row
        return out

    def readiness_state_for(
        sport: str,
        bridge_health: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        health = dict(bridge_health or {})
        state = dict(original_state_for(sport, health))
        readiness = _readiness(str(sport or "").upper(), health)
        state.update(
            {
                "model_capability_ready": readiness["model_capability_ready"],
                "request_scoring_path_ready": readiness["request_scoring_path_ready"],
                "certification_identity_status": readiness[
                    "certification_identity_status"
                ],
                "runtime_artifact_certification_status": readiness[
                    "runtime_artifact_certification_status"
                ],
                "operational_certification_status": readiness[
                    "operational_certification_status"
                ],
                "operational_readiness_status": readiness["operational_readiness_status"],
                "readiness_blockers": readiness["readiness_blockers"],
                "calibration_dependency_satisfied": readiness[
                    "calibration_dependency_satisfied"
                ],
                "hydration_dependency_satisfied": readiness[
                    "hydration_dependency_satisfied"
                ],
                "calibration_mode": readiness["calibration_mode"],
                "request_evidence_injection_supported": readiness[
                    "request_evidence_injection_supported"
                ],
                "readiness_contract_version": READINESS_CONTRACT_VERSION,
                "can_execute": False,
            }
        )
        return state

    bridges.team_event_bridge_health = readiness_health
    parity._state_for = readiness_state_for
    bridges._v17_all_sport_capability_readiness_original_health = original_health
    bridges._v17_all_sport_capability_readiness_installed = True

    return {
        "status": "INSTALLED",
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "READINESS_CONTRACT_VERSION",
    "install_all_sport_capability_readiness",
]
