"""Fail-closed production-model readiness register for the audited V17 sports.

This register answers exactly one question per sport: *has the repository proven
every gate required before this sport's fitted model may publish a governed
probability?* It is observability only. It never scores, calibrates, ranks,
promotes, registers, or publishes a sporting probability, and it never turns a
candidate pipeline, a declared specialist identity, a route registration, or a
caller-supplied string into certification.

Two kinds of signal are deliberately kept separate:

* **authoritative runtime facts** — bridge registration, scorer resolvability,
  fitted-artifact certification, calibration/hydration dependency state. These
  come from the existing readiness bridge overlay and the static certification
  catalog; this module cannot grant them.
* **sport-specific evidence receipts** — for example an NHL two-provider
  confirmed-starting-goalie boundary, an NFL forward-validation receipt, or an
  NCAAF live-serving acceptance. Absent a receipt the gate stays unsatisfied
  with a typed blocker. Supplying observation flags can satisfy an evidence
  gate; it can never satisfy an authoritative certification gate.

Missing evidence is reported as its own blocker and is never collapsed into
``MODEL_UNAVAILABLE`` or replaced with L5/L10, trends, market-implied
probability, or generic reasoning. ``can_execute`` is always false.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from v17.all_sport_capability_readiness import (
    _CALIBRATION_MODE,
    _HYDRATION_MODE,
    _readiness,
)
from v17.team_event_capability_manifest import (
    CERTIFIED_TEAM_EVENT_SPORTS,
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
)

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
REGISTER_VERSION = "V17_SPORT_MODEL_PRODUCTION_READINESS_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
PRODUCTION_READY = "PRODUCTION_READY"
BLOCKED = "BLOCKED"

# The sports named in the 2026-11-11 production-model audit. Every one of them
# is required here even while its lane is blocked.
AUDITED_SPORTS: tuple[str, ...] = (
    "NHL",
    "NBA",
    "NFL",
    "NCAAF",
    "NCAAB",
    "TENNIS",
    "SOCCER",
)

# Gate kinds. Authoritative kinds are evaluated from runtime/registry state; the
# observation kind can only be satisfied by an explicit server-side receipt.
_KIND_AUTHORITATIVE = "authoritative"
_KIND_OBSERVATION = "observation"


@dataclass(frozen=True)
class Gate:
    """One required production gate and the typed blocker when it is unmet."""

    gate_id: str
    blocker: str
    kind: str
    source: str
    observation_key: str | None = None
    required_value: str | None = None
    can_execute: bool = False


_AUTH_GATES: dict[str, Gate] = {
    "BRIDGE_REGISTERED": Gate(
        "BRIDGE_REGISTERED",
        "TEAM_EVENT_BRIDGE_NOT_REGISTERED",
        _KIND_AUTHORITATIVE,
        "TEAM_EVENT_BRIDGES runtime registration",
    ),
    "SCORER_RESOLVABLE": Gate(
        "SCORER_RESOLVABLE",
        "TEAM_EVENT_SCORER_NOT_RESOLVABLE",
        _KIND_AUTHORITATIVE,
        "bridge scorer resolution",
    ),
    "FITTED_MODEL_CERTIFIED": Gate(
        "FITTED_MODEL_CERTIFIED",
        "TEAM_EVENT_FITTED_MODEL_CERTIFICATION_NOT_ACTIVE",
        _KIND_AUTHORITATIVE,
        "CERTIFIED_TEAM_EVENT_SPORTS / runtime promoted champion",
    ),
    "CALIBRATION_BOUND": Gate(
        "CALIBRATION_BOUND",
        "SERVER_CALIBRATION_ARTIFACT_NOT_BOUND",
        _KIND_AUTHORITATIVE,
        "readiness calibration dependency",
    ),
    "EVIDENCE_HYDRATOR_BOUND": Gate(
        "EVIDENCE_HYDRATOR_BOUND",
        "BACKEND_SPORT_EVIDENCE_HYDRATOR_NOT_BOUND",
        _KIND_AUTHORITATIVE,
        "readiness hydration dependency",
    ),
}


def _observation_gate(
    gate_id: str, blocker: str, observation_key: str, required_value: str
) -> Gate:
    return Gate(
        gate_id=gate_id,
        blocker=blocker,
        kind=_KIND_OBSERVATION,
        source="server-side acceptance receipt",
        observation_key=observation_key,
        required_value=required_value,
    )


# Sport-specific gates track the audited limitation for each sport. They are not
# probability semantics: each one asserts that prior evidence exists before the
# existing fitted model may publish.
_SPORT_GATES: dict[str, tuple[Gate, ...]] = {
    "NHL": (
        _observation_gate(
            "STARTING_GOALIES_CONFIRMED",
            "NHL_STARTING_GOALIE_CONFIRMATION_NOT_SUPPLIED",
            "starting_goalie_confirmation_status",
            "CONFIRMED",
        ),
        _observation_gate(
            "PREGAME_INPUTS_COMPLETE",
            "NHL_PREGAME_INPUTS_INCOMPLETE",
            "pregame_inputs_status",
            "COMPLETE",
        ),
    ),
    "NBA": (
        _observation_gate(
            "SOURCE_TO_SCORER_INTEGRATED",
            "NBA_SOURCE_TO_SCORER_INTEGRATION_UNVERIFIED",
            "source_to_scorer_integration_status",
            "VERIFIED",
        ),
        _observation_gate(
            "CERTIFIED_CALIBRATION",
            "NBA_CERTIFIED_CALIBRATION_UNAVAILABLE",
            "certified_calibration_status",
            "PASS",
        ),
    ),
    "NFL": (
        _observation_gate(
            "FORWARD_VALIDATION",
            "NFL_FORWARD_VALIDATION_UNPROVEN",
            "forward_validation_status",
            "PASS",
        ),
        _observation_gate(
            "PUBLICATION_PROVENANCE",
            "NFL_PUBLICATION_PROVENANCE_UNVERIFIED",
            "publication_provenance_status",
            "VERIFIED",
        ),
        _observation_gate(
            "CERTIFICATION_REQUIREMENT",
            "NFL_CERTIFICATION_REQUIREMENT_UNMET",
            "certification_requirement_status",
            "PASS",
        ),
    ),
    "NCAAF": (
        _observation_gate(
            "CERTIFIED_LIVE_SERVING",
            "NCAAF_CERTIFIED_LIVE_SERVING_INCOMPLETE",
            "certified_live_serving_status",
            "PASS",
        ),
        _observation_gate(
            "PUBLICATION_PROVENANCE",
            "NCAAF_PUBLICATION_PROVENANCE_UNVERIFIED",
            "publication_provenance_status",
            "VERIFIED",
        ),
    ),
    "NCAAB": (
        _observation_gate(
            "PREGAME_MODEL_INTEGRATION",
            "NCAAB_PREGAME_MODEL_INTEGRATION_INCOMPLETE",
            "pregame_model_integration_status",
            "VERIFIED",
        ),
        _observation_gate(
            "INDEPENDENT_VALIDATION",
            "NCAAB_INDEPENDENT_VALIDATION_UNPROVEN",
            "independent_validation_status",
            "PASS",
        ),
    ),
    "TENNIS": (
        _observation_gate(
            "PRODUCTION_SPECIALIST",
            "TENNIS_PRODUCTION_SPECIALIST_NOT_DEMONSTRATED",
            "production_specialist_status",
            "DEMONSTRATED",
        ),
    ),
    "SOCCER": (
        _observation_gate(
            "PRODUCTION_SPECIALIST",
            "SOCCER_PRODUCTION_SPECIALIST_NOT_DEMONSTRATED",
            "production_specialist_status",
            "DEMONSTRATED",
        ),
    ),
}


def _health_row(health: Mapping[str, Any] | None, sport: str) -> dict[str, Any]:
    if not isinstance(health, Mapping):
        return {}
    row = health.get(sport)
    return dict(row) if isinstance(row, Mapping) else {}


def _runtime_promoted(readiness: Mapping[str, Any]) -> bool:
    return readiness.get("runtime_artifact_certification_status") == "PASS"


def _runtime_proof_complete(readiness: Mapping[str, Any]) -> bool:
    """A runtime PASS is only proof when it names the exact promoted versions.

    The NFL champion loader can report PASS while returning an empty artifact
    version. An unnamed champion is not a certification identity, so it must not
    satisfy certification or its calibration/hydration dependencies.
    """
    version = readiness.get("runtime_model_artifact_version")
    calibration = readiness.get("runtime_calibration_version")
    return (
        isinstance(version, str)
        and bool(version.strip())
        and isinstance(calibration, str)
        and bool(calibration.strip())
    )


def _authoritative_satisfied(
    gate: Gate, health: Mapping[str, Any], readiness: Mapping[str, Any]
) -> bool:
    if gate.gate_id == "BRIDGE_REGISTERED":
        return bool(health.get("registered"))
    if gate.gate_id == "SCORER_RESOLVABLE":
        return bool(health.get("scorer_resolvable"))
    if gate.gate_id == "FITTED_MODEL_CERTIFIED":
        if readiness.get("certification_identity_status") == "CERTIFIED":
            return True
        return _runtime_promoted(readiness) and _runtime_proof_complete(readiness)
    if gate.gate_id == "CALIBRATION_BOUND":
        if readiness.get("calibration_dependency_satisfied") is not True:
            return False
        return not _runtime_promoted(readiness) or _runtime_proof_complete(readiness)
    if gate.gate_id == "EVIDENCE_HYDRATOR_BOUND":
        if readiness.get("hydration_dependency_satisfied") is not True:
            return False
        return not _runtime_promoted(readiness) or _runtime_proof_complete(readiness)
    return False


def _gate_status(
    gate: Gate,
    health: Mapping[str, Any],
    readiness: Mapping[str, Any],
    observations: Mapping[str, Any],
) -> dict[str, Any]:
    if gate.kind == _KIND_AUTHORITATIVE:
        satisfied = _authoritative_satisfied(gate, health, readiness)
        observed = None
    else:
        observed = observations.get(gate.observation_key or "", None)
        satisfied = (
            isinstance(observed, str)
            and observed.strip().upper() == str(gate.required_value)
        )
    return {
        "gate_id": gate.gate_id,
        "satisfied": satisfied,
        "blocker": None if satisfied else gate.blocker,
        "evidence_source": gate.source,
        "required_value": gate.required_value,
        "observed_value": observed,
        "can_execute": False,
    }


def _certification_identity(sport: str, readiness: Mapping[str, Any]) -> str | None:
    version = readiness.get("runtime_model_artifact_version")
    if (
        _runtime_promoted(readiness)
        and _runtime_proof_complete(readiness)
        and isinstance(version, str)
    ):
        return version.strip()
    return CERTIFIED_TEAM_EVENT_SPORTS.get(sport)


def assess_sport(
    sport: str,
    *,
    health: Mapping[str, dict[str, Any]] | None = None,
    observations: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the fail-closed production-model readiness row for one sport.

    ``health`` is the bridge-health mapping produced by the installed readiness
    overlay. ``observations`` may carry sport-specific server receipts; it can
    satisfy evidence gates only, never certification.
    """
    normalized = str(sport or "").strip().upper()
    if normalized not in EXPECTED_TEAM_EVENT_SPORTS:
        raise ValueError(f"unsupported readiness sport: {normalized}")
    row_health = _health_row(health, normalized)
    readiness = _readiness(normalized, row_health)
    supplied = observations.get(normalized) if isinstance(observations, Mapping) else None
    supplied = dict(supplied) if isinstance(supplied, Mapping) else {}

    gates = [_AUTH_GATES[key] for key in _AUTH_GATES]
    gates.extend(_SPORT_GATES.get(normalized, ()))
    statuses = [_gate_status(gate, row_health, readiness, supplied) for gate in gates]
    blockers = [status["blocker"] for status in statuses if status["blocker"]]
    blockers.extend(readiness.get("readiness_blockers") or [])

    ready = bool(statuses) and all(status["satisfied"] for status in statuses)
    return {
        "register_version": REGISTER_VERSION,
        "sport": normalized,
        "audited_2026_11_11": normalized in AUDITED_SPORTS,
        "production_model_ready": ready,
        "readiness_state": PRODUCTION_READY if ready else BLOCKED,
        "blockers": list(dict.fromkeys(blockers)),
        "gates": statuses,
        "certification_identity": _certification_identity(normalized, readiness),
        "certification_identity_status": readiness.get("certification_identity_status"),
        "runtime_artifact_certification_status": readiness.get(
            "runtime_artifact_certification_status"
        ),
        "development_status": readiness.get("operational_readiness_status"),
        "calibration_mode": _CALIBRATION_MODE.get(normalized, "UNAVAILABLE"),
        "hydration_mode": _HYDRATION_MODE.get(normalized, "UNASSIGNED"),
        "required_inputs": list(TEAM_EVENT_INPUT_CONTRACTS.get(normalized, ())),
        "probability_publishable": PROBABILITY_PUBLISHABLE,
        "publication_scope": "ROW_NOT_SPORT_CAPABILITY",
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "legacy_fallback_allowed": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
    }


def build_readiness_register(
    *,
    health: Mapping[str, dict[str, Any]] | None = None,
    observations: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one row per audited sport; absent evidence can never read READY."""
    rows = [
        assess_sport(sport, health=health, observations=observations)
        for sport in AUDITED_SPORTS
    ]
    blocked = [row["sport"] for row in rows if not row["production_model_ready"]]
    return {
        "register_version": REGISTER_VERSION,
        "sports": rows,
        "audited_sports": list(AUDITED_SPORTS),
        "production_ready_sports": [
            row["sport"] for row in rows if row["production_model_ready"]
        ],
        "blocked_sports": blocked,
        "all_audited_sports_blocked": len(blocked) == len(rows),
        "probability_behavior_changed": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUDITED_SPORTS",
    "BLOCKED",
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "PRODUCTION_READY",
    "REGISTER_VERSION",
    "TERMINAL_AUTHORITY",
    "assess_sport",
    "build_readiness_register",
]
