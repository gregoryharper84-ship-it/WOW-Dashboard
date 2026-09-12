"""Machine-readable V17 team/event certified model coverage.

This manifest describes intended cross-sport team/event coverage and the exact
minimum input families each future bridge must own.  It does not create model
capability.  A sport becomes production-capable only when an exact specialist
artifact, governed evidence contract, numerical verification, calibration/bounds
path, and runtime bridge registration are all active.
"""
from __future__ import annotations

from dataclasses import dataclass

CAN_EXECUTE = False

MLB_GAME_WIN_PROBABILITY_EXPERT = "MLB_GAME_WIN_PROBABILITY_EXPERT"

# The catalog is intentionally broader than the production bridge registry.  A
# catalog entry means LLP knows the sport/contract shape; it does NOT mean the
# governed backend can score that sport today.
EXPECTED_TEAM_EVENT_SPORTS = (
    "MLB",
    "NFL",
    "NBA",
    "WNBA",
    "NCAAF",
    "NCAAB",
    "NHL",
    "SOCCER",
    "TENNIS",
    "PGA",
)

# Minimum bridge-owned inputs.  Universal identity/status/settlement checks still
# apply in addition to these sport-specific families.
TEAM_EVENT_INPUT_CONTRACTS: dict[str, tuple[str, ...]] = {
    "MLB": (
        "official_event_id",
        "home_team",
        "away_team",
        "venue",
        "home_starting_pitcher",
        "away_starting_pitcher",
        "home_starter_status",
        "away_starter_status",
        "home_lineup_status",
        "away_lineup_status",
        "settlement_basis",
    ),
    "NFL": (
        "official_event_id",
        "home_team",
        "away_team",
        "quarterback_status",
        "injury_report",
        "weather_or_roof",
        "rest_travel",
        "settlement_basis",
    ),
    "NBA": (
        "official_event_id",
        "home_team",
        "away_team",
        "injury_report",
        "expected_starters_rotation",
        "rest_back_to_back",
        "settlement_basis",
    ),
    "WNBA": (
        "official_event_id",
        "home_team",
        "away_team",
        "injury_report",
        "expected_starters_rotation",
        "rest_back_to_back",
        "settlement_basis",
    ),
    "NCAAF": (
        "official_event_id",
        "home_team",
        "away_team",
        "quarterback_status",
        "team_power_inputs",
        "injury_news_status",
        "venue_weather",
        "settlement_basis",
    ),
    "NCAAB": (
        "official_event_id",
        "home_team",
        "away_team",
        "team_strength_inputs",
        "injuries_suspensions",
        "venue",
        "tempo_profile",
        "settlement_basis",
    ),
    "NHL": (
        "official_event_id",
        "home_team",
        "away_team",
        "goalie_status",
        "injury_report",
        "rest_travel",
        "settlement_basis",
    ),
    "SOCCER": (
        "official_event_id",
        "home_team",
        "away_team",
        "starting_xi_status",
        "injury_report",
        "competition_rules",
        "home_draw_away_outcome_space",
        "settlement_basis",
    ),
    "TENNIS": (
        "official_event_id",
        "home_team",
        "away_team",
        "surface",
        "retirement_settlement_rules",
        "participant_status",
        "tournament_round",
        "settlement_basis",
    ),
    "PGA": (
        "official_event_id",
        "event_field_identity",
        "market_type",
        "tournament_or_h2h_settlement",
        "field_status",
        "withdrawal_dq_rules",
        "settlement_basis",
    ),
}

# Certification is deliberately narrower than discovery/catalog support.
CERTIFIED_TEAM_EVENT_SPORTS: dict[str, str] = {
    "MLB": MLB_GAME_WIN_PROBABILITY_EXPERT,
}

KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS = frozenset(
    sport for sport in EXPECTED_TEAM_EVENT_SPORTS if sport not in CERTIFIED_TEAM_EVENT_SPORTS
)


@dataclass(frozen=True)
class TeamEventCapability:
    sport: str
    status: str
    controlling_specialist: str | None
    blocker: str | None
    required_inputs: tuple[str, ...]
    can_execute: bool = False


def normalize_team_event_sport(value: str) -> str:
    sport = str(value or "").strip().upper()
    aliases = {
        "BASEBALL": "MLB",
        "BASEBALL_MLB": "MLB",
        "MAJOR LEAGUE BASEBALL": "MLB",
        "MAJOR_LEAGUE_BASEBALL": "MLB",
        "MLS": "SOCCER",
        "MAJOR LEAGUE SOCCER": "SOCCER",
        "MAJOR_LEAGUE_SOCCER": "SOCCER",
        "FOOTBALL_SOCCER": "SOCCER",
        "CFB": "NCAAF",
        "COLLEGE FOOTBALL": "NCAAF",
        "NCAA FOOTBALL": "NCAAF",
        "CBB": "NCAAB",
        "COLLEGE BASKETBALL": "NCAAB",
        "NCAA BASKETBALL": "NCAAB",
        "GOLF": "PGA",
    }
    return aliases.get(sport, sport)


def team_event_capability(value: str) -> TeamEventCapability:
    sport = normalize_team_event_sport(value)
    specialist = CERTIFIED_TEAM_EVENT_SPORTS.get(sport)
    required_inputs = TEAM_EVENT_INPUT_CONTRACTS.get(sport, ())
    if specialist:
        return TeamEventCapability(
            sport=sport,
            status="AVAILABLE",
            controlling_specialist=specialist,
            blocker=None,
            required_inputs=required_inputs,
            can_execute=False,
        )
    return TeamEventCapability(
        sport=sport,
        status="MODEL_UNAVAILABLE",
        controlling_specialist=None,
        blocker="TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED",
        required_inputs=required_inputs,
        can_execute=False,
    )


__all__ = [
    "CAN_EXECUTE",
    "CERTIFIED_TEAM_EVENT_SPORTS",
    "EXPECTED_TEAM_EVENT_SPORTS",
    "KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS",
    "MLB_GAME_WIN_PROBABILITY_EXPERT",
    "TEAM_EVENT_INPUT_CONTRACTS",
    "TeamEventCapability",
    "normalize_team_event_sport",
    "team_event_capability",
]
