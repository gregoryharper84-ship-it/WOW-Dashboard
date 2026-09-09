"""Production bridge registry and health contract for V17 LLP team/event scoring.

The catalog of sports LLP can discover is intentionally separate from the set of
bridges the governed backend can actually invoke.  Only registered bridges may
score.  Registration is never inferred from sport knowledge, public models,
market prices, or a generic numerical family.

This module is installed once by the accepted production bootstrap.  MLB is the
only production bridge registered today.  Future sports must supply an exact
sport adapter and pass its own certification/acceptance work before registration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Mapping

from fastapi import HTTPException

import v17.team_event_request_runtime as _base_runtime
from v17.llp_governed_package_scoring import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    PASS,
    validate_governed_scoring_package,
)
from v17.team_event_capability_manifest import (
    CERTIFIED_TEAM_EVENT_SPORTS,
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
    normalize_team_event_sport,
)

CAN_EXECUTE = False
DISCOVERY_ONLY_LABEL = "DISCOVERY CANDIDATE — NOT MODEL-SUPPORTED"
NO_VERIFIED_THREE_TEAM_PARLAY = (
    "NO VERIFIED 3-TEAM PARLAY — insufficient model-supported cross-sport rows."
)

BridgeScorer = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class TeamEventBridgeRegistration:
    sport: str
    adapter_name: str
    controlling_specialist: str
    required_inputs: tuple[str, ...]
    scorer: BridgeScorer
    standard_package_validation: bool = True
    can_execute: bool = False


# This is the live runtime registry.  It is deliberately NOT pre-populated from
# EXPECTED_TEAM_EVENT_SPORTS or CERTIFIED_TEAM_EVENT_SPORTS.  Discovery/catalog
# support and artifact certification alone cannot make a route invokable.
TEAM_EVENT_BRIDGES: dict[str, TeamEventBridgeRegistration] = {}

_ORIGINAL_BASE_SCORE = _base_runtime.score_team_event_request
_INSTALLED = False


def register_team_event_bridge(
    sport: str,
    *,
    adapter_name: str,
    controlling_specialist: str,
    scorer: BridgeScorer,
    required_inputs: tuple[str, ...] | None = None,
    standard_package_validation: bool = True,
) -> TeamEventBridgeRegistration:
    """Register one exact bridge; callers must already own certification proof."""
    normalized = normalize_team_event_sport(sport)
    registration = TeamEventBridgeRegistration(
        sport=normalized,
        adapter_name=adapter_name,
        controlling_specialist=controlling_specialist,
        required_inputs=tuple(required_inputs or TEAM_EVENT_INPUT_CONTRACTS.get(normalized, ())),
        scorer=scorer,
        standard_package_validation=standard_package_validation,
        can_execute=False,
    )
    TEAM_EVENT_BRIDGES[normalized] = registration
    return registration


def unregister_team_event_bridge(sport: str) -> None:
    """Test/rollback seam.  Production startup registers only certified bridges."""
    TEAM_EVENT_BRIDGES.pop(normalize_team_event_sport(sport), None)


def _model_failure_detail(
    req: Any,
    *,
    code: str,
    blocker: str,
    status_code: int,
    extra: Mapping[str, Any] | None = None,
) -> HTTPException:
    sport = normalize_team_event_sport(getattr(req, "sport", ""))
    detail: dict[str, Any] = {
        "code": code,
        "sport": sport,
        "league": getattr(req, "league", None),
        "failed_contract_scope": ["CONTROLLING_SPECIALIST"],
        "backend_route_status": (
            "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED"
            if code == MODEL_UNAVAILABLE
            else "SPORT_SPECIFIC_TEAM_EVENT_BRIDGE_BLOCKED"
        ),
        "blockers": [blocker],
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    if extra:
        detail.update(dict(extra))
    # Preserve the established LLP host-routing envelope on every new bridge
    # failure instead of returning a parallel error shape.  This keeps requester,
    # controlling engine, candidate family and terminal-authority identity intact.
    augmented = _base_runtime._augment_detail(detail, req)
    return HTTPException(status_code=status_code, detail=augmented)


def _input_value(req: Any, field: str) -> Any:
    value = getattr(req, field, None)
    if value not in (None, "", [], {}):
        return value
    evidence = getattr(req, "sport_specific_evidence", None)
    if isinstance(evidence, Mapping):
        value = evidence.get(field)
    return value


def missing_bridge_inputs(req: Any, registration: TeamEventBridgeRegistration) -> list[str]:
    return [
        field
        for field in registration.required_inputs
        if _input_value(req, field) in (None, "", [], {})
    ]


def _finite_probability(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    parsed = float(value)
    return isfinite(parsed) and 0.0 <= parsed <= 1.0


def _validate_standard_bridge_output(req: Any, result: dict[str, Any]) -> dict[str, Any]:
    """Validate the canonical package for newly registered non-MLB adapters."""
    audit = validate_governed_scoring_package(result)
    if audit.status != PASS:
        # Stale governed packages have their own typed status in the scoring
        # contract; malformed/missing packages remain MODEL_OUTPUT_INVALID.
        code = audit.status if audit.status != MODEL_OUTPUT_INVALID else MODEL_OUTPUT_INVALID
        raise _model_failure_detail(
            req,
            code=code,
            blocker=(audit.blockers[0] if audit.blockers else "GOVERNED_PACKAGE_INVALID"),
            status_code=409,
            extra={"governed_package_audit": audit.as_dict()},
        )

    # A bridge cannot self-promote ranking by returning a valid point estimate
    # while omitting the governed lower bound; the scoring audit above requires it.
    out = dict(result)
    out["rank_eligible"] = bool(result.get("rank_eligible") is True and audit.rank_eligible)
    out["probability_publishable"] = bool(
        result.get("probability_publishable") is True and audit.scoring_allowed
    )
    out["can_execute"] = False
    return out


def score_registered_team_event_request(
    req: Any,
    *,
    event_api: Any,
    canonical_hydration_required: bool = False,
) -> dict[str, Any]:
    """Dispatch through the exact registered sport bridge or fail closed."""
    sport = normalize_team_event_sport(getattr(req, "sport", ""))
    registration = TEAM_EVENT_BRIDGES.get(sport)
    if registration is None:
        raise _model_failure_detail(
            req,
            code=MODEL_UNAVAILABLE,
            blocker=f"{sport}_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE",
            status_code=409,
        )

    # MLB's established canonical hydration/identity contract remains controlling
    # inside the existing runtime.  Future standard adapters must prove their
    # sport-specific input contract here before scorer invocation.
    if registration.standard_package_validation:
        missing = missing_bridge_inputs(req, registration)
        if missing:
            raise _model_failure_detail(
                req,
                code=MODEL_INPUTS_INSUFFICIENT,
                blocker="SPORT_SPECIFIC_MODEL_INPUTS_INSUFFICIENT",
                status_code=422,
                extra={"missing_fields": missing},
            )

    try:
        result = registration.scorer(
            req,
            event_api=event_api,
            canonical_hydration_required=canonical_hydration_required,
        )
    except HTTPException:
        # The controlling bridge already produced a typed governed failure; never
        # flatten it into MODEL_UNAVAILABLE merely because scoring did not finish.
        raise
    except (TimeoutError, ConnectionError) as exc:
        raise _model_failure_detail(
            req,
            code=MODEL_SCORER_FAILED,
            blocker="TEAM_EVENT_SCORER_TIMEOUT_OR_TRANSPORT_FAILURE",
            status_code=503,
            extra={"error_type": type(exc).__name__, "model_invoked": True},
        ) from exc
    except Exception as exc:
        raise _model_failure_detail(
            req,
            code=MODEL_SCORER_FAILED,
            blocker="TEAM_EVENT_SCORER_EXCEPTION",
            status_code=503,
            extra={"error_type": type(exc).__name__, "model_invoked": True},
        ) from exc

    if not isinstance(result, dict):
        raise _model_failure_detail(
            req,
            code=MODEL_OUTPUT_INVALID,
            blocker="TEAM_EVENT_SCORER_INVALID_RESPONSE",
            status_code=500,
            extra={"model_invoked": True},
        )

    if registration.standard_package_validation:
        return _validate_standard_bridge_output(req, result)
    result = dict(result)
    result["can_execute"] = False
    return result


def team_event_bridge_health() -> dict[str, dict[str, Any]]:
    """Expose catalog support separately from production registration."""
    sports = list(EXPECTED_TEAM_EVENT_SPORTS)
    extras = sorted(set(TEAM_EVENT_BRIDGES).difference(sports))
    health: dict[str, dict[str, Any]] = {}
    for sport in [*sports, *extras]:
        registration = TEAM_EVENT_BRIDGES.get(sport)
        health[sport] = {
            "status": "UP" if registration is not None else MODEL_UNAVAILABLE,
            "registered": registration is not None,
            "controlling_specialist": (
                registration.controlling_specialist if registration is not None else None
            ),
            "adapter": registration.adapter_name if registration is not None else None,
            "required_inputs": list(TEAM_EVENT_INPUT_CONTRACTS.get(sport, ())),
            "discovery_supported": sport in EXPECTED_TEAM_EVENT_SPORTS,
            "probability_publishable": False,
            "can_execute": False,
        }
    return health


def discovery_only_candidate(sport: str, *, candidate_id: str | None = None) -> dict[str, Any]:
    normalized = normalize_team_event_sport(sport)
    return {
        "candidate_id": candidate_id,
        "sport": normalized,
        "status": DISCOVERY_ONLY_LABEL,
        "model_status": MODEL_UNAVAILABLE,
        "rank_eligible": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def verified_three_team_parlay_status(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Fail closed unless exactly three rows have current governed packages."""
    blockers: list[str] = []
    if len(rows) != 3:
        blockers.append("EXACTLY_THREE_GOVERNED_ROWS_REQUIRED")

    for index, row in enumerate(rows):
        audit = validate_governed_scoring_package(row)
        prefix = f"LEG_{index + 1}"
        if audit.status != PASS:
            blockers.append(f"{prefix}:{audit.status}")
        if row.get("rank_eligible") is not True:
            blockers.append(f"{prefix}:RANK_INELIGIBLE")
        if row.get("probability_publishable") is not True:
            blockers.append(f"{prefix}:PROBABILITY_NOT_PUBLISHABLE")
        if row.get("blockers"):
            blockers.append(f"{prefix}:ROW_BLOCKED")
        event_status = str(
            row.get("event_status") or row.get("official_event_status") or ""
        ).upper()
        if event_status not in {"PREGAME", "SCHEDULED"}:
            blockers.append(f"{prefix}:EVENT_STATUS_NOT_FRESH_PREGAME")

    if blockers:
        return {
            "status": "NO_VERIFIED_3_TEAM_PARLAY",
            "message": NO_VERIFIED_THREE_TEAM_PARLAY,
            "rank_eligible": False,
            "blockers": blockers,
            "can_execute": False,
        }
    return {
        "status": "VERIFIED_3_TEAM_RESEARCH_CARD",
        "message": "All three rows have valid governed pregame probability packages.",
        "rank_eligible": True,
        "blockers": [],
        "can_execute": False,
    }


