"""WOW v16 ML research-readiness registry.

This module is intentionally *non-terminal*. It reports whether the current
research/model infrastructure for an outright-winner lane is structured enough
to support governed probability work. It never scores an event, publishes a
probability, changes a terminal ceiling, or authorizes execution.

The distinction is deliberate:

* research_readiness describes the maturity of the structured research packet;
* model_capability describes whether a fitted, governed event model is actually
  available at the codebase/capability-baseline layer;
* neither field is a substitute for event-specific Full Model gates.

Every response is therefore marked as a CODEBASE_CAPABILITY_BASELINE and
requires event-specific refresh. A baseline AVAILABLE model may still fail
closed for a particular event when live status, evidence, calibration, identity,
or another mandatory gate is missing.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Final, Literal, Optional


ResearchReadiness = Literal["STRONG", "MODERATE", "INCOMPLETE"]
ModelCapability = Literal["AVAILABLE", "INCOMPLETE", "MODEL_UNAVAILABLE"]
ComponentStatus = Literal["READY", "PARTIAL", "INCOMPLETE", "UNAVAILABLE"]

SUPPORTED_ML_READINESS_SPORTS: Final[tuple[str, ...]] = (
    "MLB",
    "NFL",
    "NBA",
    "WNBA",
    "TENNIS",
    "NCAAF",
)

# These are capability/readiness profiles, not a live event evidence packet.
# Event-specific fields (starter/QB/lineup/injuries/weather/etc.) must still be
# hydrated and refreshed by the Full Model for each candidate.
_ML_RESEARCH_READINESS: Final[dict[str, dict]] = {
    "MLB": {
        "sport": "MLB",
        "research_readiness": "STRONG",
        "model_capability": "AVAILABLE",
        "priority": "REFERENCE_IMPLEMENTATION",
        "components": {
            "exact_event_identity_contract": "READY",
            "starter_and_lineup_evidence_contract": "READY",
            "weather_and_venue_context": "READY",
            "matchup_research_framework": "READY",
            "failure_path_framework": "READY",
            "fitted_event_model": "READY",
            "event_calibrator": "READY",
            "calibrated_lower_bound": "READY",
        },
        "capability_evidence": {
            "provider_identity": "WOW_MLB_EVENT_FITTED_MODEL_V1",
            "model_artifact_version": "MLB_V16_V2D_CONTEXT_SHARED_SIM_R1",
            "feature_schema_version": "MLB_V2D_CONTEXT_V1",
            "lifecycle_state": "PROSPECTIVE_CERTIFIED",
            "certification_id": "V16-PROSPECTIVE-20260831-MLB-EVENT-R1",
            "calibration_health_requirement": "PASS",
            "minimum_simulations": 50000,
        },
        "research_requirements": [
            "starting pitchers and lineup status",
            "team offense and pitcher matchup context",
            "bullpen availability and workload",
            "park and weather context",
            "defense/baserunning and late-game environment",
            "two-sided failure-path analysis",
        ],
        "failure_regimes": [
            "starter underperformance or early exit",
            "bullpen collapse or availability shock",
            "lineup/status change",
            "weather/park run-environment change",
            "extra-inning tail risk",
        ],
        "blockers": [],
        "main_research_gap": "NONE_AT_MODEL_RESEARCH_LAYER",
    },
    "NFL": {
        "sport": "NFL",
        "research_readiness": "INCOMPLETE",
        "model_capability": "MODEL_UNAVAILABLE",
        "priority": "P0",
        "components": {
            "exact_event_identity_contract": "PARTIAL",
            "qb_status_and_injury_context": "INCOMPLETE",
            "rest_travel_weather_context": "PARTIAL",
            "epa_success_rate_feature_pipeline": "INCOMPLETE",
            "pressure_explosive_special_teams_features": "INCOMPLETE",
            "failure_path_framework": "PARTIAL",
            "fitted_event_model": "UNAVAILABLE",
            "event_calibrator": "UNAVAILABLE",
            "calibrated_lower_bound": "UNAVAILABLE",
        },
        "research_requirements": [
            "QB-adjusted EPA/play",
            "offensive and defensive EPA",
            "pass and rush success rates",
            "pressure and sack rates",
            "explosive-play rates",
            "special-teams strength",
            "turnover regression",
            "home/rest/travel context",
            "weather",
            "injuries and offensive-line continuity",
        ],
        "failure_regimes": [
            "QB degradation or injury",
            "pass-protection collapse",
            "turnover-tail outcome",
            "special-teams swing",
            "material weather disruption",
        ],
        "blockers": [
            "NFL_STRUCTURED_FEATURE_PIPELINE_INCOMPLETE",
            "NFL_FITTED_EVENT_MODEL_UNAVAILABLE",
            "NFL_EVENT_CALIBRATOR_UNAVAILABLE",
        ],
        "main_research_gap": "CERTIFIED_NFL_EVENT_MODEL_AND_STRUCTURED_FEATURE_PIPELINE",
    },
    "NBA": {
        "sport": "NBA",
        "research_readiness": "INCOMPLETE",
        "model_capability": "MODEL_UNAVAILABLE",
        "priority": "P1",
        "components": {
            "exact_event_identity_contract": "PARTIAL",
            "projected_lineup_and_player_status": "INCOMPLETE",
            "rest_back_to_back_travel_context": "PARTIAL",
            "adjusted_net_rating_feature_pipeline": "INCOMPLETE",
            "pace_shot_quality_rebounding_turnover_features": "INCOMPLETE",
            "failure_path_framework": "PARTIAL",
            "fitted_event_model": "UNAVAILABLE",
            "event_calibrator": "UNAVAILABLE",
            "calibrated_lower_bound": "UNAVAILABLE",
        },
        "research_requirements": [
            "adjusted net rating",
            "expected possessions and pace",
            "projected lineups and player impact",
            "rest/back-to-back/travel context",
            "shot quality and three-point variance",
            "rebounding",
            "turnovers",
            "foul environment",
            "home-court context",
        ],
        "failure_regimes": [
            "star scratch or restriction",
            "bench-unit collapse",
            "extreme shooting variance",
            "foul-trouble regime",
            "late lineup/status change",
        ],
        "blockers": [
            "NBA_STRUCTURED_FEATURE_PIPELINE_INCOMPLETE",
            "NBA_FITTED_EVENT_MODEL_UNAVAILABLE",
            "NBA_EVENT_CALIBRATOR_UNAVAILABLE",
        ],
        "main_research_gap": "CERTIFIED_NBA_EVENT_MODEL_AND_STRUCTURED_FEATURE_PIPELINE",
    },
    "WNBA": {
        "sport": "WNBA",
        "research_readiness": "MODERATE",
        "model_capability": "INCOMPLETE",
        "priority": "P2",
        "components": {
            "exact_event_identity_contract": "PARTIAL",
            "lineup_role_status_context": "PARTIAL",
            "team_matchup_research_framework": "PARTIAL",
            "rest_travel_context": "PARTIAL",
            "failure_path_framework": "PARTIAL",
            "fitted_event_model": "UNAVAILABLE",
            "event_calibrator": "UNAVAILABLE",
            "calibrated_lower_bound": "UNAVAILABLE",
        },
        "research_requirements": [
            "projected availability and starting lineup",
            "adjusted team strength and recent role context",
            "pace and efficiency matchup",
            "rest/travel",
            "rebounding and turnover matchup",
            "shot-distribution variance",
        ],
        "failure_regimes": [
            "star availability change",
            "rotation/role discontinuity",
            "three-point variance",
            "rebounding/turnover tail",
        ],
        "blockers": [
            "WNBA_FULL_GAME_EVENT_MODEL_INCOMPLETE",
            "WNBA_EVENT_CALIBRATOR_UNAVAILABLE",
        ],
        "main_research_gap": "FITTED_WNBA_GAME_WIN_MODEL_AND_CALIBRATION_PIPELINE",
    },
    "TENNIS": {
        "sport": "TENNIS",
        "research_readiness": "MODERATE",
        "model_capability": "INCOMPLETE",
        "priority": "P2",
        "components": {
            "exact_event_identity_contract": "PARTIAL",
            "surface_and_format_context": "PARTIAL",
            "serve_return_matchup_features": "PARTIAL",
            "fitness_and_schedule_context": "PARTIAL",
            "failure_path_framework": "PARTIAL",
            "fitted_event_model": "UNAVAILABLE",
            "event_calibrator": "UNAVAILABLE",
            "calibrated_lower_bound": "UNAVAILABLE",
        },
        "research_requirements": [
            "surface-adjusted serve and return strength",
            "hold/break distributions",
            "format and tiebreak rules",
            "opponent-quality adjusted form",
            "fitness/injury and workload",
            "travel/schedule context",
        ],
        "failure_regimes": [
            "fitness or injury degradation",
            "serve-performance collapse",
            "tiebreak/high-leverage variance",
            "surface mismatch",
        ],
        "blockers": [
            "TENNIS_FITTED_MATCH_WIN_MODEL_INCOMPLETE",
            "TENNIS_EVENT_CALIBRATOR_UNAVAILABLE",
        ],
        "main_research_gap": "FITTED_TENNIS_MATCH_WIN_MODEL_AND_CALIBRATION_PIPELINE",
    },
    "NCAAF": {
        "sport": "NCAAF",
        "research_readiness": "INCOMPLETE",
        "model_capability": "MODEL_UNAVAILABLE",
        "priority": "P3",
        "live_readiness_source": "/internal/ncaaf/readiness",
        "components": {
            "exact_event_identity_contract": "PARTIAL",
            "roster_qb_status_context": "INCOMPLETE",
            "team_efficiency_feature_pipeline": "INCOMPLETE",
            "schedule_strength_and_game_state_features": "INCOMPLETE",
            "failure_path_framework": "PARTIAL",
            "fitted_event_model": "UNAVAILABLE",
            "event_calibrator": "UNAVAILABLE",
            "calibrated_lower_bound": "UNAVAILABLE",
        },
        "research_requirements": [
            "QB and roster availability",
            "opponent-adjusted offensive/defensive efficiency",
            "success/explosiveness and havoc",
            "special teams",
            "tempo and game-state tendencies",
            "schedule strength",
            "home/travel/weather context",
        ],
        "failure_regimes": [
            "QB/roster status shock",
            "explosive-play tail",
            "turnover/havoc tail",
            "special-teams swing",
            "game-state divergence",
        ],
        "blockers": [
            "NCAAF_STRUCTURED_FEATURE_PIPELINE_INCOMPLETE",
            "NCAAF_FITTED_EVENT_MODEL_UNAVAILABLE",
            "NCAAF_EVENT_CALIBRATOR_UNAVAILABLE",
        ],
        "main_research_gap": "NCAAF_HISTORICAL_FEATURE_MODEL_AND_CALIBRATION_PIPELINE",
    },
}


def _decorate(profile: dict) -> dict:
    """Attach invariants common to every readiness response."""
    result = deepcopy(profile)
    result.update(
        {
            "runtime": "WOW_v16_CLEAN_CORE",
            "market_family": "OUTRIGHT_WINNER",
            "status_basis": "CODEBASE_CAPABILITY_BASELINE",
            "requires_event_specific_refresh": True,
            "readiness_is_terminal_gate": False,
            "probability_publishable_from_readiness": False,
            "terminal_ceiling_effect": "NONE",
            "can_execute": False,
        }
    )
    return result


def get_ml_research_readiness(sport: Optional[str] = None) -> dict:
    """Return one sport profile or the full six-sport readiness registry.

    Raises KeyError for unsupported sports. The API layer maps that to 404.
    """
    if sport is not None:
        key = sport.strip().upper()
        if key not in _ML_RESEARCH_READINESS:
            raise KeyError(key)
        return _decorate(_ML_RESEARCH_READINESS[key])

    return {
        "runtime": "WOW_v16_CLEAN_CORE",
        "market_family": "OUTRIGHT_WINNER",
        "status_basis": "CODEBASE_CAPABILITY_BASELINE",
        "requires_event_specific_refresh": True,
        "readiness_is_terminal_gate": False,
        "probability_publishable_from_readiness": False,
        "terminal_ceiling_effect": "NONE",
        "can_execute": False,
        "sports": [_decorate(_ML_RESEARCH_READINESS[s]) for s in SUPPORTED_ML_READINESS_SPORTS],
    }


def validate_registry_invariants() -> None:
    """Fail at import/test time if this transparency layer drifts into scoring."""
    if tuple(_ML_RESEARCH_READINESS) != SUPPORTED_ML_READINESS_SPORTS:
        raise RuntimeError("ML readiness registry sport set/order drifted")

    allowed_research = {"STRONG", "MODERATE", "INCOMPLETE"}
    allowed_model = {"AVAILABLE", "INCOMPLETE", "MODEL_UNAVAILABLE"}
    allowed_components = {"READY", "PARTIAL", "INCOMPLETE", "UNAVAILABLE"}
    prohibited_probability_keys = {
        "model_probability",
        "raw_probability",
        "unconditional_probability",
        "calibrated_probability",
        "calibrated_probability_lower_bound",
        "calibrated_probability_upper_bound",
    }

    for sport, profile in _ML_RESEARCH_READINESS.items():
        if profile["research_readiness"] not in allowed_research:
            raise RuntimeError(f"invalid research_readiness for {sport}")
        if profile["model_capability"] not in allowed_model:
            raise RuntimeError(f"invalid model_capability for {sport}")
        if not set(profile["components"].values()).issubset(allowed_components):
            raise RuntimeError(f"invalid component status for {sport}")
        if prohibited_probability_keys.intersection(profile):
            raise RuntimeError(f"readiness profile must never publish probability fields for {sport}")


validate_registry_invariants()
