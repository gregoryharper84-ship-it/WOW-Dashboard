"""Universal V17 governance profiles for team/event probability lanes.

The governance envelope is shared across sports; sporting evidence and fitted
models are not. Every cataloged sport therefore owns an explicit profile that
states its outcome space, settlement semantics, and sport-specific evidence
families. Profiles never create model capability, replace a specialist input
contract, or authorize execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from v17.team_event_capability_manifest import (
    EXPECTED_TEAM_EVENT_SPORTS,
    TEAM_EVENT_INPUT_CONTRACTS,
    normalize_team_event_identity,
)

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


@dataclass(frozen=True)
class TeamEventGovernanceProfile:
    sport: str
    outcome_space: str
    settlement_contract: str
    evidence_families: tuple[str, ...]
    requires_participant_role_for_upset: bool = True
    probability_market_independent: bool = True
    terminal_authority: str = TERMINAL_AUTHORITY
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "outcome_space": self.outcome_space,
            "settlement_contract": self.settlement_contract,
            "evidence_families": list(self.evidence_families),
            "requires_participant_role_for_upset": self.requires_participant_role_for_upset,
            "probability_market_independent": self.probability_market_independent,
            "terminal_authority": self.terminal_authority,
            "can_execute": False,
        }


_PROFILES: dict[str, TeamEventGovernanceProfile] = {
    "MLB": TeamEventGovernanceProfile("MLB", "HOME_AWAY", "FULL_GAME_INCLUDING_EXTRA_INNINGS", TEAM_EVENT_INPUT_CONTRACTS["MLB"]),
    "NFL": TeamEventGovernanceProfile("NFL", "HOME_AWAY", "FULL_GAME_INCLUDING_OVERTIME", TEAM_EVENT_INPUT_CONTRACTS["NFL"]),
    "NBA": TeamEventGovernanceProfile("NBA", "HOME_AWAY", "FULL_GAME_INCLUDING_OVERTIME", TEAM_EVENT_INPUT_CONTRACTS["NBA"]),
    "WNBA": TeamEventGovernanceProfile("WNBA", "HOME_AWAY", "FULL_GAME_INCLUDING_OVERTIME", TEAM_EVENT_INPUT_CONTRACTS["WNBA"]),
    "NCAAF": TeamEventGovernanceProfile("NCAAF", "HOME_AWAY", "FULL_GAME_INCLUDING_OVERTIME", TEAM_EVENT_INPUT_CONTRACTS["NCAAF"]),
    "NCAAB": TeamEventGovernanceProfile("NCAAB", "HOME_AWAY", "FULL_GAME_INCLUDING_OVERTIME", TEAM_EVENT_INPUT_CONTRACTS["NCAAB"]),
    "NHL": TeamEventGovernanceProfile("NHL", "HOME_AWAY", "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT", TEAM_EVENT_INPUT_CONTRACTS["NHL"]),
    "SOCCER": TeamEventGovernanceProfile("SOCCER", "HOME_DRAW_AWAY", "REGULATION_TIME_THREE_WAY_UNLESS_MARKET_EXPLICIT", TEAM_EVENT_INPUT_CONTRACTS["SOCCER"]),
    "TENNIS": TeamEventGovernanceProfile("TENNIS", "PLAYER_A_PLAYER_B", "MATCH_WINNER_WITH_EXPLICIT_RETIREMENT_RULE", TEAM_EVENT_INPUT_CONTRACTS["TENNIS"]),
    "PGA": TeamEventGovernanceProfile("PGA", "FIELD_OR_HEAD_TO_HEAD", "TOURNAMENT_OR_H2H_WITHDRAWAL_DQ_RULES", TEAM_EVENT_INPUT_CONTRACTS["PGA"]),
    "MMA": TeamEventGovernanceProfile("MMA", "FIGHTER_A_FIGHTER_B_DRAW_NC", "OFFICIAL_FIGHT_RESULT_WITH_DRAW_NO_CONTEST_RULES", TEAM_EVENT_INPUT_CONTRACTS["MMA"]),
    "BOXING": TeamEventGovernanceProfile("BOXING", "FIGHTER_A_FIGHTER_B_DRAW_NC", "OFFICIAL_FIGHT_RESULT_WITH_DRAW_NO_CONTEST_RULES", TEAM_EVENT_INPUT_CONTRACTS["BOXING"]),
    "CRICKET": TeamEventGovernanceProfile("CRICKET", "TEAM_A_TEAM_B_TIE_NO_RESULT", "T20_MATCH_WINNER_WITH_EXPLICIT_TIE_NO_RESULT_RULES", TEAM_EVENT_INPUT_CONTRACTS["CRICKET"]),
}

if set(_PROFILES) != set(EXPECTED_TEAM_EVENT_SPORTS):
    missing = sorted(set(EXPECTED_TEAM_EVENT_SPORTS) - set(_PROFILES))
    extra = sorted(set(_PROFILES) - set(EXPECTED_TEAM_EVENT_SPORTS))
    raise RuntimeError(f"TEAM_EVENT_GOVERNANCE_PROFILE_COVERAGE_MISMATCH missing={missing} extra={extra}")

TEAM_EVENT_GOVERNANCE_PROFILES: Mapping[str, TeamEventGovernanceProfile] = _PROFILES


def governance_profile(sport: str, league: str | None = None) -> TeamEventGovernanceProfile | None:
    return _PROFILES.get(normalize_team_event_identity(sport, league))


def governance_profile_preflight(req: Any) -> dict[str, Any]:
    """Resolve governance metadata without replacing specialist validation."""
    sport = normalize_team_event_identity(getattr(req, "sport", ""), getattr(req, "league", None))
    profile = _PROFILES.get(sport)
    if profile is None:
        return {
            "status": "DEFER",
            "sport": sport,
            "code": "TEAM_EVENT_GOVERNANCE_PROFILE_MISSING",
            "profile": None,
            "terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": False,
        }

    return {
        "status": "PASS",
        "sport": sport,
        "code": "PASS",
        "profile": profile.as_dict(),
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def governance_health() -> dict[str, dict[str, Any]]:
    return {sport: {"status": "INSTALLED", **profile.as_dict()} for sport, profile in _PROFILES.items()}


__all__ = [
    "CAN_EXECUTE",
    "TEAM_EVENT_GOVERNANCE_PROFILES",
    "TERMINAL_AUTHORITY",
    "TeamEventGovernanceProfile",
    "governance_health",
    "governance_profile",
    "governance_profile_preflight",
]