def _install_health_overlay() -> None:
    """Replace inherited shallow /health with bridge-visible V17 health."""
    import api_prod_market_acceptance as production_base

    app = production_base.app
    existing = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) == "/health"
        and "GET" in (getattr(route, "methods", set()) or set())
    ]
    if any(getattr(route, "name", "") == "v17_bridge_health" for route in existing):
        return
    original_endpoint = existing[0].endpoint if existing else None
    app.router.routes[:] = [route for route in app.router.routes if route not in existing]

    def v17_bridge_health() -> dict[str, Any]:
        base_payload: dict[str, Any] = {}
        if callable(original_endpoint):
            value = original_endpoint()
            if isinstance(value, dict):
                base_payload.update(value)
        bridges = team_event_bridge_health()
        return {
            **base_payload,
            "status": base_payload.get("status", "ok"),
            "runtime_generation": (
                "V17_ACTIVE" if os.getenv("WOW_V17_ACTIVE", "0") == "1" else "V17_INACTIVE"
            ),
            "team_event_bridges": bridges,
            "team_event_bridge_summary": {
                "registered": sum(1 for value in bridges.values() if value["registered"]),
                "cataloged": len(bridges),
                "unsupported": sum(1 for value in bridges.values() if not value["registered"]),
            },
            "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        }

    app.add_api_route(
        "/health",
        v17_bridge_health,
        methods=["GET"],
        name="v17_bridge_health",
        include_in_schema=True,
    )


def install_team_event_bridge_runtime() -> dict[str, Any]:
    """Install the authoritative production registry exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return {
            "status": "ALREADY_INSTALLED",
            "team_event_bridges": team_event_bridge_health(),
            "can_execute": False,
        }

    # MLB is the only bridge currently backed by a certified production path.
    register_team_event_bridge(
        "MLB",
        adapter_name="V17_MLB_TEAM_EVENT_BRIDGE",
        controlling_specialist=CERTIFIED_TEAM_EVENT_SPORTS["MLB"],
        scorer=_ORIGINAL_BASE_SCORE,
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["MLB"],
        # Existing MLB runtime owns canonical hydration and its two-outcome
        # home/away package validator; do not re-interpret that mature contract.
        standard_package_validation=False,
    )

    _base_runtime.score_team_event_request = score_registered_team_event_request
    _install_health_overlay()
    _INSTALLED = True
    return {
        "status": "INSTALLED",
        "team_event_bridges": team_event_bridge_health(),
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "DISCOVERY_ONLY_LABEL",
    "NO_VERIFIED_THREE_TEAM_PARLAY",
    "TEAM_EVENT_BRIDGES",
    "TeamEventBridgeRegistration",
    "discovery_only_candidate",
    "install_team_event_bridge_runtime",
    "missing_bridge_inputs",
    "register_team_event_bridge",
    "score_registered_team_event_request",
    "team_event_bridge_health",
    "unregister_team_event_bridge",
    "verified_three_team_parlay_status",
]
