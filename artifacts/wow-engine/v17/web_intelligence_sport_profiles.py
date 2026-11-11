"""Research-only sport-specific evidence profiles. These are unfitted heuristics.

They are not probability models, do not grant fitted-model capability, and never
authorize market publication. Each domain is separate from exact settlement.
"""
from __future__ import annotations

CAN_EXECUTE = False
NBA_WEIGHTS = {
    "TEAM_STRENGTH": 0.30, "INJURIES_LINEUPS": 0.25,
    "OPPONENT_MATCHUP": 0.20, "PACE_GAME_SCRIPT": 0.15,
    "VENUE_FATIGUE": 0.10,
}
WEIGHT_PROFILES: dict[str, dict[str, float]] = {
    "NBA": NBA_WEIGHTS,
    "WNBA": {"TEAM_STRENGTH": .25, "INJURIES_LINEUPS": .30,
             "OPPONENT_MATCHUP": .20, "PACE_GAME_SCRIPT": .15, "VENUE_FATIGUE": .10},
    "NCAAB": {"TEAM_STRENGTH": .25, "ROSTER_AVAILABILITY": .25,
              "STYLE_MATCHUP": .25, "TEMPO_REBOUNDING": .15, "SITE_REST": .10},
    "NFL": {"QB_PERSONNEL": .30, "TRENCH_MATCHUP": .20,
            "OPPONENT_ADJUSTED_EFFICIENCY": .25, "SCHEME_GAME_SCRIPT": .15,
            "WEATHER_REST": .10},
    "NCAAF": {"QB_PERSONNEL": .25, "TRENCH_MATCHUP": .20,
              "OPPONENT_ADJUSTED_EFFICIENCY": .25, "SCHEME_GAME_SCRIPT": .20,
              "WEATHER_REST": .10},
    "MLB": {"STARTING_PITCHING": .30, "LINEUP_PLATOON": .25, "BULLPEN_STATE": .20,
            "OFFENSE_DEFENSE_FORM": .15, "PARK_WEATHER": .10},
    "NHL": {"STARTING_GOALIE": .30, "SKATER_AVAILABILITY": .20, "SHOT_QUALITY": .20,
            "SPECIAL_TEAMS": .20, "REST_VENUE": .10},
    "SOCCER": {"EXPECTED_GOALS_FORM": .25, "STARTING_XI": .25,
               "TACTICAL_MATCHUP": .20, "SET_PIECES": .15, "REST_VENUE": .15},
    "TENNIS": {"SERVE_RETURN": .30, "SURFACE_FORM": .25, "PHYSICAL_STATUS": .20,
               "STYLE_MATCHUP": .15, "CONDITIONS_REST": .10},
    "GOLF": {"STROKES_GAINED": .30, "APPROACH_OFF_TEE": .25, "SHORT_GAME_PUTTING": .20,
             "COURSE_FIT": .15, "WEATHER_FIELD": .10},
    "MMA": {"STRIKING_DEFENSE": .25, "GRAPPLING_WRESTLING": .25, "PACE_CARDIO": .20,
            "OPPONENT_MATCHUP": .20, "WEIGH_IN_HEALTH": .10},
    "BOXING": {"STRIKING_DEFENSE": .30, "POWER_DURABILITY": .25, "PACE_CARDIO": .20,
               "OPPONENT_MATCHUP": .15, "WEIGH_IN_HEALTH": .10},
    "CRICKET": {"BATTING_FORM": .25, "BOWLING_MATCHUP": .25, "XI_AVAILABILITY": .20,
                "PITCH_WEATHER": .20, "TOSS_VENUE": .10},
}
SPORT_ALIASES = {
    "BASKETBALL_NBA": "NBA", "BASKETBALL_WNBA": "WNBA",
    "BASKETBALL_NCAAB": "NCAAB", "COLLEGE_BASKETBALL": "NCAAB",
    "AMERICANFOOTBALL_NFL": "NFL", "AMERICANFOOTBALL_NCAAF": "NCAAF",
    "CFB": "NCAAF", "COLLEGE_FOOTBALL": "NCAAF",
    "BASEBALL_MLB": "MLB", "ICEHOCKEY_NHL": "NHL",
    "ICE_HOCKEY_NHL": "NHL", "SOCCER_FOOTBALL": "SOCCER",
    "FOOTBALL_SOCCER": "SOCCER", "MLS": "SOCCER", "EPL": "SOCCER",
    "CHAMPIONS_LEAGUE": "SOCCER", "ATP": "TENNIS", "WTA": "TENNIS",
    "PGA": "GOLF", "PGA_GOLF": "GOLF", "UFC": "MMA",
    "MIXED_MARTIAL_ARTS": "MMA", "T20": "CRICKET", "ODI": "CRICKET",
}
BASE_MARKETS = frozenset({"MONEYLINE", "SPREAD", "TOTAL"})
MARKETS: dict[str, frozenset[str]] = {
    "NBA": BASE_MARKETS, "WNBA": BASE_MARKETS, "NCAAB": BASE_MARKETS,
    "NFL": BASE_MARKETS, "NCAAF": BASE_MARKETS,
    "MLB": BASE_MARKETS, "NHL": BASE_MARKETS,
    # Explicitly distinguish three-way soccer and boxing from two-way moneyline.
    "SOCCER": frozenset({"THREE_WAY_1X2", "DRAW_NO_BET", "SPREAD", "TOTAL"}),
    "TENNIS": frozenset({"MATCH_WINNER", "SPREAD", "TOTAL"}),
    "GOLF": frozenset({"OUTRIGHT", "MATCH_WINNER"}),
    "MMA": frozenset({"MONEYLINE", "TOTAL"}),
    "BOXING": frozenset({"THREE_WAY_1X2", "TOTAL"}),
    "CRICKET": frozenset({"MATCH_WINNER", "TOTAL", "SPREAD"}),
}
# Normalize existing Scout domain names; these mappings do not create evidence.
DOMAIN_FACTORS: dict[str, dict[str, str]] = {
    "NBA": {"form": "TEAM_STRENGTH", "team_strength": "TEAM_STRENGTH",
            "availability": "INJURIES_LINEUPS", "rotation": "INJURIES_LINEUPS",
            "matchup": "OPPONENT_MATCHUP", "pace": "PACE_GAME_SCRIPT",
            "game_script": "PACE_GAME_SCRIPT", "venue": "VENUE_FATIGUE",
            "rest": "VENUE_FATIGUE", "schedule": "VENUE_FATIGUE"},
    "WNBA": {"form": "TEAM_STRENGTH", "team_strength": "TEAM_STRENGTH",
             "availability": "INJURIES_LINEUPS", "rotation": "INJURIES_LINEUPS",
             "matchup": "OPPONENT_MATCHUP", "pace": "PACE_GAME_SCRIPT",
             "game_script": "PACE_GAME_SCRIPT", "rest": "VENUE_FATIGUE",
             "schedule": "VENUE_FATIGUE"},
    "NCAAB": {"form": "TEAM_STRENGTH", "availability": "ROSTER_AVAILABILITY",
              "rotation": "ROSTER_AVAILABILITY", "matchup": "STYLE_MATCHUP",
              "pace": "TEMPO_REBOUNDING", "rebounding": "TEMPO_REBOUNDING",
              "context": "SITE_REST", "schedule": "SITE_REST"},
    "NFL": {"personnel": "QB_PERSONNEL", "practice": "QB_PERSONNEL",
            "trench": "TRENCH_MATCHUP", "efficiency": "OPPONENT_ADJUSTED_EFFICIENCY",
            "form": "OPPONENT_ADJUSTED_EFFICIENCY", "matchup": "SCHEME_GAME_SCRIPT",
            "scheme": "SCHEME_GAME_SCRIPT", "weather": "WEATHER_REST",
            "schedule": "WEATHER_REST"},
    "NCAAF": {"personnel": "QB_PERSONNEL", "trench": "TRENCH_MATCHUP",
              "efficiency": "OPPONENT_ADJUSTED_EFFICIENCY",
              "form": "OPPONENT_ADJUSTED_EFFICIENCY", "matchup": "SCHEME_GAME_SCRIPT",
              "scheme": "SCHEME_GAME_SCRIPT", "weather": "WEATHER_REST",
              "schedule": "WEATHER_REST"},
    "MLB": {"starter": "STARTING_PITCHING", "lineup": "LINEUP_PLATOON",
            "bullpen": "BULLPEN_STATE", "form": "OFFENSE_DEFENSE_FORM",
            "matchup": "OFFENSE_DEFENSE_FORM", "weather": "PARK_WEATHER",
            "park": "PARK_WEATHER"},
    "NHL": {"goalie": "STARTING_GOALIE", "availability": "SKATER_AVAILABILITY",
            "lines": "SKATER_AVAILABILITY", "matchup": "SHOT_QUALITY",
            "shot_quality": "SHOT_QUALITY", "special_teams": "SPECIAL_TEAMS",
            "schedule": "REST_VENUE", "rest": "REST_VENUE"},
    "SOCCER": {"form": "EXPECTED_GOALS_FORM", "xg": "EXPECTED_GOALS_FORM",
               "lineup": "STARTING_XI", "availability": "STARTING_XI",
               "matchup": "TACTICAL_MATCHUP", "tactics": "TACTICAL_MATCHUP",
               "set_pieces": "SET_PIECES", "rest": "REST_VENUE",
               "schedule": "REST_VENUE"},
    "TENNIS": {"serve_return": "SERVE_RETURN", "form": "SURFACE_FORM",
               "surface": "SURFACE_FORM", "availability": "PHYSICAL_STATUS",
               "physical": "PHYSICAL_STATUS", "matchup": "STYLE_MATCHUP",
               "conditions": "CONDITIONS_REST", "schedule": "CONDITIONS_REST"},
    "GOLF": {"strokes_gained": "STROKES_GAINED", "approach": "APPROACH_OFF_TEE",
             "short_game": "SHORT_GAME_PUTTING", "course": "COURSE_FIT",
             "weather": "WEATHER_FIELD", "field": "WEATHER_FIELD"},
    "MMA": {"striking": "STRIKING_DEFENSE", "grappling": "GRAPPLING_WRESTLING",
            "cardio": "PACE_CARDIO", "matchup": "OPPONENT_MATCHUP",
            "weigh_in": "WEIGH_IN_HEALTH"},
    "BOXING": {"striking": "STRIKING_DEFENSE", "power": "POWER_DURABILITY",
               "cardio": "PACE_CARDIO", "matchup": "OPPONENT_MATCHUP",
               "weigh_in": "WEIGH_IN_HEALTH"},
    "CRICKET": {"batting": "BATTING_FORM", "bowling": "BOWLING_MATCHUP",
                "lineup": "XI_AVAILABILITY", "pitch": "PITCH_WEATHER",
                "weather": "PITCH_WEATHER", "toss": "TOSS_VENUE"},
}
NO_LINE_MARKETS = frozenset({"MONEYLINE", "MATCH_WINNER", "OUTRIGHT",
                             "THREE_WAY_1X2", "DRAW_NO_BET"})


def normalize_sport(sport: object) -> str:
    key = str(sport or "").upper().strip().replace(" ", "_")
    return SPORT_ALIASES.get(key, key)


def weights_for(sport: object) -> dict[str, float] | None:
    weights = WEIGHT_PROFILES.get(normalize_sport(sport))
    return dict(weights) if weights else None


def validate_profiles() -> None:
    for sport, weights in WEIGHT_PROFILES.items():
        assert set(weights) and len(weights) == 5, sport
        assert abs(sum(weights.values()) - 1.0) < 1e-9, sport
        assert all(0 < value <= 1 for value in weights.values()), sport
        assert sport in MARKETS and sport in DOMAIN_FACTORS, sport
        assert set(DOMAIN_FACTORS[sport].values()) <= set(weights), sport


validate_profiles()
