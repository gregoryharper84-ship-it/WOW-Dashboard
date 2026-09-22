"""Fail-closed operational readiness overlay for every V17 team/event sport.

Registration/certification identity is not the same as an autonomously usable
production probability lane.  This overlay keeps those concepts separate so a
bridge cannot advertise ``model_capability_ready=true`` merely because its
adapter imports and its specialist identity is certified while required runtime
dependencies (for example calibration or backend hydration) are still external.

The overlay does not change any fitted model, calibration coefficient, sporting
probability, ranking rule, or terminal authority.  It only makes capability
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

# MLB/NFL own hydration/calibration inside their exact sport publication chains.
# The five multisport bridges can score only when the request supplies the fitted
# sporting evidence and a matching certified calibration artifact.  Until a
# server-owned hydrator/artifact registry is bound, they are request-dependent,
# not autonomous production-ready lanes.
_BRIDGE_OWNED_DEPENDENCIES = frozenset({"MLB", "NFL"})
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
    "NFL": "BRIDGE_OWNED",
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


def _readiness(
    sport: str,
    health: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = str(sport or "").upper()
    registered = bool(health.get("registered"))
    scorer = bool(health.get("scorer_resolvable"))
    model_artifact = bool(health.get("model_artifact_present"))
    certification = str(health.get("certification_status") or "NOT_CERTIFIED")

    blockers: list[str] = []
    if not registered:
        blockers.append("TEAM_EVENT_BRIDGE_NOT_REGISTERED")
    if registered and not scorer:
        blockers.append("TEAM_EVENT_SCORER_NOT_RESOLVABLE")
    if registered and not model_artifact:
        blockers.append("TEAM_EVENT_MODEL_ARTIFACT_NOT_RESOLVABLE")
    if certification != "CERTIFIED":
        blockers.append("TEAM_EVENT_CERTIFICATION_NOT_ACTIVE")

    request_scoring_path_ready = bool(
        registered
        and scorer
        and model_artifact
        and certification == "CERTIFIED"
    )

    calibration_mode = _CALIBRATION_MODE.get(normalized, "UNAVAILABLE")
    hydration_mode = _HYDRATION_MODE.get(normalized, "UNASSIGNED")
    calibration_dependency_satisfied = normalized in _BRIDGE_OWNED_DEPENDENCIES
    hydration_dependency_satisfied = normalized in _BRIDGE_OWNED_DEPENDENCIES

    if normalized in _REQUEST_DEPENDENT_MULTISPORT and request_scoring_path_ready:
        # The request schema can carry sport_specific_evidence, so these exact
        # models remain invokable when the caller supplies certified evidence.
        # But /health must not claim autonomous readiness until the backend owns
        # both dependencies itself.
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
        # Keep the repository/identity certification visible while publishing a
        # separate operational certification state.  This prevents the health
        # surface from implying that identity certification alone means the lane
        # can autonomously complete a governed probability package.
        "certification_identity_status": certification,
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
        current = bridges.team_event_bridge_health()
        return {
            "status": "ALREADY_INSTALLED",
            "team_event_bridges": current,
            "can_execute": False,
        }

    original_health = bridges.team_event_bridge_health
    original_state_for = parity._state_for

    def readiness_health() -> dict[str, dict[str, Any]]:
        base = original_health()
        out: dict[str, dict[str, Any]] = {}
        for sport in EXPECTED_TEAM_EVENT_SPORTS:
            row = dict(base.get(sport) or {})
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
        "team_event_bridges": readiness_health(),
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "READINESS_CONTRACT_VERSION",
    "install_all_sport_capability_readiness",
]
