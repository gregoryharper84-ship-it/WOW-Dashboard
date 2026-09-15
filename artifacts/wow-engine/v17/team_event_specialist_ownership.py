"""Canonical V17 team/event routing ownership for the first-six certification lanes.

Ownership is routing governance only. It does not imply a fitted model exists,
does not certify a model, and never grants probability publication or execution.
Exactly one controlling specialist is declared for each route.
"""
from __future__ import annotations

from dataclasses import dataclass

CAN_EXECUTE = False
MARKET_FAMILY = "OUTRIGHT_WINNER"


@dataclass(frozen=True)
class TeamEventOwner:
    sport: str
    controlling_specialist: str
    market_family: str = MARKET_FAMILY
    probability_publishable: bool = False
    can_execute: bool = False


FIRST_SIX_TEAM_EVENT_OWNERS: dict[str, TeamEventOwner] = {
    "NCAAF": TeamEventOwner("NCAAF", "wow.ncaaf-game-win-probability-expert"),
    "NBA": TeamEventOwner("NBA", "wow.nba-game-win-probability-expert"),
    "WNBA": TeamEventOwner("WNBA", "wow.wnba-game-win-probability-expert"),
    "NCAAB": TeamEventOwner("NCAAB", "wow.ncaab-game-win-probability-expert"),
    "SOCCER": TeamEventOwner("SOCCER", "wow.soccer-match-win-probability-expert"),
    "TENNIS": TeamEventOwner("TENNIS", "wow.tennis-match-win-probability-expert"),
}


def owner_for(sport: str) -> TeamEventOwner:
    normalized = str(sport or "").strip().upper()
    try:
        return FIRST_SIX_TEAM_EVENT_OWNERS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported first-six team/event sport: {normalized}") from exc


def validate_unique_ownership() -> None:
    sports = [owner.sport for owner in FIRST_SIX_TEAM_EVENT_OWNERS.values()]
    specialists = [owner.controlling_specialist for owner in FIRST_SIX_TEAM_EVENT_OWNERS.values()]
    if len(sports) != len(set(sports)):
        raise RuntimeError("TEAM_EVENT_OWNER_DUPLICATE_SPORT")
    if len(specialists) != len(set(specialists)):
        raise RuntimeError("TEAM_EVENT_OWNER_DUPLICATE_SPECIALIST")
    if any(owner.can_execute for owner in FIRST_SIX_TEAM_EVENT_OWNERS.values()):
        raise RuntimeError("TEAM_EVENT_OWNER_EXECUTION_FORBIDDEN")
    if any(owner.probability_publishable for owner in FIRST_SIX_TEAM_EVENT_OWNERS.values()):
        raise RuntimeError("TEAM_EVENT_OWNER_CANNOT_PUBLISH_PROBABILITY")


validate_unique_ownership()

__all__ = [
    "CAN_EXECUTE",
    "FIRST_SIX_TEAM_EVENT_OWNERS",
    "MARKET_FAMILY",
    "TeamEventOwner",
    "owner_for",
    "validate_unique_ownership",
]
