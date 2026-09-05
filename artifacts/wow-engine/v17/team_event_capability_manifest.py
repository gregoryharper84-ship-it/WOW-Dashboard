"""Machine-readable V17 team/event certified model coverage.

This manifest describes active repository model coverage; it does not create
model capability. A sport enters CERTIFIED_TEAM_EVENT_SPORTS only after its
exact specialist artifact, governed evidence contract, numerical verification,
calibration/bounds path, and backend registration are reviewed and active.
"""
from __future__ import annotations

from dataclasses import dataclass

CAN_EXECUTE = False

MLB_GAME_WIN_PROBABILITY_EXPERT = "MLB_GAME_WIN_PROBABILITY_EXPERT"

CERTIFIED_TEAM_EVENT_SPORTS: dict[str, str] = {
    "MLB": MLB_GAME_WIN_PROBABILITY_EXPERT,
}

KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS = frozenset(
    {
        "SOCCER",
        "MLS",
        "NBA",
        "WNBA",
        "NFL",
        "NHL",
        "TENNIS",
        "GOLF",
        "COMBAT",
        "MOTORSPORTS",
    }
)


@dataclass(frozen=True)
class TeamEventCapability:
    sport: str
    status: str
    controlling_specialist: str | None
    blocker: str | None
    can_execute: bool = False



def normalize_team_event_sport(value: str) -> str:
    sport = str(value or "").strip().upper()
    aliases = {
        "BASEBALL": "MLB",
        "MAJOR LEAGUE BASEBALL": "MLB",
        "MAJOR_LEAGUE_BASEBALL": "MLB",
        "MAJOR LEAGUE SOCCER": "MLS",
        "MAJOR_LEAGUE_SOCCER": "MLS",
        "FOOTBALL_SOCCER": "SOCCER",
    }
    return aliases.get(sport, sport)



def team_event_capability(value: str) -> TeamEventCapability:
    sport = normalize_team_event_sport(value)
    specialist = CERTIFIED_TEAM_EVENT_SPORTS.get(sport)
    if specialist:
        return TeamEventCapability(
            sport=sport,
            status="AVAILABLE",
            controlling_specialist=specialist,
            blocker=None,
            can_execute=False,
        )
    return TeamEventCapability(
        sport=sport,
        status="MODEL_UNAVAILABLE",
        controlling_specialist=None,
        blocker="TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED",
        can_execute=False,
    )
