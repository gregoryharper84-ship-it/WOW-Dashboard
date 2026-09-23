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

from nfl_event_model_contract import CONTROLLING_SPECIALIST as NFL_CONTROLLING_SPECIALIST
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
    normalize_team_event_identity,
    normalize_team_event_sport,
)
from v17.rundown_sport_registry import REGULAR_SEASON
from v17.team_event_feature_consumption import build_feature_consumption_receipt
from v17.team_event_model_registry_audit import (
    certification_state,
    probe_sport,
    resolve_state,
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
    # Feature-consumption metadata is observational only.  A bridge must declare
    # an exact schema/role map before a receipt can become complete; absent
    # declarations remain visible as UNDECLARED rather than being inferred.
    feature_schema_version: str | None = None
    feature_roles: Mapping[str, str] | None = None
    critical_features: tuple[str, ...] = ()
    # Season regimes this fitted artifact is contracted to score. Defaults to
    # regular season only: a regular-season model has no calibration evidence
    # for preseason, playoff, spring-training or summer-league play and must not
    # inherit those regimes by being registered for the sport.
    supported_regimes: tuple[str, ...] = (REGULAR_SEASON,)
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
    feature_schema_version: str | None = None,
    feature_roles: Mapping[str, str] | None = None,
    critical_features: tuple[str, ...] | None = None,
    supported_regimes: tuple[str, ...] | None = None,
) -> TeamEventBridgeRegistration:
    """Register one exact bridge; callers must already own certification proof.

    Registration is capability, never certification: it makes a sport routable,
    and says nothing about whether its model is certified for publication.
    Feature-consumption declarations are audit metadata only and never alter
    probability, calibration, ranking or terminal authority.
    """
    normalized = normalize_team_event_sport(sport)
    registration = TeamEventBridgeRegistration(
        sport=normalized,
        adapter_name=adapter_name,
        controlling_specialist=controlling_specialist,
        required_inputs=tuple(required_inputs or TEAM_EVENT_INPUT_CONTRACTS.get(normalized, ())),
        scorer=scorer,
        standard_package_validation=standard_package_validation,
        feature_schema_version=(
            str(feature_schema_version).strip() if feature_schema_version else None
        ),
        feature_roles=dict(feature_roles) if feature_roles is not None else None,
        critical_features=tuple(critical_features or ()),
        supported_regimes=tuple(supported_regimes or (REGULAR_SEASON,)),
        can_execute=False,
    )
    TEAM_EVENT_BRIDGES[normalized] = registration
    return registration


def unregister_team_event_bridge(sport: str) -> None:
    """Test/rollback seam.  Production startup registers only certified bridges."""
    TEAM_EVENT_BRIDGES.pop(normalize_team_event_sport(sport), None)


def request_sport(req: Any) -> str:
    """Governed sport contract for a request, resolved from sport *and* league.

    A request carrying sport="FOOTBALL", league="NFL" names the NFL contract.
    Reading the sport field alone produced the unknown sport "FOOTBALL" and a
    MODEL_UNAVAILABLE that blamed a missing model for a missing alias.
    """
    return normalize_team_event_identity(getattr(req, "sport", ""), getattr(req, "league", None))


def _model_failure_detail(
    req: Any,
    *,
    code: str,
    blocker: str,
    status_code: int,
    extra: Mapping[str, Any] | None = None,
) -> HTTPException:
    sport = request_sport(req)
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


