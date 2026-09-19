"""Production V17 bridge registrations for governed multisport team/event scoring.

This module is additive to the mature MLB and NFL paths. It registers only exact
sport specialists and never falls back to market implied probability, generic
LLM reasoning, or another sport's model. Certification is activated only after
the exact scorer is importable and registration succeeds; a failed install rolls
back both certification and development-state mutations.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import HTTPException

import v17.team_event_bridge_runtime as bridges
import v17.team_event_request_runtime as base_runtime
import v17.team_event_capability_manifest as capability_manifest
import v17.team_event_model_development_manifest as development_manifest
from v17.llp_governed_package_scoring import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
)
from v17.multisport_team_event_governance import (
    FINAL_APPROVED,
    GLOBAL_TERMINAL_REDUCER,
    reduce_multisport_team_event,
)
from v17.multisport_team_event_models import (
    MODEL_SCORERS,
    MODEL_SPECS,
    ModelInputsInsufficient,
    ModelOutputInvalid,
    ModelScorerFailed,
)
from v17.team_event_official_publication_guard import (
    evaluate_team_event_official_publication,
)

CAN_EXECUTE = False
_INSTALLED = False

SPORT_MODEL_INPUTS: dict[str, tuple[str, ...]] = {
    "WNBA": ("home_win_pct", "away_win_pct"),
    "NHL": (
        "home_elo",
        "away_elo",
        "home_goalie_sv_pct",
        "away_goalie_sv_pct",
        "home_pp_pct",
        "away_pk_pct",
    ),
    "SOCCER": ("home_xg_per_game", "away_xg_per_game"),
    # Tennis owns multiple alternative numeric paths; the scorer resolves them
    # and returns MODEL_INPUTS_INSUFFICIENT when none is present.
    "TENNIS": (),
    "MMA": ("fight_history",),
}

RUNTIME_CERTIFICATIONS: dict[str, str] = {
    "WNBA": capability_manifest.WNBA_GAME_WIN_PROBABILITY_EXPERT,
    "NHL": capability_manifest.NHL_GAME_WIN_PROBABILITY_EXPERT,
    "SOCCER": capability_manifest.SOCCER_1X2_WIN_PROBABILITY_EXPERT,
    "TENNIS": capability_manifest.TENNIS_MATCH_WIN_PROBABILITY_EXPERT,
    "MMA": capability_manifest.MMA_FIGHT_WIN_PROBABILITY_EXPERT,
}

RUNTIME_DEVELOPMENT_LANES: dict[str, tuple[str, str]] = {
    "WNBA": ("WNBA_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "NHL": ("NHL_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "SOCCER": ("SOCCER_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "TENNIS": ("TENNIS_TEAM_EVENT_PROBABILITY", "LIVE_GOVERNANCE_ACCEPTANCE"),
    "MMA": (
        "MMA_TEAM_EVENT_PROBABILITY",
        "LIVE_GOVERNANCE_ACCEPTANCE_AND_HISTORY_HYDRATION",
    ),
}


def _typed_failure(req: Any, exc: Exception) -> HTTPException:
    if isinstance(exc, ModelInputsInsufficient):
        return bridges._model_failure_detail(
            req,
            code=MODEL_INPUTS_INSUFFICIENT,
            blocker=exc.reason,
            status_code=422,
            extra={
                "missing_fields": list(exc.missing_fields),
                "model_invoked": False,
            },
        )
    if isinstance(exc, ModelOutputInvalid):
        return bridges._model_failure_detail(
            req,
            code=MODEL_OUTPUT_INVALID,
            blocker=str(exc),
            status_code=500,
            extra={"model_invoked": True},
        )
    return bridges._model_failure_detail(
        req,
        code=MODEL_SCORER_FAILED,
        blocker=str(exc) or "MULTISPORT_TEAM_EVENT_SCORER_FAILED",
        status_code=503,
        extra={"model_invoked": True, "error_type": type(exc).__name__},
    )


def _route_contract(req: Any) -> Any:
    try:
        route = base_runtime.resolve_host_route(
            req.requester_host_identity,
            req.candidate_family,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": str(exc),
                "probability_publishable": False,
                "rank_eligible": False,
                "can_execute": False,
            },
        ) from exc
    if route.controlling_engine_identity != base_runtime.LLP_TEAM_BETTING_ENGINE:
        raise HTTPException(
            status_code=422,
            detail=base_runtime._augment_detail(
                {
                    "code": "TEAM_EVENT_CONTROLLING_ENGINE_MISMATCH",
                    "probability_publishable": False,
                    "rank_eligible": False,
                },
                req,
            ),
        )
    errors = base_runtime._base_errors(req)
    if errors:
        raise HTTPException(
            status_code=422,
            detail=base_runtime._augment_detail(
                {
                    "code": "TEAM_EVENT_CONTRACT_INVALID",
                    "errors": errors,
                    "probability_publishable": False,
                    "rank_eligible": False,
                },
                req,
            ),
        )
    return route


def _score_one(
    sport: str,
    req: Any,
    *,
    event_api: Any,
    canonical_hydration_required: bool = False,
) -> dict[str, Any]:
    del event_api, canonical_hydration_required
    route = _route_contract(req)
    scout_research_barrier = base_runtime._run_mandatory_scout_research(req)
    scorer = MODEL_SCORERS[sport]
    try:
        package = scorer(req)
    except (ModelInputsInsufficient, ModelOutputInvalid, ModelScorerFailed) as exc:
        raise _typed_failure(req, exc) from exc
    except Exception as exc:  # noqa: BLE001 - convert unknown scorer faults to typed failure
        raise _typed_failure(req, ModelScorerFailed(type(exc).__name__)) from exc

    governance = reduce_multisport_team_event(req, package)
    passed = (
        governance.get("status") == "PASS"
        and governance.get("terminal_label") == FINAL_APPROVED
        and governance.get("probability_publishable") is True
        and governance.get("rank_eligible") is True
        and governance.get("global_terminal_reducer") == GLOBAL_TERMINAL_REDUCER
        and governance.get("can_execute") is False
    )
    result = {
        **package,
        "requester_host_identity": route.requester_host_identity,
        "controlling_engine_identity": base_runtime.LLP_TEAM_BETTING_ENGINE,
        "candidate_family": route.candidate_family,
        "scout_research_barrier": scout_research_barrier,
        "llp_governance": governance,
        "llp_probability_audit_result": governance.get("probability_audit_result"),
        "event_mutex_status": governance.get("event_mutex_status"),
        "global_terminal_authority": GLOBAL_TERMINAL_REDUCER,
        "terminal_label": governance.get("terminal_label"),
        "terminal_ceiling": governance.get("terminal_ceiling"),
        "probability_publishable": passed,
        "rank_eligible": passed,
        "code": (
            "GOVERNED_PROBABILITY_PUBLISHED"
            if passed
            else "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
        ),
        "blockers": list(governance.get("blockers") or []),
        "blend_publishable": False,
        "host_terminal_authority": False,
        "can_execute": False,
    }

    official = evaluate_team_event_official_publication(result)
    result["official_publication_guard"] = official
    if official.get("official_publication_allowed") is not True:
        result["probability_publishable"] = False
        result["rank_eligible"] = False
        result["terminal_label"] = "MODEL_QUALIFIED_HOLD"
        result["terminal_ceiling"] = "MODEL_QUALIFIED_HOLD"
        result["code"] = "LLP_EVENT_GOVERNANCE_NOT_PROVEN"
        result["blockers"] = list(
            dict.fromkeys(
                [
                    *result.get("blockers", []),
                    *(official.get("blockers") or []),
                ]
            )
        )
    return result


def _make_scorer(sport: str) -> Callable[..., dict[str, Any]]:
    def scorer(
        req: Any,
        *,
        event_api: Any,
        canonical_hydration_required: bool = False,
    ) -> dict[str, Any]:
        return _score_one(
            sport,
            req,
            event_api=event_api,
            canonical_hydration_required=canonical_hydration_required,
        )

    scorer.__name__ = f"score_{sport.lower()}_team_event_request"
    return scorer


score_wnba_team_event_request = _make_scorer("WNBA")
score_nhl_team_event_request = _make_scorer("NHL")
score_soccer_team_event_request = _make_scorer("SOCCER")
score_tennis_team_event_request = _make_scorer("TENNIS")
score_mma_team_event_request = _make_scorer("MMA")

BRIDGE_SCORERS: dict[str, Callable[..., dict[str, Any]]] = {
    "WNBA": score_wnba_team_event_request,
    "NHL": score_nhl_team_event_request,
    "SOCCER": score_soccer_team_event_request,
    "TENNIS": score_tennis_team_event_request,
    "MMA": score_mma_team_event_request,
}


def _activate_runtime_certification(sport: str) -> None:
    capability_manifest.CERTIFIED_TEAM_EVENT_SPORTS[sport] = RUNTIME_CERTIFICATIONS[sport]
    capability_manifest.KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS = frozenset(
        value
        for value in capability_manifest.EXPECTED_TEAM_EVENT_SPORTS
        if value not in capability_manifest.CERTIFIED_TEAM_EVENT_SPORTS
    )
    maintenance_lane, next_gate = RUNTIME_DEVELOPMENT_LANES[sport]
    development_manifest.TEAM_EVENT_MODEL_DEVELOPMENT[sport] = (
        development_manifest.ModelDevelopmentLane(
            sport,
            "PRODUCTION_MODEL_PRESENT",
            maintenance_lane,
            next_gate,
        )
    )


def install_multisport_team_event_bridges() -> dict[str, Any]:
    """Atomically activate the five exact multisport bridge contracts."""
    global _INSTALLED
    if _INSTALLED:
        return {
            "status": "ALREADY_INSTALLED",
            "registered_sports": sorted(BRIDGE_SCORERS),
            "can_execute": False,
        }

    previous_bridges = dict(bridges.TEAM_EVENT_BRIDGES)
    previous_certifications = dict(capability_manifest.CERTIFIED_TEAM_EVENT_SPORTS)
    previous_uncertified = capability_manifest.KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS
    previous_development = {
        sport: development_manifest.TEAM_EVENT_MODEL_DEVELOPMENT[sport]
        for sport in BRIDGE_SCORERS
    }
    registered: list[str] = []
    try:
        for sport, scorer in BRIDGE_SCORERS.items():
            spec = MODEL_SPECS[sport]
            # The imported scorer identity and certification identity must agree
            # before either can become visible as production capability.
            if spec["specialist"] != RUNTIME_CERTIFICATIONS[sport]:
                raise RuntimeError(f"{sport}_SPECIALIST_CERTIFICATION_IDENTITY_MISMATCH")

            required = tuple(
                dict.fromkeys(
                    [
                        *capability_manifest.TEAM_EVENT_INPUT_CONTRACTS[sport],
                        *SPORT_MODEL_INPUTS[sport],
                    ]
                )
            )
            bridges.register_team_event_bridge(
                sport,
                adapter_name=f"V17_{sport}_TEAM_EVENT_BRIDGE",
                controlling_specialist=spec["specialist"],
                scorer=scorer,
                required_inputs=required,
                standard_package_validation=True,
            )
            _activate_runtime_certification(sport)
            registered.append(sport)
    except Exception:
        bridges.TEAM_EVENT_BRIDGES.clear()
        bridges.TEAM_EVENT_BRIDGES.update(previous_bridges)
        capability_manifest.CERTIFIED_TEAM_EVENT_SPORTS.clear()
        capability_manifest.CERTIFIED_TEAM_EVENT_SPORTS.update(previous_certifications)
        capability_manifest.KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS = previous_uncertified
        for sport, lane in previous_development.items():
            development_manifest.TEAM_EVENT_MODEL_DEVELOPMENT[sport] = lane
        raise

    bridges._install_health_overlay()
    _INSTALLED = True
    return {
        "status": "INSTALLED",
        "registered_sports": sorted(registered),
        "certified_sports": sorted(RUNTIME_CERTIFICATIONS),
        "global_terminal_authority": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "BRIDGE_SCORERS",
    "CAN_EXECUTE",
    "RUNTIME_CERTIFICATIONS",
    "SPORT_MODEL_INPUTS",
    "install_multisport_team_event_bridges",
    "score_mma_team_event_request",
    "score_nhl_team_event_request",
    "score_soccer_team_event_request",
    "score_tennis_team_event_request",
    "score_wnba_team_event_request",
]
