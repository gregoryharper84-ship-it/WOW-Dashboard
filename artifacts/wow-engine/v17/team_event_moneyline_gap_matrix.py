"""Fail-closed Phase-0 audit matrix for V17 team/event MoneyLine coverage.

This module is observability only.  It does not score, calibrate, rank, register,
certify, promote, hydrate, or publish a sporting probability.  Its purpose is to
make cross-sport gaps explicit without turning repository implementation into
probability authority.

The matrix deliberately separates:
- route ownership from certification identity;
- implementation from runtime registration;
- production/runtime feature schemas from development-only schemas;
- calibration/hydration dependency modes from proof that those dependencies are
  satisfied for a particular prediction.

Unknown values stay ``None`` and produce an explicit gap.  Nothing here may be
used as a generic or legacy probability fallback.
"""
from __future__ import annotations

from typing import Any, Callable

from v17 import all_sport_capability_readiness as readiness
from v17.team_event_capability_manifest import (
    ACTIVATABLE_TEAM_EVENT_CERTIFICATIONS,
    CERTIFIED_TEAM_EVENT_SPORTS,
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
)
from v17.team_event_model_development_manifest import TEAM_EVENT_MODEL_DEVELOPMENT
from v17.team_event_model_registry_audit import CERTIFIED, audit_table

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
RANK_METRIC = "GOVERNED_CALIBRATED_LOWER_BOUND_WHERE_LANE_CONTRACT_REQUIRES"
GAP_MATRIX_VERSION = "V17_MONEYLINE_GAP_MATRIX_V1"

# These are route-ownership declarations already present in governed repository
# sources.  They are not certification claims.  A missing owner remains missing
# rather than being inferred from a model name or sport label.
_ROUTE_OWNERS: dict[str, tuple[str, str]] = {
    "MLB": (
        "MLB_GAME_WIN_PROBABILITY_EXPERT",
        "v17/team_event_capability_manifest.py:CERTIFIED_TEAM_EVENT_SPORTS",
    ),
    "NFL": (
        "wow.nfl-game-win-probability-expert",
        "nfl_event_model_contract.py:CONTROLLING_SPECIALIST",
    ),
    "NCAAF": (
        "wow.ncaaf-game-win-probability-expert",
        "migrations/20260915_first_six_team_event_ownership.sql",
    ),
    "NBA": (
        "wow.nba-game-win-probability-expert",
        "migrations/20260915_first_six_team_event_ownership.sql",
    ),
    "WNBA": (
        "wow.wnba-game-win-probability-expert",
        "migrations/20260915_first_six_team_event_ownership.sql",
    ),
    "NCAAB": (
        "wow.ncaab-game-win-probability-expert",
        "migrations/20260915_first_six_team_event_ownership.sql",
    ),
    "SOCCER": (
        "wow.soccer-match-win-probability-expert",
        "migrations/20260915_first_six_team_event_ownership.sql",
    ),
    "TENNIS": (
        "wow.tennis-match-win-probability-expert",
        "migrations/20260915_first_six_team_event_ownership.sql",
    ),
}

# Runtime schema means the repository exposes the exact schema contract for the
# governed team/event lane.  Candidate/development schemas are kept separate so
# their existence cannot silently promote production capability.
_RUNTIME_FEATURE_SCHEMAS: dict[str, tuple[str, str]] = {
    "NFL": (
        "NFL_EVENT_PREGAME_PRIOR_V1",
        "nfl_event_model_contract.py:FEATURE_SCHEMA_VERSION",
    ),
}

_DEVELOPMENT_FEATURE_SCHEMAS: dict[str, tuple[str, str]] = {
    "NBA": (
        "BASKETBALL_TEAM_EVENT_FEATURES_V2",
        "basketball_team_event_specialist.py:FEATURE_SCHEMA_VERSION",
    ),
    "WNBA": (
        "BASKETBALL_TEAM_EVENT_FEATURES_V2",
        "basketball_team_event_specialist.py:FEATURE_SCHEMA_VERSION",
    ),
    "NCAAF": (
        "NCAAF_FEATURES_V1",
        "ncaaf_feature_compiler.py:FEATURE_SCHEMA_VERSION",
    ),
}

LEGACY_MONEYLINE_PATHS: tuple[dict[str, Any], ...] = (
    {
        "path": "artifacts/flask-scoring-api/gate_engine/moneyline/sport_model.py",
        "authority_state": "LEGACY_NON_AUTHORITATIVE",
        "known_legacy_constructs": [
            "_HOME_ADV_LOGIT",
            "_ENSEMBLE_WEIGHTS",
            "WNBA_MANUAL_REST_NUDGE",
            "STATIC_SOCCER_DRAW_BASE",
        ],
        "production_fallback_allowed": False,
        "probability_authority": False,
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "can_execute": False,
    },
)


def _certification_identity(sport: str) -> str | None:
    return CERTIFIED_TEAM_EVENT_SPORTS.get(sport) or ACTIVATABLE_TEAM_EVENT_CERTIFICATIONS.get(sport)


def _declared_value(
    mapping: dict[str, tuple[str, str]], sport: str
) -> tuple[str | None, str | None]:
    value = mapping.get(sport)
    if value is None:
        return None, None
    return value


