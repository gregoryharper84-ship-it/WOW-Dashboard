"""Non-invokable V17 candidate bridge registrations for three partial model lanes.

This registry is deliberately disjoint from TEAM_EVENT_BRIDGES. It records
importable, explicit candidate/replay entrypoints without routing requests into
uncertified models. A shadow registration is never production capability,
calibration approval, terminal authority, or evidence of qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from v17.team_event_capability_manifest import normalize_team_event_sport

CAN_EXECUTE = False


@dataclass(frozen=True)
class CandidateBridgeRegistration:
    sport: str
    module: str
    entrypoint: str
    purpose: str
    next_gate: str
    invokable_from_team_event_router: bool = False
    probability_publishable: bool = False
    rank_eligible: bool = False
    can_execute: bool = False


# These entrypoints already exist, but none is a certified team/event publication
# adapter. NCAAB in particular is a frozen-candidate REPLAY VERIFIER, not a
# pregame scorer. Never substitute one for another.
CANDIDATE_BRIDGE_SPECS: dict[str, tuple[str, str, str, str]] = {
    "NBA": (
        "v17.nba_current_pregame_scorer",
        "prepare_nba_current_pregame_scorer_receipt",
        "PREGAME_RESEARCH_RECEIPT",
        "SOURCE_ATTESTED_HYDRATION_CERTIFIED_CALIBRATOR_DYNAMIC_BOUNDS_AND_GOVERNED_PUBLICATION",
    ),
    "NCAAF": (
        "ncaaf_event_feature_handoff",
        "score_current_event_snapshot",
        "FITTED_CANDIDATE_RESEARCH_HANDOFF",
        "FAILURE_PATH_DYNAMIC_BOUNDS_AND_TERMINAL_GOVERNANCE_ACCEPTANCE",
    ),
    "NCAAB": (
        "v17.ncaab_moneyline_replay",
        "verify_frozen_replay",
        "FROZEN_CANDIDATE_REPLAY_VERIFIER",
        "INDEPENDENT_REPLAY_PROMOTION_CURRENT_PREGAME_HYDRATION_AND_PUBLICATION_ADAPTER",
    ),
}

CANDIDATE_TEAM_EVENT_BRIDGES: dict[str, CandidateBridgeRegistration] = {}
CANDIDATE_BRIDGE_IMPORT_FAILURES: dict[str, str] = {}


def install_candidate_team_event_bridges() -> dict[str, Any]:
    """Register ONLY import-verified research entrypoints, never a live scorer.

    Missing modules/symbols are recorded as truthful health diagnostics; an
    unavailable candidate must not crash production startup or be faked as UP.
    Neither registration nor return values from these functions ever enter the
    V17 production scorer/terminal router.
    """
    for sport, (module_name, symbol, purpose, next_gate) in CANDIDATE_BRIDGE_SPECS.items():
        try:
            module = import_module(module_name)
            if not callable(getattr(module, symbol, None)):
                raise AttributeError("ENTRYPOINT_NOT_CALLABLE")
        except Exception as exc:  # candidate-only startup must not break certified routes
            CANDIDATE_TEAM_EVENT_BRIDGES.pop(sport, None)
            CANDIDATE_BRIDGE_IMPORT_FAILURES[sport] = type(exc).__name__
            continue
        CANDIDATE_TEAM_EVENT_BRIDGES[sport] = CandidateBridgeRegistration(
            sport=sport,
            module=module_name,
            entrypoint=symbol,
            purpose=purpose,
            next_gate=next_gate,
        )
        CANDIDATE_BRIDGE_IMPORT_FAILURES.pop(sport, None)
    return {
        "candidate_bridges_registered": sorted(CANDIDATE_TEAM_EVENT_BRIDGES),
        "candidate_bridges_unavailable": sorted(CANDIDATE_BRIDGE_IMPORT_FAILURES),
        "production_route_changed": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def candidate_bridge_health(sport: str) -> dict[str, Any]:
    normalized = normalize_team_event_sport(sport)
    spec = CANDIDATE_BRIDGE_SPECS.get(normalized)
    registration = CANDIDATE_TEAM_EVENT_BRIDGES.get(normalized)
    if spec is None:
        return {
            "candidate_bridge_registered": False,
            "candidate_bridge_status": "NOT_APPLICABLE",
            "candidate_bridge_purpose": None,
            "candidate_bridge_entrypoint": None,
            "candidate_bridge_next_gate": None,
            "candidate_bridge_import_failure_type": None,
            "candidate_bridge_live_routable": False,
        }
    return {
        "candidate_bridge_registered": registration is not None,
        "candidate_bridge_status": (
            "REGISTERED_RESEARCH_ONLY" if registration is not None
            else "CANDIDATE_ENTRYPOINT_UNAVAILABLE"
        ),
        "candidate_bridge_purpose": spec[2],
        "candidate_bridge_entrypoint": f"{spec[0]}:{spec[1]}",
        "candidate_bridge_next_gate": spec[3],
        "candidate_bridge_import_failure_type": CANDIDATE_BRIDGE_IMPORT_FAILURES.get(normalized),
        "candidate_bridge_live_routable": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CANDIDATE_BRIDGE_SPECS",
    "CANDIDATE_TEAM_EVENT_BRIDGES",
    "CandidateBridgeRegistration",
    "candidate_bridge_health",
    "install_candidate_team_event_bridges",
]
