"""Universal sport-parity diagnostics for WOW V17 player props.

Equal treatment means every cataloged sport receives the same acquisition/model
accounting shape. It does not mean every sport has the same fitted-model or
autonomous-discovery capability. Missing capability stays visible and typed;
no sportsbook price, recent hit rate, or generic reasoning becomes a model.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from v17.prop_capability_manifest import (
    BUILD_REQUIRED,
    CANDIDATE_ONLY,
    CERTIFIED_PRODUCTION,
    DECLARED_PROP_LANES,
    SUPPORTED_HOLD_ONLY,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
PROP_PARITY_VERSION = "WOW_V17_PROP_SPORT_PARITY_V1"

# Team/event catalog uses PGA while existing prop artifacts use GOLF. Preserve
# artifact identity but expose one canonical sport to orchestration consumers.
_MANIFEST_SPORT = {"PGA": "GOLF"}

# Automatic row hydration is already reviewed for these sports. This is not the
# same as autonomous board discovery: NFL/WNBA need a candidate row/player/event
# before their hydrator can run.
_AUTOMATIC_ROW_HYDRATION = {
    "MLB": "MLB_OFFICIAL_STATS_API",
    "NFL": "NFL_ESPN_IDENTITY_NFLVERSE_STATS_V1",
    "WNBA": "WNBA_OFFICIAL_STATS_CDN_INJURY_V1",
}

# Daily currently owns one truly autonomous candidate producer. Keeping this
# explicit prevents a missing producer from silently looking like an empty slate.
_DAILY_AUTONOMOUS_DISCOVERY = {
    "MLB": "MLB_OFFICIAL_PROBABLE_PITCHERS",
}


def _manifest_sport(canonical_sport: str) -> str:
    return _MANIFEST_SPORT.get(canonical_sport, canonical_sport)


def prop_sport_parity() -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        manifest_sport = _manifest_sport(sport)
        lanes = [
            capability
            for (lane_sport, _stat), capability in DECLARED_PROP_LANES.items()
            if lane_sport == manifest_sport
        ]
        counts = Counter(capability.lane_status for capability in lanes)
        certified = counts.get(CERTIFIED_PRODUCTION, 0)
        held = counts.get(SUPPORTED_HOLD_ONLY, 0)
        candidate = counts.get(CANDIDATE_ONLY, 0)
        build = counts.get(BUILD_REQUIRED, 0)
        output[sport] = {
            "contract_version": PROP_PARITY_VERSION,
            "sport": sport,
            "manifest_sport": manifest_sport,
            "cataloged": True,
            "discovery_visibility_required": True,
            "declared_lane_count": len(lanes),
            "certified_production_lane_count": certified,
            "supported_hold_lane_count": held,
            "candidate_lane_count": candidate,
            "build_required_lane_count": build,
            "automatic_row_hydration_supported": sport in _AUTOMATIC_ROW_HYDRATION,
            "automatic_row_hydration_provider": _AUTOMATIC_ROW_HYDRATION.get(sport),
            "daily_autonomous_discovery_supported": sport in _DAILY_AUTONOMOUS_DISCOVERY,
            "daily_autonomous_discovery_provider": _DAILY_AUTONOMOUS_DISCOVERY.get(sport),
            "daily_zero_rows_mean_no_slate": False,
            "unsupported_route_fallback_allowed": False,
            "market_probability_substitution_allowed": False,
            "generic_reasoning_substitution_allowed": False,
            "publication_is_exact_lane_scoped": True,
            "can_execute": False,
        }
    return output


def prop_sport_parity_summary() -> dict[str, Any]:
    rows = prop_sport_parity()
    return {
        "contract_version": PROP_PARITY_VERSION,
        "cataloged_sports": len(rows),
        "sports": rows,
        "automatic_row_hydration_sports": sorted(
            sport for sport, row in rows.items() if row["automatic_row_hydration_supported"]
        ),
        "daily_autonomous_discovery_sports": sorted(
            sport for sport, row in rows.items() if row["daily_autonomous_discovery_supported"]
        ),
        "sports_without_daily_autonomous_discovery": sorted(
            sport for sport, row in rows.items() if not row["daily_autonomous_discovery_supported"]
        ),
        "zero_candidates_is_not_capability_proof": True,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "PROP_PARITY_VERSION",
    "prop_sport_parity",
    "prop_sport_parity_summary",
]
