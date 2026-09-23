"""Versioned, fail-closed MoneyLine feature-contract registry.

This module is an audit surface only. It does not score, hydrate, calibrate,
register, certify, rank, promote, or publish a sporting probability.

A declared feature contract says only that repository model/training code proves
that a named feature belongs to a versioned numerical model vector. It does not
prove that a live prediction consumed the feature. Live numerical consumption
remains UNVERIFIED until the scorer emits explicit ``consumed_feature_ids`` and
the Feature Consumption Receipt verifies them.

Development-only schemas are intentionally separated from runtime contracts so
schema existence cannot self-promote model authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS
from v17.team_event_feature_consumption import FEATURE_ROLES

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
REGISTRY_VERSION = "V17_MONEYLINE_FEATURE_CONTRACT_REGISTRY_V1"

RUNTIME_CONTRACT = "RUNTIME_CONTRACT"
DEVELOPMENT_ONLY = "DEVELOPMENT_ONLY"
UNDECLARED = "UNDECLARED"
SCHEMA_STATES = frozenset({RUNTIME_CONTRACT, DEVELOPMENT_ONLY, UNDECLARED})

CONSUMPTION_PROOF_REQUIRED = True
CONSUMPTION_CURRENTLY_VERIFIED = False

# Exact vector consumed by the certified NFL fitted model. Source chain:
# nfl_event_features_p2.FEATURE_ORDER -> nfl_event_model_v17.CertifiedNFLModel.
_NFL_FEATURES = (
    "week",
    "home_prior_games",
    "away_prior_games",
    "home_season_prior_games",
    "away_season_prior_games",
    "home_rest_days",
    "away_rest_days",
    "home_recent_off_epa_pp",
    "away_recent_off_epa_pp",
    "off_epa_edge",
    "home_recent_def_epa_pp",
    "away_recent_def_epa_pp",
    "def_epa_edge",
    "home_recent_success_rate",
    "away_recent_success_rate",
    "success_rate_edge",
    "home_recent_turnovers_pg",
    "away_recent_turnovers_pg",
    "turnover_edge",
    "home_recent_sacks_allowed_pg",
    "away_recent_sacks_allowed_pg",
    "sack_edge",
    "home_recent_st_epa_pg",
    "away_recent_st_epa_pg",
    "st_epa_edge",
    "home_prior_win_rate",
    "away_prior_win_rate",
    "win_rate_edge",
    "home_prior_point_diff_pg",
    "away_prior_point_diff_pg",
    "point_diff_edge",
)

# Exact vector consumed by the NBA/WNBA development logistic artifacts. These
# are deliberately DEVELOPMENT_ONLY until a governed runtime lane is promoted.
_BASKETBALL_FEATURES = (
    "home_win_rate_prior",
    "away_win_rate_prior",
    "home_point_diff_prior",
    "away_point_diff_prior",
    "home_rest_days_capped",
    "away_rest_days_capped",
    "home_back_to_back",
    "away_back_to_back",
)

# Exact fitted candidate vector from ncaaf_trainer.FEATURES. Market prior fields
# are explicitly excluded by ncaaf_feature_transform.py.
_NCAAF_FEATURES = (
    "power_delta",
    "off_epa_delta",
    "def_epa_delta",
    "success_rate_delta",
    "explosiveness_delta",
    "qb_value_delta",
    "qb_certainty_delta",
    "ol_health_delta",
    "def_front_health_delta",
    "skill_availability_delta",
    "rest_days_delta",
    "tempo_delta",
    "turnover_volatility_delta",
    "special_teams_delta",
    "travel_distance_miles",
    "weather_wind_mph",
    "weather_precip_probability",
    "neutral_site",
)


@dataclass(frozen=True)
class DeclaredFeature:
    feature_id: str
    role: str
    required: bool = True
    critical: bool = True

    def __post_init__(self) -> None:
        if not self.feature_id:
            raise ValueError("feature_id is required")
        if self.role not in FEATURE_ROLES:
            raise ValueError(f"unsupported feature role: {self.role}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "role": self.role,
            "required": self.required,
            "critical": self.critical,
        }


@dataclass(frozen=True)
class SportFeatureContract:
    sport: str
    schema_state: str
    schema_version: str | None
    model_family: str | None
    schema_source: str | None
    model_consumption_source: str | None
    features: tuple[DeclaredFeature, ...] = ()
    market_probability_feature_allowed: bool = False
    generic_probability_substitution_allowed: bool = False
    consumption_proof_required: bool = CONSUMPTION_PROOF_REQUIRED
    consumption_currently_verified: bool = CONSUMPTION_CURRENTLY_VERIFIED
    can_execute: bool = False

    def __post_init__(self) -> None:
        if self.sport not in EXPECTED_TEAM_EVENT_SPORTS:
            raise ValueError(f"unsupported sport: {self.sport}")
        if self.schema_state not in SCHEMA_STATES:
            raise ValueError(f"unsupported schema_state: {self.schema_state}")
        if self.schema_state == UNDECLARED:
            if self.schema_version is not None or self.features:
                raise ValueError("UNDECLARED contracts cannot claim a schema or features")
        elif not self.schema_version or not self.features:
            raise ValueError("declared contracts require a schema version and features")
        if self.consumption_currently_verified:
            raise ValueError(
                "registry declarations cannot claim live consumption verification"
            )
        if self.market_probability_feature_allowed:
            raise ValueError("market sporting-probability features are not certified here")
        if self.generic_probability_substitution_allowed:
            raise ValueError("generic probability substitution is prohibited")
        if self.can_execute:
            raise ValueError("can_execute must remain false")

    def as_dict(self) -> dict[str, Any]:
        return {
            "registry_version": REGISTRY_VERSION,
            "sport": self.sport,
            "schema_state": self.schema_state,
            "schema_version": self.schema_version,
            "model_family": self.model_family,
            "schema_source": self.schema_source,
            "model_consumption_source": self.model_consumption_source,
            "features": [feature.as_dict() for feature in self.features],
            "feature_count": len(self.features),
            "consumption_proof_required": self.consumption_proof_required,
            "consumption_currently_verified": self.consumption_currently_verified,
            "market_probability_feature_allowed": self.market_probability_feature_allowed,
            "generic_probability_substitution_allowed": self.generic_probability_substitution_allowed,
            "terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": self.can_execute,
        }


def _primary_features(names: tuple[str, ...]) -> tuple[DeclaredFeature, ...]:
    return tuple(
        DeclaredFeature(feature_id=name, role="MODEL_PRIMARY") for name in names
    )


_EXPLICIT_CONTRACTS: dict[str, SportFeatureContract] = {
    "NFL": SportFeatureContract(
        sport="NFL",
        schema_state=RUNTIME_CONTRACT,
        schema_version="NFL_EVENT_PREGAME_PRIOR_V1",
        model_family="NFL_OUTRIGHT_WIN_LOGREG_V1",
        schema_source="nfl_event_features_p2.py:FEATURE_ORDER",
        model_consumption_source="nfl_event_model_v17.py:FEATURE_ORDER",
        features=_primary_features(_NFL_FEATURES),
    ),
    "NBA": SportFeatureContract(
        sport="NBA",
        schema_state=DEVELOPMENT_ONLY,
        schema_version="BASKETBALL_TEAM_EVENT_FEATURES_V2",
        model_family="BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        schema_source="basketball_team_event_specialist.py:FEATURE_NAMES",
        model_consumption_source="basketball_team_event_specialist.py:LogisticArtifact.raw_probability",
        features=_primary_features(_BASKETBALL_FEATURES),
    ),
    "WNBA": SportFeatureContract(
        sport="WNBA",
        schema_state=DEVELOPMENT_ONLY,
        schema_version="BASKETBALL_TEAM_EVENT_FEATURES_V2",
        model_family="BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        schema_source="basketball_team_event_specialist.py:FEATURE_NAMES",
        model_consumption_source="basketball_team_event_specialist.py:LogisticArtifact.raw_probability",
        features=_primary_features(_BASKETBALL_FEATURES),
    ),
    "NCAAF": SportFeatureContract(
        sport="NCAAF",
        schema_state=DEVELOPMENT_ONLY,
        schema_version="NCAAF_FEATURES_V1",
        model_family="NCAAF_LOGISTIC_V1",
        schema_source="ncaaf_trainer.py:FEATURES",
        model_consumption_source="ncaaf_trainer.py:_as_matrix",
        features=_primary_features(_NCAAF_FEATURES),
    ),
}


def feature_contract_for_sport(sport: str) -> SportFeatureContract:
    normalized = str(sport or "").upper().strip()
    if normalized not in EXPECTED_TEAM_EVENT_SPORTS:
        raise KeyError(normalized)
    declared = _EXPLICIT_CONTRACTS.get(normalized)
    if declared is not None:
        return declared
    return SportFeatureContract(
        sport=normalized,
        schema_state=UNDECLARED,
        schema_version=None,
        model_family=None,
        schema_source=None,
        model_consumption_source=None,
    )


def feature_contract_registry() -> list[dict[str, Any]]:
    """Return one fail-closed contract row for every cataloged team/event sport."""
    return [feature_contract_for_sport(sport).as_dict() for sport in EXPECTED_TEAM_EVENT_SPORTS]


def feature_contract_registry_health() -> dict[str, Any]:
    rows = feature_contract_registry()
    declared = [row for row in rows if row["schema_state"] != UNDECLARED]
    runtime = [row for row in rows if row["schema_state"] == RUNTIME_CONTRACT]
    development = [row for row in rows if row["schema_state"] == DEVELOPMENT_ONLY]
    return {
        "registry_version": REGISTRY_VERSION,
        "cataloged_sport_count": len(rows),
        "declared_contract_count": len(declared),
        "runtime_contract_count": len(runtime),
        "development_only_contract_count": len(development),
        "undeclared_contract_count": len(rows) - len(declared),
        "all_live_consumption_unverified": all(
            row["consumption_currently_verified"] is False for row in rows
        ),
        "market_probability_feature_allowed": False,
        "generic_probability_substitution_allowed": False,
        "probability_behavior_changed": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CONSUMPTION_CURRENTLY_VERIFIED",
    "CONSUMPTION_PROOF_REQUIRED",
    "DEVELOPMENT_ONLY",
    "DeclaredFeature",
    "REGISTRY_VERSION",
    "RUNTIME_CONTRACT",
    "SCHEMA_STATES",
    "SportFeatureContract",
    "TERMINAL_AUTHORITY",
    "UNDECLARED",
    "feature_contract_for_sport",
    "feature_contract_registry",
    "feature_contract_registry_health",
]
