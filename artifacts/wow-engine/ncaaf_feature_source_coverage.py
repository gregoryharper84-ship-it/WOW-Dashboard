"""Map NCAAF_FEATURES_V1 slots to reviewed pregame providers.

The approved provider set is the seed in ncaaf_verified_availability_sources.sql.
A slot with no approved provider cannot be materialized. Availability reports,
Elo, box scores, and market prices are not substitutes for a missing slot.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from ncaaf_feature_compiler import REQUIRED_SCOPED_KEYS

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False

# Keep this identical to the reviewed seed. Tests compare it to that SQL.
APPROVED_PROVIDER_KINDS: dict[str, frozenset[str]] = {
    "BIG12_OFFICIAL_AVAILABILITY": frozenset({"PLAYER_AVAILABILITY_REPORT"}),
    "ACC_OFFICIAL_AVAILABILITY": frozenset({"PLAYER_AVAILABILITY_REPORT"}),
    "BIGTEN_OFFICIAL_AVAILABILITY": frozenset({"PLAYER_AVAILABILITY_REPORT"}),
    "SUNBELT_OFFICIAL_AVAILABILITY": frozenset({"PLAYER_AVAILABILITY_REPORT"}),
    "WOW_NCAAF_AVAILABILITY_AGGREGATOR_V1": frozenset({
        "QB_STATUS",
        "QB_CERTAINTY",
        "SKILL_AVAILABILITY",
    }),
}
_RAW_ONLY_KINDS = frozenset({"PLAYER_AVAILABILITY_REPORT", "QB_STATUS", "MARKET_NO_VIG"})
# Evidence slots required before the existing 18-feature logistic vector can be built.
# neutral_site is game identity, not a pregame evidence provider.
FITTED_FEATURE_SLOTS: dict[str, tuple[str, ...]] = {
    "power_delta": ("TEAM_POWER:HOME", "TEAM_POWER:AWAY"),
    "off_epa_delta": ("OFF_EPA:HOME", "OFF_EPA:AWAY"),
    "def_epa_delta": ("DEF_EPA:HOME", "DEF_EPA:AWAY"),
    "success_rate_delta": ("SUCCESS_RATE:HOME", "SUCCESS_RATE:AWAY"),
    "explosiveness_delta": ("EXPLOSIVENESS:HOME", "EXPLOSIVENESS:AWAY"),
    "qb_value_delta": ("QB_VALUE:HOME", "QB_VALUE:AWAY"),
    "qb_certainty_delta": ("QB_CERTAINTY:HOME", "QB_CERTAINTY:AWAY"),
    "ol_health_delta": ("OL_HEALTH:HOME", "OL_HEALTH:AWAY"),
    "def_front_health_delta": ("DEF_FRONT_HEALTH:HOME", "DEF_FRONT_HEALTH:AWAY"),
    "skill_availability_delta": ("SKILL_AVAILABILITY:HOME", "SKILL_AVAILABILITY:AWAY"),
    "rest_days_delta": ("REST_TRAVEL:HOME", "REST_TRAVEL:AWAY"),
    "tempo_delta": ("TEMPO:HOME", "TEMPO:AWAY"),
    "turnover_volatility_delta": ("TURNOVER_VOLATILITY:HOME", "TURNOVER_VOLATILITY:AWAY"),
    "special_teams_delta": ("SPECIAL_TEAMS:HOME", "SPECIAL_TEAMS:AWAY"),
    "travel_distance_miles": ("REST_TRAVEL:AWAY",),
    "weather_wind_mph": ("WEATHER:EVENT",),
    "weather_precip_probability": ("WEATHER:EVENT",),
    "neutral_site": (),
}


def _providers_for(kind: str, registry: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    if kind in _RAW_ONLY_KINDS:
        return ()
    return tuple(sorted(
        provider
        for provider, kinds in registry.items()
        if kind in {str(item).upper() for item in kinds}
    ))


def build_feature_source_matrix(
    registry: Mapping[str, Iterable[str]] | None = None,
) -> dict:
    """Return one row per required slot and the slots that still have no source."""
    approved = APPROVED_PROVIDER_KINDS if registry is None else {
        str(provider): frozenset(str(kind).upper() for kind in kinds)
        for provider, kinds in registry.items()
    }
    rows = []
    for kind, scope in REQUIRED_SCOPED_KEYS:
        providers = _providers_for(kind, approved)
        rows.append({
            "slot": f"{kind}:{scope}",
            "evidence_kind": kind,
            "scope": scope,
            "approved_providers": list(providers),
            "coverage": "APPROVED_SOURCE" if providers else "MISSING_APPROVED_SOURCE",
        })
    missing = [row["slot"] for row in rows if row["coverage"] != "APPROVED_SOURCE"]
    covered = [row["slot"] for row in rows if row["coverage"] == "APPROVED_SOURCE"]
    covered_set = set(covered)
    fitted = []
    for feature, slots in FITTED_FEATURE_SLOTS.items():
        absent = [slot for slot in slots if slot not in covered_set]
        fitted.append({
            "feature": feature,
            "required_slots": list(slots),
            "missing_slots": absent,
            "coverage": "GAME_IDENTITY" if not slots else ("APPROVED_SOURCE" if not absent else "MISSING_APPROVED_SOURCE"),
        })
    return {
        "feature_schema_version": "NCAAF_FEATURES_V1",
        "required_slot_count": len(rows),
        "approved_slot_count": len(covered),
        "missing_slot_count": len(missing),
        "slots": rows,
        "approved_slots": covered,
        "missing_slots": missing,
        "fitted_features": fitted,
        "fitted_features_missing_source": [
            row["feature"] for row in fitted if row["coverage"] == "MISSING_APPROVED_SOURCE"
        ],
        "raw_reports_are_not_feature_slots": sorted(_RAW_ONLY_KINDS),
        "complete_feature_rows_possible_from_approved_sources": not missing,
        "challenger_fit_allowed": False,
        "challenger_fit_block": "MISSING_APPROVED_SOURCE" if missing else "ROW_COUNT_NOT_PROVEN",
        "market_features_used": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "APPROVED_PROVIDER_KINDS",
    "CAN_EXECUTE",
    "PROBABILITY_PUBLISHABLE",
    "build_feature_source_matrix",
]