def _attach_feature_consumption_receipt(
    req: Any,
    registration: TeamEventBridgeRegistration,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach observational feature-consumption metadata without gating scoring.

    This intentionally does not fail a currently valid score when older scorers
    have not yet declared a feature schema or explicit consumed_feature_ids.  The
    receipt records those gaps as UNDECLARED / UNVERIFIED so rollout can proceed
    without inventing consumption evidence or changing champion probabilities.
    """
    out = dict(result)
    receipt = build_feature_consumption_receipt(
        req=req,
        sport=registration.sport,
        controlling_specialist=registration.controlling_specialist,
        required_inputs=registration.required_inputs,
        result=out,
        feature_schema_version=registration.feature_schema_version,
        declared_feature_roles=registration.feature_roles,
        critical_features=registration.critical_features,
    )
    out["feature_consumption_receipt_id"] = receipt["feature_consumption_receipt_id"]
    out["feature_consumption_receipt"] = receipt
    out["can_execute"] = False
    return out


def score_registered_team_event_request(
    req: Any,
    *,
    event_api: Any,
    canonical_hydration_required: bool = False,
) -> dict[str, Any]:
    """Dispatch through the exact registered sport bridge or fail closed."""
    sport = request_sport(req)
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
        result = _validate_standard_bridge_output(req, result)
    else:
        result = dict(result)
        result["can_execute"] = False
    return _attach_feature_consumption_receipt(req, registration, result)


def team_event_bridge_health() -> dict[str, dict[str, Any]]:
    """Expose catalog support, implementation coverage and registration separately.

    Coverage is inspectable without trial-scoring an arbitrary event. Runtime
    scorer resolution is intentionally distinct from repository implementation
    availability: an unregistered sport may have importable numerical machinery,
    but it is not runtime-resolvable until an exact governed bridge is installed.
    """
    sports = list(EXPECTED_TEAM_EVENT_SPORTS)
    extras = sorted(set(TEAM_EVENT_BRIDGES).difference(sports))
    health: dict[str, dict[str, Any]] = {}
    for sport in [*sports, *extras]:
        registration = TEAM_EVENT_BRIDGES.get(sport)
        registered = registration is not None
        probe = probe_sport(sport)
        certification, certification_id = certification_state(sport, registered=registered)
        health[sport] = {
            "status": "UP" if registered else MODEL_UNAVAILABLE,
            "registered": registered,
            "registered_capability": registered,
            "registry_state": resolve_state(sport, registered=registered, probe=probe),
            "certification_status": certification,
            "certification_id": certification_id,
            "supported_regimes": list(
                registration.supported_regimes if registered else ()
            ),
            "model_artifact_present": bool(probe.fitted_module) and probe.scorer_resolvable,
            "adapter_importable": probe.adapter_importable,
            "implementation_resolvable": probe.scorer_resolvable,
            "scorer_resolvable": bool(registered and probe.scorer_resolvable),
            "controlling_specialist": (
                registration.controlling_specialist if registered else None
            ),
            "adapter": registration.adapter_name if registered else None,
            "required_inputs": list(
                registration.required_inputs
                if registered
                else TEAM_EVENT_INPUT_CONTRACTS.get(sport, ())
            ),
            "feature_consumption_receipt_enabled": registered,
            "feature_schema_version": (
                registration.feature_schema_version if registered else None
            ),
            "feature_role_contract_declared": bool(
                registered and registration.feature_roles
            ),
            "critical_features": list(
                registration.critical_features if registered else ()
            ),
            "discovery_supported": sport in EXPECTED_TEAM_EVENT_SPORTS,
            "reason_if_unavailable": None if registered else probe.notes,
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


def _register_nfl_bridge_if_available() -> bool:
    """Register the NFL bridge when its implementation chain actually imports.

    An import failure is left as ``ADAPTER_MISSING`` rather than being turned
    into a half-registered bridge: a sport that cannot import its adapter must
    stay MODEL_UNAVAILABLE, not become a route that fails at score time.
    """
    if os.getenv("WOW_V17_NFL_TEAM_EVENT_BRIDGE", "1") != "1":
        return False
    try:
        from v17.nfl_team_event_publication import score_nfl_team_event_request
    except Exception:  # noqa: BLE001 - absence is an audit answer, not a crash
        return False

    def scorer(req: Any, *, event_api: Any, canonical_hydration_required: bool = False) -> dict[str, Any]:
        return score_nfl_team_event_request(
            _base_runtime,
            req,
            event_api=event_api,
            canonical_hydration_required=canonical_hydration_required,
        )

    register_team_event_bridge(
        "NFL",
        adapter_name="V17_NFL_TEAM_EVENT_BRIDGE",
        controlling_specialist=NFL_CONTROLLING_SPECIALIST,
        scorer=scorer,
        required_inputs=TEAM_EVENT_INPUT_CONTRACTS["NFL"],
        # The NFL publication chain owns its own canonical acquisition, input
        # resolution and governed-package construction, exactly as MLB does.
        # Re-validating it here would apply a second, different contract.
        standard_package_validation=False,
        # The fitted bundle trains on regular-season cohorts (2021-2023 train,
        # 2024 calibration, 2025 validation) and carries no preseason or playoff
        # calibration evidence, so it is registered for regular season only.
        supported_regimes=(REGULAR_SEASON,),
    )
    return True


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

    # NFL has a fitted outright-win bundle, a sport-specific evidence adapter, a
    # governed publication chain and its own acceptance tests, all reachable
    # from this process. It was already wired as an additive wrapper keyed on a
    # narrow sport alias, which meant the registry — and therefore /health —
    # reported NFL as unavailable while NFL scoring actually worked, and a
    # request naming sport="FOOTBALL" missed the wrapper entirely.
    #
    # Registering it makes one registry the single answer for both. This does
    # not certify an NFL specialist artifact: champion promotion stays a runtime
    # condition, and with no promoted champion the scorer still fails closed as
    # MODEL_UNAVAILABLE at score time.
    _register_nfl_bridge_if_available()

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
