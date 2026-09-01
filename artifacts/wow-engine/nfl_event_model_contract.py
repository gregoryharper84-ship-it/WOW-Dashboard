"""WOW v16 NFL full-game moneyline fitted-specialist P0 contract.

P0 establishes identity, input requirements, and publication boundaries only.
It does NOT implement or approximate an NFL win-probability model.  Until a
certified fitted artifact and calibrator are promoted, NFL event probability
must remain MODEL_UNAVAILABLE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PROVIDER_IDENTITY = "WOW_NFL_EVENT_FITTED_MODEL_V1"
CONTROLLING_SPECIALIST = "wow.nfl-game-win-probability-expert"
CAPABILITY_KEY = "NFL_EVENT_PROBABILITY"
FEATURE_SCHEMA_VERSION = "NFL_EVENT_FEATURES_V1"
MODEL_SCOPE = "FULL_GAME_MONEYLINE"
SUPPORTED_MARKET_FAMILIES = frozenset({"OUTRIGHT_WINNER"})
SUPPORTED_PERIODS = frozenset({"FULL_GAME", "FULL_GAME_INCLUDING_OVERTIME"})
TERMINAL_CEILING = "MODEL_QUALIFIED_HOLD"

# These are feature GROUPS, not caller-trusted scalar values.  P1/P2 must
# materialize them from timestamped evidence and bind them to an immutable
# pregame snapshot before a fitted artifact can score a game.
REQUIRED_FEATURE_GROUPS = (
    "event_identity",
    "team_strength",
    "quarterback_status",
    "offensive_line_status",
    "skill_position_availability",
    "defensive_context",
    "special_teams_context",
    "rest_travel_context",
    "venue_weather_context",
)


@dataclass(frozen=True)
class ContractCheck:
    ok: bool
    blockers: tuple[str, ...]


def validate_candidate_identity(candidate: dict[str, Any]) -> ContractCheck:
    """Validate only the immutable NFL event identity boundary.

    This function intentionally performs no probability calculation and accepts
    no market-implied or caller-supplied probability as model evidence.
    """
    blockers: list[str] = []
    sport = str(candidate.get("sport") or "").upper()
    market = str(candidate.get("market_family") or "").upper()
    period = str(candidate.get("period") or "").upper()

    if sport != "NFL":
        blockers.append("NFL_EVENT_SPORT_MISMATCH")
    if market not in SUPPORTED_MARKET_FAMILIES:
        blockers.append("NFL_EVENT_MARKET_UNSUPPORTED")
    if period not in SUPPORTED_PERIODS:
        blockers.append("NFL_EVENT_PERIOD_UNSUPPORTED")
    for key in ("official_event_id", "participant", "opponent"):
        if candidate.get(key) in (None, ""):
            blockers.append(f"NFL_EVENT_{key.upper()}_MISSING")

    return ContractCheck(ok=not blockers, blockers=tuple(sorted(set(blockers))))


def validate_feature_packet(packet: dict[str, Any] | None) -> ContractCheck:
    """Require every P0 feature group; never synthesize absent evidence."""
    if not isinstance(packet, dict):
        return ContractCheck(False, ("NFL_EVENT_FEATURE_PACKET_MISSING",))
    missing = [
        group for group in REQUIRED_FEATURE_GROUPS
        if packet.get(group) in (None, {}, [], "")
    ]
    return ContractCheck(
        ok=not missing,
        blockers=tuple(f"NFL_EVENT_{group.upper()}_MISSING" for group in missing),
    )


def p0_readiness(*, artifact_ready: bool, calibrator_ready: bool, capability_status: str) -> dict[str, Any]:
    """Return non-predictive readiness state for P0/P1 orchestration."""
    blockers: list[str] = []
    if not artifact_ready:
        blockers.append("NFL_FITTED_MODEL_ARTIFACT_UNAVAILABLE")
    if not calibrator_ready:
        blockers.append("NFL_EVENT_CALIBRATOR_UNAVAILABLE")
    if str(capability_status).upper() != "AVAILABLE":
        blockers.append("NFL_EVENT_PROBABILITY_CAPABILITY_UNAVAILABLE")
    return {
        "provider_identity": PROVIDER_IDENTITY,
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "capability_key": CAPABILITY_KEY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_scope": MODEL_SCOPE,
        "model_status": "AVAILABLE" if not blockers else "MODEL_UNAVAILABLE",
        "blockers": sorted(set(blockers)),
        "probability_publishable": False,
        "terminal_ceiling": TERMINAL_CEILING,
        "can_execute": False,
    }
