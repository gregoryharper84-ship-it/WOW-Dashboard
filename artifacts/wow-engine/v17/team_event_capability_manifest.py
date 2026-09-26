"""Machine-readable V17 team/event specialist coverage and input contracts.

This manifest describes intended cross-sport team/event coverage and the exact
minimum input families each bridge must own. A sport becomes production-capable
only when an exact fitted specialist artifact, governed evidence contract,
independent numerical verification, calibration/bounds path, runtime bridge
registration, and terminal-governance path are all active.

Specialist identity declarations are not certification. Importability,
registration, numerical output, or a matching specialist name cannot promote a
sport without an immutable governed certification receipt.
"""
from __future__ import annotations

from dataclasses import dataclass

CAN_EXECUTE = False

MLB_GAME_WIN_PROBABILITY_EXPERT = "MLB_GAME_WIN_PROBABILITY_EXPERT"
WNBA_GAME_WIN_PROBABILITY_EXPERT = "WNBA_GAME_WIN_PROBABILITY_EXPERT_V1"
NHL_GAME_WIN_PROBABILITY_EXPERT = "NHL_GAME_WIN_PROBABILITY_EXPERT_V1"
SOCCER_1X2_WIN_PROBABILITY_EXPERT = "SOCCER_1X2_WIN_PROBABILITY_EXPERT_V1"
TENNIS_MATCH_WIN_PROBABILITY_EXPERT = "TENNIS_MATCH_WIN_PROBABILITY_EXPERT_V1"
MMA_FIGHT_WIN_PROBABILITY_EXPERT = "MMA_FIGHT_WIN_PROBABILITY_EXPERT_V1"

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
    "MMA",
    "BOXING",
    "CRICKET",
)

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
    "MMA": (
        "official_event_id",
        "home_team",
        "away_team",
        "weight_class",
        "scheduled_rounds",
        "participant_status",
        "weigh_in_status",
        "no_contest_draw_outcome_space",
        "settlement_basis",
    ),
    "BOXING": (
        "official_event_id",
        "home_team",
        "away_team",
        "weight_class",
        "scheduled_rounds",
        "participant_status",
        "weigh_in_status",
        "no_contest_draw_outcome_space",
        "settlement_basis",
    ),
    "CRICKET": (
        "official_event_id",
        "home_team",
        "away_team",
        "match_format",
        "venue",
        "team_strength_inputs",
        "player_availability",
        "pitch_weather_conditions",
        "tie_no_result_settlement_rules",
        "settlement_basis",
    ),
}

# Static certification remains deliberately narrow. It records unconditional
# repository certification and cannot be expanded merely because code imports or
# a bridge is registered.
CERTIFIED_TEAM_EVENT_SPORTS: dict[str, str] = {
    "MLB": MLB_GAME_WIN_PROBABILITY_EXPERT,
}

# Declared specialist identities are routing/development identities only. They do
# NOT indicate that the corresponding fitted artifact has passed certification.
# A production promotion must be backed by the immutable V17 team/event
# certification registry and its independent-verification receipt.
DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES: dict[str, str] = {
    "WNBA": WNBA_GAME_WIN_PROBABILITY_EXPERT,
    "NHL": NHL_GAME_WIN_PROBABILITY_EXPERT,
    "SOCCER": SOCCER_1X2_WIN_PROBABILITY_EXPERT,
    "TENNIS": TENNIS_MATCH_WIN_PROBABILITY_EXPERT,
    "MMA": MMA_FIGHT_WIN_PROBABILITY_EXPERT,
}

# Backward-compatible alias for code that still consumes the historical name.
# It is intentionally non-authoritative for certification.
ACTIVATABLE_TEAM_EVENT_CERTIFICATIONS: dict[str, str] = dict(
    DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES
)

KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS = frozenset(
    sport
    for sport in EXPECTED_TEAM_EVENT_SPORTS
    if sport not in CERTIFIED_TEAM_EVENT_SPORTS
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
        "ATP": "TENNIS",
        "WTA": "TENNIS",
        "ITF": "TENNIS",
        "ATP_TENNIS": "TENNIS",
        "WTA_TENNIS": "TENNIS",
        "CFB": "NCAAF",
        "COLLEGE FOOTBALL": "NCAAF",
        "NCAA FOOTBALL": "NCAAF",
        "CBB": "NCAAB",
        "COLLEGE BASKETBALL": "NCAAB",
        "NCAA BASKETBALL": "NCAAB",
        "GOLF": "PGA",
        "UFC": "MMA",
        "MIXED MARTIAL ARTS": "MMA",
        "MIXED_MARTIAL_ARTS": "MMA",
        "BOX": "BOXING",
        "T20": "CRICKET",
        "T20 CRICKET": "CRICKET",
        "T20_CRICKET": "CRICKET",
        "CRICKET/T20": "CRICKET",
    }
    return aliases.get(sport, sport)


def normalize_team_event_identity(sport: str, league: str | None = None) -> str:
    normalized_sport = normalize_team_event_sport(sport)
    if normalized_sport in TEAM_EVENT_INPUT_CONTRACTS:
        return normalized_sport
    normalized_league = normalize_team_event_sport(league or "")
    if normalized_league in TEAM_EVENT_INPUT_CONTRACTS:
        return normalized_league
    return normalized_sport


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
    "ACTIVATABLE_TEAM_EVENT_CERTIFICATIONS",
    "CAN_EXECUTE",
    "CERTIFIED_TEAM_EVENT_SPORTS",
    "DECLARED_TEAM_EVENT_SPECIALIST_IDENTITIES",
    "EXPECTED_TEAM_EVENT_SPORTS",
    "KNOWN_UNCERTIFIED_TEAM_EVENT_SPORTS",
    "MLB_GAME_WIN_PROBABILITY_EXPERT",
    "WNBA_GAME_WIN_PROBABILITY_EXPERT",
    "NHL_GAME_WIN_PROBABILITY_EXPERT",
    "SOCCER_1X2_WIN_PROBABILITY_EXPERT",
    "TENNIS_MATCH_WIN_PROBABILITY_EXPERT",
    "MMA_FIGHT_WIN_PROBABILITY_EXPERT",
    "TEAM_EVENT_INPUT_CONTRACTS",
    "TeamEventCapability",
    "normalize_team_event_identity",
    "normalize_team_event_sport",
    "team_event_capability",
]
