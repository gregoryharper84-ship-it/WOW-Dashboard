"""Production V17 bridge registrations for governed multisport team/event scoring.

This module is additive to the mature MLB and NFL paths. It registers only exact
sport specialists and never falls back to market implied probability, generic
LLM reasoning, or another sport's model. Certification is derived from the exact
live registration identity rather than mutating the static certification catalog.

Raw sporting models are not publishable by themselves. Every new sport must also
supply a fitted, health-PASS calibration artifact tied to that exact model family
and version. Missing/invalid calibration is MODEL_INPUTS_INSUFFICIENT, not a
manufactured lower bound.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Callable, Mapping

from fastapi import HTTPException

import v17.team_event_bridge_runtime as bridges
import v17.team_event_request_runtime as base_runtime
import v17.team_event_capability_manifest as capability_manifest
from v17.llp_governed_package_scoring import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
)
from v17.multisport_team_event_calibration import (
    CalibrationArtifactInvalid,
    apply_binary_artifact,
    apply_multiclass_artifact,
    artifact_fingerprint,
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
    "WNBA": ("home_win_pct", "away_win_pct", "calibration_artifact"),
    "NHL": (
        "home_elo",
        "away_elo",
        "home_goalie_sv_pct",
        "away_goalie_sv_pct",
        "home_pp_pct",
        "away_pk_pct",
        "calibration_artifact",
    ),
    "SOCCER": ("home_xg_per_game", "away_xg_per_game", "calibration_artifact"),
    # Tennis owns multiple alternative numeric paths; the scorer resolves them.
    "TENNIS": ("calibration_artifact",),
    "MMA": ("fight_history", "calibration_artifact"),
}

RUNTIME_CERTIFICATIONS: dict[str, str] = dict(
    capability_manifest.ACTIVATABLE_TEAM_EVENT_CERTIFICATIONS
)


def _typed_failure(req: Any, exc: Exception, *, model_invoked: bool | None = None) -> HTTPException:
    if isinstance(exc, CalibrationArtifactInvalid):
        return bridges._model_failure_detail(
            req,
            code=MODEL_INPUTS_INSUFFICIENT,
            blocker="CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE",
            status_code=422,
            extra={
                "missing_fields": ["calibration_artifact"],
                "calibration_blockers": list(exc.blockers),
                "model_invoked": True if model_invoked is None else model_invoked,
            },
        )
    if isinstance(exc, ModelInputsInsufficient):
        return bridges._model_failure_detail(
            req,
            code=MODEL_INPUTS_INSUFFICIENT,
            blocker=exc.reason,
            status_code=422,
            extra={
                "missing_fields": list(exc.missing_fields),
                "model_invoked": False if model_invoked is None else model_invoked,
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


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _freshness_penalty(evidence: Mapping[str, Any]) -> float:
    age = _number(evidence.get("status_freshness_hours"))
    if age is None or age < 0:
        return 0.01
    if age <= 1.0:
        return 0.0
    if age <= 4.0:
        return 0.01
    if age <= 12.0:
        return 0.02
    return 0.04


def _parse_time(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _artifact_for(req: Any, package: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = getattr(req, "sport_specific_evidence", None)
    artifact = evidence.get("calibration_artifact") if isinstance(evidence, Mapping) else None
    if not isinstance(artifact, Mapping):
        raise CalibrationArtifactInvalid(["CALIBRATION_ARTIFACT_MISSING"])
    if str(artifact.get("model_family") or "") != str(package.get("model_family") or ""):
        raise CalibrationArtifactInvalid(["CALIBRATION_MODEL_FAMILY_MISMATCH"])
    if str(artifact.get("model_version") or "") != str(package.get("model_version") or ""):
        raise CalibrationArtifactInvalid(["CALIBRATION_MODEL_VERSION_MISMATCH"])
    fit_end = _parse_time(artifact.get("fit_end"))
    model_at = _parse_time(package.get("immutable_model_timestamp"))
    if fit_end is None or model_at is None or fit_end > model_at:
        raise CalibrationArtifactInvalid(["CALIBRATION_FIT_END_AFTER_OR_INVALID_FOR_MODEL_TIMESTAMP"])
    return artifact


def _uncertainty_width(
    historical_residual: float,
    package: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> tuple[float, dict[str, float]]:
    disagreement = max(0.0, _number(package.get("model_disagreement")) or 0.0)
    disagreement_add = min(disagreement * 0.50, 0.08)
    freshness_add = _freshness_penalty(evidence)
    width = min(0.40, max(0.005, historical_residual + disagreement_add + freshness_add))
    return width, {
        "historical_residual_quantile_90": round(historical_residual, 6),
        "model_disagreement_additive": round(disagreement_add, 6),
        "status_freshness_additive": round(freshness_add, 6),
    }


def _apply_governed_calibration(req: Any, package: Mapping[str, Any]) -> dict[str, Any]:
    """Replace provisional model bounds with history-backed governed calibration."""
    sport = str(package.get("sport") or getattr(req, "sport", "") or "").upper()
    evidence = getattr(req, "sport_specific_evidence", None)
    evidence_map = dict(evidence) if isinstance(evidence, Mapping) else {}
    artifact = _artifact_for(req, package)
    out = dict(package)

    if sport == "SOCCER":
        raw_states = {
            "HOME": float(package["raw_home_probability"]),
            "DRAW": float(package["raw_draw_probability"]),
            "AWAY": float(package["raw_away_probability"]),
        }
        calibrated = apply_multiclass_artifact(raw_states, artifact)
        states = calibrated["calibrated_outcomes"]
        residuals = calibrated["historical_residual_quantiles_90"]
        widths: dict[str, float] = {}
        components: dict[str, dict[str, float]] = {}
        for key in ("HOME", "DRAW", "AWAY"):
            widths[key], components[key] = _uncertainty_width(
                float(residuals[key]), package, evidence_map
            )
        home = float(states["HOME"])
        draw = float(states["DRAW"])
        away = float(states["AWAY"])
        out.update(
            {
                "raw_three_state_1x2": {k.lower(): round(v, 6) for k, v in raw_states.items()},
                "three_state_1x2": {
                    "home": round(home, 6),
                    "draw": round(draw, 6),
                    "away": round(away, 6),
                },
                "calibrated_probability": round(home, 6),
                "calibrated_lower_bound": round(max(0.001, home - widths["HOME"]), 6),
                "calibrated_upper_bound": round(min(0.999, home + widths["HOME"]), 6),
                "calibrated_home_probability": round(home, 6),
                "calibrated_home_lower_bound": round(max(0.001, home - widths["HOME"]), 6),
                "calibrated_home_upper_bound": round(min(0.999, home + widths["HOME"]), 6),
                "calibrated_draw_probability": round(draw, 6),
                "calibrated_draw_lower_bound": round(max(0.001, draw - widths["DRAW"]), 6),
                "calibrated_draw_upper_bound": round(min(0.999, draw + widths["DRAW"]), 6),
                "calibrated_away_probability": round(away, 6),
                "calibrated_away_lower_bound": round(max(0.001, away - widths["AWAY"]), 6),
                "calibrated_away_upper_bound": round(min(0.999, away + widths["AWAY"]), 6),
                "dynamic_uncertainty": round(widths["HOME"], 6),
                "uncertainty_components": components,
            }
        )
    else:
        calibrated = apply_binary_artifact(
            float(package["raw_model_probability"]), artifact, sport
        )
        point = float(calibrated["calibrated_probability"])
        width, components = _uncertainty_width(
            float(calibrated["historical_residual_quantile_90"]),
            package,
            evidence_map,
        )
        away = 1.0 - point
        out.update(
            {
                "calibrated_probability": round(point, 6),
                "calibrated_lower_bound": round(max(0.001, point - width), 6),
                "calibrated_upper_bound": round(min(0.999, point + width), 6),
                "calibrated_home_probability": round(point, 6),
                "calibrated_home_lower_bound": round(max(0.001, point - width), 6),
                "calibrated_home_upper_bound": round(min(0.999, point + width), 6),
                "calibrated_away_probability": round(away, 6),
                "calibrated_away_lower_bound": round(max(0.001, away - width), 6),
                "calibrated_away_upper_bound": round(min(0.999, away + width), 6),
                "dynamic_uncertainty": round(width, 6),
                "uncertainty_components": components,
            }
        )

    out.update(
        {
            "calibration_method": calibrated["calibration_method"],
            "calibration_version": calibrated["calibration_version"],
            "calibration_health_status": calibrated["calibration_health_status"],
            "calibration_status": "PASS",
            "calibration_sample_scope": f"{sport}_HISTORICAL_TIME_SPLIT",
            "effective_sample_n": calibrated["calibration_training_n"],
            "calibration_training_n": calibrated["calibration_training_n"],
            "calibration_brier_score": calibrated["calibration_brier_score"],
            "calibration_error": calibrated["calibration_error"],
            "calibration_source_data_hash": calibrated["calibration_source_data_hash"],
            "calibration_split_hash": calibrated["calibration_split_hash"],
            "calibration_fit_end": calibrated["calibration_fit_end"],
            "calibration_artifact_fingerprint": artifact_fingerprint(artifact),
            "calibration_history_present": True,
            "calibration_artifact_certified": True,
            "confidence_level": "HISTORICAL_RESIDUAL_Q90",
            "uncertainty_method": "FITTED_CALIBRATION_RESIDUAL_PLUS_CURRENT_STATUS_AND_MODEL_DISAGREEMENT",
            "probability_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        }
    )
    return out


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
        raw_package = scorer(req)
    except (ModelInputsInsufficient, ModelOutputInvalid, ModelScorerFailed) as exc:
        raise _typed_failure(req, exc) from exc
    except Exception as exc:  # noqa: BLE001 - convert unknown scorer faults to typed failure
        raise _typed_failure(req, ModelScorerFailed(type(exc).__name__)) from exc

    try:
        package = _apply_governed_calibration(req, raw_package)
    except CalibrationArtifactInvalid as exc:
        raise _typed_failure(req, exc, model_invoked=True) from exc

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
    registered: list[str] = []
    try:
        for sport, scorer in BRIDGE_SCORERS.items():
            spec = MODEL_SPECS[sport]
            if spec["specialist"] != RUNTIME_CERTIFICATIONS[sport]:
                raise RuntimeError(
                    f"{sport}_SPECIALIST_CERTIFICATION_IDENTITY_MISMATCH"
                )

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
            registered.append(sport)
    except Exception:
        bridges.TEAM_EVENT_BRIDGES.clear()
        bridges.TEAM_EVENT_BRIDGES.update(previous_bridges)
        raise

    bridges._install_health_overlay()
    _INSTALLED = True
    return {
        "status": "INSTALLED",
        "registered_sports": sorted(registered),
        "runtime_certification_identities": dict(RUNTIME_CERTIFICATIONS),
        "calibration_required": True,
        "global_terminal_authority": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "BRIDGE_SCORERS",
    "CAN_EXECUTE",
    "RUNTIME_CERTIFICATIONS",
    "SPORT_MODEL_INPUTS",
    "_apply_governed_calibration",
    "install_multisport_team_event_bridges",
    "score_mma_team_event_request",
    "score_nhl_team_event_request",
    "score_soccer_team_event_request",
    "score_tennis_team_event_request",
    "score_wnba_team_event_request",
]
