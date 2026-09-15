"""Exact V17 team/event controlling-specialist ownership.

Ownership is routing governance, not numerical authority. A sport listed here
still requires its own certified fitted artifact, calibration/bounds package and
runtime bridge before it can publish a model probability. All owners are
non-executing.
"""
from __future__ import annotations

from dataclasses import dataclass

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False


@dataclass(frozen=True)
class TeamEventOwner:
    sport: str
    market_family: str
    controlling_specialist: str
    can_execute: bool = False


TEAM_EVENT_OWNERS: dict[str, TeamEventOwner] = {
    "MLB": TeamEventOwner("MLB", "OUTRIGHT_WINNER", "wow.mlb-game-win-probability-expert"),
    "NFL": TeamEventOwner("NFL", "OUTRIGHT_WINNER", "wow.nfl-game-win-probability-expert"),
    "NCAAF": TeamEventOwner("NCAAF", "OUTRIGHT_WINNER", "wow.ncaaf-game-win-probability-expert"),
    "NBA": TeamEventOwner("NBA", "OUTRIGHT_WINNER", "wow.nba-game-win-probability-expert"),
    "WNBA": TeamEventOwner("WNBA", "OUTRIGHT_WINNER", "wow.wnba-game-win-probability-expert"),
    "NCAAB": TeamEventOwner("NCAAB", "OUTRIGHT_WINNER", "wow.ncaab-game-win-probability-expert"),
    "NHL": TeamEventOwner("NHL", "OUTRIGHT_WINNER", "wow.nhl-game-win-probability-expert"),
    "SOCCER": TeamEventOwner("SOCCER", "OUTRIGHT_WINNER", "wow.soccer-match-win-probability-expert"),
    "TENNIS": TeamEventOwner("TENNIS", "OUTRIGHT_WINNER", "wow.tennis-match-win-probability-expert"),
    "PGA": TeamEventOwner("PGA", "OUTRIGHT_WINNER", "wow.pga-event-win-probability-expert"),
    "MMA": TeamEventOwner("MMA", "OUTRIGHT_WINNER", "wow.mma-fight-win-probability-expert"),
    "BOXING": TeamEventOwner("BOXING", "OUTRIGHT_WINNER", "wow.boxing-fight-win-probability-expert"),
}


def controlling_specialist(sport: str) -> str:
    normalized = str(sport or "").strip().upper()
    try:
        return TEAM_EVENT_OWNERS[normalized].controlling_specialist
    except KeyError as exc:
        raise KeyError(f"V17_TEAM_EVENT_OWNER_UNKNOWN:{normalized}") from exc


def validate_owner_registry() -> None:
    expected = set(EXPECTED_TEAM_EVENT_SPORTS)
    actual = set(TEAM_EVENT_OWNERS)
    if actual != expected:
        raise RuntimeError(
            f"V17_TEAM_EVENT_OWNER_COVERAGE_DRIFT:missing={sorted(expected-actual)}:extra={sorted(actual-expected)}"
        )
    specialist_ids = [owner.controlling_specialist for owner in TEAM_EVENT_OWNERS.values()]
    if len(specialist_ids) != len(set(specialist_ids)):
        raise RuntimeError("V17_TEAM_EVENT_OWNER_DUPLICATE_SPECIALIST")
    for sport, owner in TEAM_EVENT_OWNERS.items():
        if owner.sport != sport or owner.market_family != "OUTRIGHT_WINNER":
            raise RuntimeError(f"V17_TEAM_EVENT_OWNER_IDENTITY_DRIFT:{sport}")
        if owner.can_execute:
            raise RuntimeError(f"V17_TEAM_EVENT_OWNER_EXECUTION_FORBIDDEN:{sport}")


validate_owner_registry()

__all__ = [
    "CAN_EXECUTE",
    "TEAM_EVENT_OWNERS",
    "TeamEventOwner",
    "controlling_specialist",
    "validate_owner_registry",
]