def _authority_state(
    *,
    registered: bool,
    implementation_resolvable: bool,
    certification_status: str,
    development_status: str,
) -> str:
    if registered and certification_status == CERTIFIED:
        return "CERTIFIED_REGISTERED_SPECIALIST"
    if registered:
        return "REGISTERED_UNCERTIFIED_SPECIALIST"
    if certification_status == CERTIFIED:
        return "CERTIFIED_NOT_REGISTERED"
    if implementation_resolvable:
        return "IMPLEMENTED_NOT_REGISTERED"
    if development_status == "CANDIDATE_PIPELINE_PRESENT":
        return "DEVELOPMENT_ONLY"
    return "BUILD_OR_ADAPTER_REQUIRED"


def build_moneyline_gap_matrix(
    is_registered: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Return exactly one fail-closed audit row for every cataloged sport.

    ``is_registered`` may be supplied by a live runtime health caller.  Omitting
    it intentionally does *not* infer registration from importability.
    """
    audit_rows = {row["sport"]: row for row in audit_table(is_registered)}
    rows: list[dict[str, Any]] = []

    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        audit = audit_rows[sport]
        development = TEAM_EVENT_MODEL_DEVELOPMENT[sport]
        route_owner, route_owner_source = _declared_value(_ROUTE_OWNERS, sport)
        runtime_schema, runtime_schema_source = _declared_value(
            _RUNTIME_FEATURE_SCHEMAS, sport
        )
        development_schema, development_schema_source = _declared_value(
            _DEVELOPMENT_FEATURE_SCHEMAS, sport
        )
        calibration_mode = readiness._CALIBRATION_MODE.get(sport, "UNAVAILABLE")
        hydration_mode = readiness._HYDRATION_MODE.get(sport, "UNASSIGNED")
        registered = bool(audit["registered_capability"])
        implementation_resolvable = bool(audit["implementation_scorer_resolvable"])
        certification_status = str(audit["certification_status"])

        gaps: list[str] = []
        if route_owner is None:
            gaps.append("CANONICAL_ROUTE_OWNER_NOT_DECLARED_IN_AUDIT_SOURCES")
        if runtime_schema is None:
            gaps.append("RUNTIME_FEATURE_SCHEMA_NOT_DECLARED_IN_CROSS_SPORT_AUDIT")
        if calibration_mode == "UNAVAILABLE":
            gaps.append("CALIBRATION_PATH_UNAVAILABLE")
        if not registered:
            gaps.append("RUNTIME_BRIDGE_NOT_REGISTERED")
        if certification_status != CERTIFIED:
            gaps.append("CERTIFICATION_NOT_ACTIVE")
        if not implementation_resolvable:
            gaps.append("SCORER_IMPLEMENTATION_NOT_RESOLVABLE")
        if development.next_gate:
            gaps.append(f"NEXT_GATE:{development.next_gate}")

        rows.append(
            {
                "gap_matrix_version": GAP_MATRIX_VERSION,
                "sport": sport,
                "route_owner": route_owner,
                "route_owner_source": route_owner_source,
                "certification_identity": _certification_identity(sport),
                "certification_status": certification_status,
                "certification_id": audit.get("certification_id"),
                "authority_state": _authority_state(
                    registered=registered,
                    implementation_resolvable=implementation_resolvable,
                    certification_status=certification_status,
                    development_status=development.status,
                ),
                "fitted_module": audit.get("fitted_module"),
                "adapter_module": audit.get("adapter_module"),
                "scorer_symbol": audit.get("scorer_symbol"),
                "model_artifact_loader": audit.get("model_artifact_loader"),
                "runtime_feature_schema_version": runtime_schema,
                "runtime_feature_schema_source": runtime_schema_source,
                "development_feature_schema_version": development_schema,
                "development_feature_schema_source": development_schema_source,
                "calibration_mode": calibration_mode,
                "hydration_mode": hydration_mode,
                "required_inputs": list(TEAM_EVENT_INPUT_CONTRACTS.get(sport, ())),
                "implementation_scorer_resolvable": implementation_resolvable,
                "registry_state": audit.get("registry_state"),
                "registered_capability": registered,
                "development_status": development.status,
                "maintenance_lane": development.maintenance_lane,
                "next_gate": development.next_gate,
                "known_gaps": list(dict.fromkeys(gaps)),
                "legacy_generic_fallback_allowed": False,
                "market_probability_substitution_allowed": False,
                "generic_reasoning_substitution_allowed": False,
                "probability_authority_inferred_from_importability": False,
                "rank_metric": RANK_METRIC,
                "global_terminal_authority": TERMINAL_AUTHORITY,
                "can_execute": False,
            }
        )

    return rows


def moneyline_gap_audit(
    is_registered: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    rows = build_moneyline_gap_matrix(is_registered)
    return {
        "gap_matrix_version": GAP_MATRIX_VERSION,
        "sports": rows,
        "legacy_paths": [dict(row) for row in LEGACY_MONEYLINE_PATHS],
        "all_cataloged_sports_accounted_for": {
            row["sport"] for row in rows
        } == set(EXPECTED_TEAM_EVENT_SPORTS),
        "probability_behavior_changed": False,
        "global_terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "GAP_MATRIX_VERSION",
    "LEGACY_MONEYLINE_PATHS",
    "RANK_METRIC",
    "TERMINAL_AUTHORITY",
    "build_moneyline_gap_matrix",
    "moneyline_gap_audit",
]
