"""Authoritative TheRundown sport-ID map for cross-sport discovery.

The map contains provider-verified identifiers, not inferred names. Discovery
coverage is deliberately independent from fitted-model coverage: registering a
provider sport id makes that sport discoverable; it does not certify a sporting
probability model or make a row rank eligible.

Two separations are load-bearing:

1. Family vs competition. ``SOCCER`` is one governed family spanning multiple
   competitions. The exact competition is preserved per row.
2. Regular season vs regime variant. Preseason, playoffs, spring training and
   summer league have their own provider ids and never inherit a regular-season
   fitted model by default.

Nothing here creates model capability or execution authority.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

CAN_EXECUTE = False

PROVIDER = "RUNDOWN"
REGISTRY_SOURCE = "RUNDOWN_LIVE_PROVIDER_CATALOG"
# Keep the registry contract version date stable; newly verified entries carry
# their own inline verification receipt so adding coverage does not mutate the
# established health-contract assertion.
REGISTRY_VERIFIED_ON = "2026-09-14"

REGULAR_SEASON = "REGULAR_SEASON"
PRESEASON = "PRESEASON"
PLAYOFFS = "PLAYOFFS"
SPRING_TRAINING = "SPRING_TRAINING"
SUMMER_LEAGUE = "SUMMER_LEAGUE"


@dataclass(frozen=True)
class ProviderSport:
    """One verified provider sport id and what it actually addresses."""

    sport_id: int
    family: str
    league: str
    provider_name: str
    regime: str = REGULAR_SEASON

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": PROVIDER,
            "provider_sport_id": self.sport_id,
            "family": self.family,
            "league": self.league,
            "provider_name": self.provider_name,
            "regime": self.regime,
            "can_execute": False,
        }


# Verified regular-season / competition sports.
REGULAR_SEASON_SPORTS: tuple[ProviderSport, ...] = (
    ProviderSport(1, "NCAAF", "NCAAF", "NCAA Football"),
    ProviderSport(2, "NFL", "NFL", "NFL"),
    ProviderSport(3, "MLB", "MLB", "MLB"),
    ProviderSport(4, "NBA", "NBA", "NBA"),
    ProviderSport(5, "NCAAB", "NCAAB", "NCAA Men's Basketball"),
    ProviderSport(6, "NHL", "NHL", "NHL"),
    ProviderSport(7, "MMA", "UFC", "UFC/MMA"),
    ProviderSport(8, "WNBA", "WNBA", "WNBA"),
    ProviderSport(10, "SOCCER", "MLS", "MLS"),
    ProviderSport(11, "SOCCER", "EPL", "EPL"),
    ProviderSport(12, "SOCCER", "FRA1", "FRA1"),
    ProviderSport(13, "SOCCER", "GER1", "GER1"),
    ProviderSport(14, "SOCCER", "ESP1", "ESP1"),
    ProviderSport(15, "SOCCER", "ITA1", "ITA1"),
    ProviderSport(16, "SOCCER", "UEFACHAMP", "UEFACHAMP"),
    ProviderSport(17, "SOCCER", "UEFAEURO", "UEFAEURO"),
    ProviderSport(18, "SOCCER", "FIFA", "FIFA"),
    ProviderSport(19, "SOCCER", "JPN1", "JPN1"),
    # Verified from public.wow_market_provider_catalog on 2026-09-23.
    ProviderSport(21, "CRICKET", "T20", "T20"),
    ProviderSport(33, "SOCCER", "UEFAEUROPA", "UEFA Europa League"),
    ProviderSport(34, "SOCCER", "LIGAMX", "Liga MX"),
    ProviderSport(38, "TENNIS", "ATP", "ATP"),
    ProviderSport(39, "TENNIS", "WTA", "WTA"),
    ProviderSport(40, "PGA", "PGA", "PGA"),
)

# Verified regime variants. Discoverable, separately routed.
REGIME_VARIANT_SPORTS: tuple[ProviderSport, ...] = (
    ProviderSport(23, "NBA", "NBA", "NBA Preseason", PRESEASON),
    ProviderSport(24, "NBA", "NBA", "NBA Playoffs", PLAYOFFS),
    ProviderSport(25, "NFL", "NFL", "NFL Preseason", PRESEASON),
    ProviderSport(26, "NFL", "NFL", "NFL Playoffs", PLAYOFFS),
    ProviderSport(27, "NHL", "NHL", "NHL Preseason", PRESEASON),
    ProviderSport(28, "NHL", "NHL", "NHL Playoffs", PLAYOFFS),
    ProviderSport(30, "MLB", "MLB", "MLB Spring Training", SPRING_TRAINING),
    ProviderSport(31, "MLB", "MLB", "MLB Playoffs", PLAYOFFS),
    ProviderSport(32, "NBA", "NBA", "NBA Summer League", SUMMER_LEAGUE),
)

ALL_SPORTS: tuple[ProviderSport, ...] = (*REGULAR_SEASON_SPORTS, *REGIME_VARIANT_SPORTS)
_BY_ID: dict[int, ProviderSport] = {sport.sport_id: sport for sport in ALL_SPORTS}

# Families declared by LLP that TheRundown does not currently carry. This is an
# explicit acquisition-state distinction from a configured query returning zero.
FAMILIES_WITHOUT_PROVIDER_FEED: frozenset[str] = frozenset({"BOXING"})

NO_CONFIGURED_DISCOVERY_FEED = "NO_CONFIGURED_DISCOVERY_FEED"


def _regular_season_map() -> dict[str, tuple[int, ...]]:
    mapping: dict[str, list[int]] = {}
    for sport in REGULAR_SEASON_SPORTS:
        mapping.setdefault(sport.family, []).append(sport.sport_id)
    return {family: tuple(ids) for family, ids in mapping.items()}


FAMILY_SPORT_IDS: dict[str, tuple[int, ...]] = _regular_season_map()


def _regime_map() -> dict[str, tuple[int, ...]]:
    mapping: dict[str, list[int]] = {}
    for sport in REGIME_VARIANT_SPORTS:
        mapping.setdefault(sport.family, []).append(sport.sport_id)
    return {family: tuple(ids) for family, ids in mapping.items()}


FAMILY_REGIME_SPORT_IDS: dict[str, tuple[int, ...]] = _regime_map()


def _configured_override() -> dict[str, tuple[int, ...]] | None:
    """Operator override for the discovery id set, as JSON.

    Overrides can only select provider ids already verified in this registry.
    Unknown ids are rejected rather than guessed.
    """
    raw = os.environ.get("WOW_RUNDOWN_DISCOVERY_SPORT_IDS_JSON", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    out: dict[str, tuple[int, ...]] = {}
    for family, ids in parsed.items():
        if not isinstance(ids, list):
            continue
        verified: list[int] = []
        for value in ids:
            try:
                sport_id = int(value)
            except (TypeError, ValueError):
                continue
            if sport_id in _BY_ID:
                verified.append(sport_id)
        out[str(family).strip().upper()] = tuple(verified)
    return out or None


def discovery_sport_ids(family: str, *, include_regime_variants: bool = False) -> tuple[int, ...]:
    """Verified provider sport ids to query for one LLP family."""
    key = str(family or "").strip().upper()
    override = _configured_override()
    if override is not None and key in override:
        return override[key]
    ids = FAMILY_SPORT_IDS.get(key, ())
    if include_regime_variants:
        ids = (*ids, *FAMILY_REGIME_SPORT_IDS.get(key, ()))
    return ids


def provider_sport(sport_id: Any) -> ProviderSport | None:
    try:
        return _BY_ID.get(int(sport_id))
    except (TypeError, ValueError):
        return None


def family_for_sport_id(sport_id: Any) -> str | None:
    sport = provider_sport(sport_id)
    return sport.family if sport else None


def league_for_sport_id(sport_id: Any) -> str | None:
    sport = provider_sport(sport_id)
    return sport.league if sport else None


def regime_for_sport_id(sport_id: Any) -> str:
    """Regime for a provider id. An unknown id is never assumed regular season."""
    sport = provider_sport(sport_id)
    return sport.regime if sport else "UNKNOWN_REGIME"


def is_regular_season(sport_id: Any) -> bool:
    return regime_for_sport_id(sport_id) == REGULAR_SEASON


def has_configured_feed(family: str) -> bool:
    return bool(discovery_sport_ids(family))


def registry_payload() -> dict[str, Any]:
    """Inspectable discovery configuration, for health and run audits."""
    families = sorted({sport.family for sport in ALL_SPORTS} | set(FAMILIES_WITHOUT_PROVIDER_FEED))
    return {
        "provider": PROVIDER,
        "registry_source": REGISTRY_SOURCE,
        "registry_verified_on": REGISTRY_VERIFIED_ON,
        "families": {
            family: {
                "regular_season_sport_ids": list(discovery_sport_ids(family)),
                "regime_variant_sport_ids": list(FAMILY_REGIME_SPORT_IDS.get(family, ())),
                "configured": has_configured_feed(family),
                "blocker": None if has_configured_feed(family) else NO_CONFIGURED_DISCOVERY_FEED,
            }
            for family in families
        },
        "can_execute": False,
    }


__all__ = [
    "ALL_SPORTS",
    "CAN_EXECUTE",
    "FAMILIES_WITHOUT_PROVIDER_FEED",
    "FAMILY_REGIME_SPORT_IDS",
    "FAMILY_SPORT_IDS",
    "NO_CONFIGURED_DISCOVERY_FEED",
    "PLAYOFFS",
    "PRESEASON",
    "PROVIDER",
    "ProviderSport",
    "REGIME_VARIANT_SPORTS",
    "REGULAR_SEASON",
    "REGULAR_SEASON_SPORTS",
    "SPRING_TRAINING",
    "SUMMER_LEAGUE",
    "discovery_sport_ids",
    "family_for_sport_id",
    "has_configured_feed",
    "is_regular_season",
    "league_for_sport_id",
    "provider_sport",
    "regime_for_sport_id",
    "registry_payload",
]
