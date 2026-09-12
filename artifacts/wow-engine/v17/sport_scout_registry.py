"""WOW V17 sport-specialist Scout team registry.

This registry defines discovery-only research teams. Scout agents may collect,
reconcile, rank, contradict, and summarize evidence, but they cannot create a
governed probability, approve a wager, or execute a wager.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

RESEARCH_CEILING = "RESEARCH_INTEREST"
CAN_EXECUTE = False


@dataclass(frozen=True)
class ScoutAgent:
    name: str
    mission: str
    evidence_domains: tuple[str, ...]
    cadence: tuple[str, ...]


@dataclass(frozen=True)
class ScoutTeam:
    team_id: str
    sport_key: str
    sport_label: str
    controlling_team_event_route: str
    controlling_prop_route: str
    agents: tuple[ScoutAgent, ...]
    edge_classes: tuple[str, ...]
    red_team_checks: tuple[str, ...]
    research_cycle: tuple[str, ...]

    def to_handoff(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "sport_key": self.sport_key,
            "sport_label": self.sport_label,
            "research_ceiling": RESEARCH_CEILING,
            "can_execute": CAN_EXECUTE,
            "controlling_team_event_route": self.controlling_team_event_route,
            "controlling_prop_route": self.controlling_prop_route,
            "agents": [asdict(agent) for agent in self.agents],
            "edge_classes": list(self.edge_classes),
            "red_team_checks": list(self.red_team_checks),
            "research_cycle": list(self.research_cycle),
        }


COMMON_AGENTS = (
    ScoutAgent("SLATE_SCOUT", "Maintain canonical event identity, schedule, venue, rest and travel context.", ("schedule", "venue", "rest", "travel", "reschedules"), ("DAILY", "GAME_DAY")),
    ScoutAgent("PERSONNEL_SCOUT", "Track availability, depth-chart, lineup and role changes with timestamps and source confidence.", ("injuries", "suspensions", "depth_chart", "lineup", "role"), ("DAILY", "PRE_GAME")),
    ScoutAgent("FORM_SCOUT", "Separate underlying skill change from variance, opponent effects and schedule strength.", ("rolling_efficiency", "season_baseline", "opponent_adjusted_form", "regression"), ("DAILY",)),
    ScoutAgent("MARKET_SCOUT", "Track opener/current price, book disagreement, line movement and stale evidence. Market data is evidence only.", ("moneyline", "spread", "total", "props", "line_movement", "book_disagreement"), ("DAILY", "PRE_GAME")),
    ScoutAgent("NEWS_INTELLIGENCE_SCOUT", "Track verified team, league, coach and beat-reporter information with freshness/confidence.", ("team_news", "league_news", "coach_comments", "beat_reporting"), ("DAILY", "PRE_GAME")),
    ScoutAgent("CONTRARIAN_RED_TEAM_SCOUT", "Try to invalidate each priority thesis before specialist handoff.", ("sample_size", "role_instability", "hidden_injury", "stale_line", "conflicting_evidence", "correlation"), ("DAILY", "FINAL_BOARD")),
)

FOOTBALL_COMMON = COMMON_AGENTS + (
    ScoutAgent("QB_SCOUT", "Study quarterback efficiency, pressure response, rushing and turnover risk.", ("epa_dropback", "pressure_splits", "blitz_splits", "cpoe", "scramble_rate", "turnover_worthy_plays"), ("TUE", "WED", "FINAL_BOARD")),
    ScoutAgent("TRENCHES_SCOUT", "Study OL/DL injuries and run/pass protection interaction.", ("pass_block", "run_block", "pressure_rate", "sack_rate", "line_injuries"), ("TUE", "WED", "FINAL_BOARD")),
    ScoutAgent("USAGE_SCOUT", "Track snaps, routes, touches, target share, red-zone and two-minute roles.", ("snap_share", "route_share", "target_share", "carry_share", "red_zone_share", "two_minute_role"), ("TUE", "THU", "PRE_GAME")),
    ScoutAgent("SCHEME_MATCHUP_SCOUT", "Find offense-defense interaction edges rather than generic team ranking gaps.", ("epa_play", "success_rate", "explosive_rate", "pace", "coverage", "blitz", "havoc", "red_zone"), ("WED", "THU")),
    ScoutAgent("WEATHER_SCOUT", "Track wind, precipitation, temperature and field effects that can alter role or efficiency.", ("wind", "precipitation", "temperature", "field_surface"), ("FRI", "PRE_GAME")),
)

CFB_TEAM = ScoutTeam(
    team_id="CFB_SCOUT_TEAM",
    sport_key="americanfootball_ncaaf",
    sport_label="CFB",
    controlling_team_event_route="LLP_TEAM_BETTING_ENGINE",
    controlling_prop_route="WOW_PROP_LANE",
    agents=FOOTBALL_COMMON,
    edge_classes=("ROLE_EDGE", "PERSONNEL_EDGE", "SCHEME_EDGE", "USAGE_EDGE", "REST_EDGE", "MARKET_DISAGREEMENT_EDGE", "INFORMATION_EDGE", "REGRESSION_EDGE", "MATCHUP_EDGE", "WEATHER_EDGE", "DEPTH_EDGE"),
    red_team_checks=("SMALL_SAMPLE", "OPPONENT_ADJUSTMENT", "QB_STATUS", "OL_STATUS", "WEATHER_SHIFT", "STALE_LINE", "MARKET_CONTRADICTION", "DATA_FRESHNESS", "IDENTITY_AMBIGUITY"),
    research_cycle=("MON_POSTMORTEM_AND_OPENERS", "TUE_PERSONNEL_AND_SCHEME", "WED_MATCHUP_DEEP_DIVE", "THU_MARKET_AND_PROP_RELEASE", "FRI_INJURY_AND_WEATHER", "SAT_FINAL_BOARD"),
)

NFL_TEAM = ScoutTeam(
    team_id="NFL_SCOUT_TEAM",
    sport_key="americanfootball_nfl",
    sport_label="NFL",
    controlling_team_event_route="LLP_TEAM_BETTING_ENGINE",
    controlling_prop_route="WOW_PROP_LANE",
    agents=FOOTBALL_COMMON,
    edge_classes=CFB_TEAM.edge_classes,
    red_team_checks=CFB_TEAM.red_team_checks + ("PRACTICE_PARTICIPATION_TREND", "INACTIVE_STATUS"),
    research_cycle=("TUE_USAGE_AND_POSTMORTEM", "WED_FIRST_PRACTICE_REPORT", "THU_TNF_FINAL_AND_SUN_PRELIM", "FRI_INJURY_PROGRESSION", "SAT_SUNDAY_PACKAGE", "SUN_INACTIVES_WEATHER_FINAL", "MON_MNF_FINAL"),
)

MLB_TEAM = ScoutTeam(
    team_id="MLB_SCOUT_TEAM",
    sport_key="baseball_mlb",
    sport_label="MLB",
    controlling_team_event_route="LLP_TEAM_BETTING_ENGINE",
    controlling_prop_route="WOW_PROP_LANE",
    agents=COMMON_AGENTS + (
        ScoutAgent("STARTING_PITCHER_SCOUT", "Study arsenal, velocity, command, workload, platoon and first-inning traits.", ("pitch_mix", "velocity", "spin", "csw", "k_rate", "bb_rate", "pitch_count", "times_through_order", "first_inning"), ("MORNING", "LINEUPS", "PRE_GAME")),
        ScoutAgent("LINEUP_HITTING_SCOUT", "Study confirmed order, platoon, pitch-type matchup, contact and power quality.", ("confirmed_lineup", "platoon", "contact", "chase", "barrel", "iso", "order_position"), ("MORNING", "LINEUPS")),
        ScoutAgent("BULLPEN_SCOUT", "Track leverage-arm availability and recent bullpen workload.", ("bullpen_usage_3d", "closer_status", "leverage_arms", "fip", "xfip"), ("MORNING", "PRE_GAME")),
        ScoutAgent("PARK_WEATHER_SCOUT", "Track park, roof, wind, temperature and humidity effects.", ("park_factor", "roof", "wind", "temperature", "humidity"), ("MORNING", "PRE_GAME")),
    ),
    edge_classes=("PITCHING_EDGE", "BULLPEN_EDGE", "LINEUP_EDGE", "PLATOON_EDGE", "ROLE_EDGE", "MARKET_DISAGREEMENT_EDGE", "INFORMATION_EDGE", "REGRESSION_EDGE", "WEATHER_EDGE", "PARK_EDGE"),
    red_team_checks=("STARTER_CONFIRMATION", "LINEUP_CONFIRMATION", "BULLPEN_DEPLETION", "PITCH_COUNT_LIMIT", "STALE_PROP_LINE", "WEATHER_SHIFT", "SMALL_SAMPLE", "IDENTITY_AMBIGUITY"),
    research_cycle=("MORNING_STARTER_AND_BULLPEN", "MIDDAY_LINEUP_WATCH", "LINEUP_CONFIRMATION", "PRE_GAME_FINAL_BOARD"),
)

BASKETBALL_COMMON = COMMON_AGENTS + (
    ScoutAgent("ROTATION_USAGE_SCOUT", "Track minutes, usage, starting status, teammate absences and role redistribution.", ("minutes", "usage", "starting_status", "rotation", "on_off", "ball_handler_role"), ("MORNING", "INJURY_REPORT", "PRE_GAME")),
    ScoutAgent("MATCHUP_PACE_SCOUT", "Study pace and shot-profile interaction at team and player level.", ("pace", "off_rating", "def_rating", "rim_rate", "three_rate", "transition", "rebounding", "turnovers"), ("MORNING", "PRE_GAME")),
    ScoutAgent("SCHEDULE_FATIGUE_SCOUT", "Track back-to-backs, condensed schedule, travel, altitude and overtime carryover.", ("back_to_back", "three_in_four", "four_in_six", "travel", "altitude", "overtime"), ("MORNING",)),
)

NBA_TEAM = ScoutTeam(
    team_id="NBA_SCOUT_TEAM",
    sport_key="basketball_nba",
    sport_label="NBA",
    controlling_team_event_route="LLP_TEAM_BETTING_ENGINE",
    controlling_prop_route="WOW_PROP_LANE",
    agents=BASKETBALL_COMMON,
    edge_classes=("USAGE_EDGE", "ROTATION_EDGE", "PERSONNEL_EDGE", "PACE_EDGE", "MATCHUP_EDGE", "REST_EDGE", "MARKET_DISAGREEMENT_EDGE", "INFORMATION_EDGE", "REGRESSION_EDGE"),
    red_team_checks=("LATE_SCRATCH", "MINUTES_LIMIT", "BLOWOUT_SENSITIVITY", "ROLE_INSTABILITY", "BACK_TO_BACK", "STALE_LINE", "IDENTITY_AMBIGUITY"),
    research_cycle=("MORNING_SLATE", "INJURY_REPORT_UPDATES", "STARTER_CONFIRMATION", "PRE_GAME_FINAL_BOARD"),
)

WNBA_TEAM = ScoutTeam(
    team_id="WNBA_SCOUT_TEAM",
    sport_key="basketball_wnba",
    sport_label="WNBA",
    controlling_team_event_route="LLP_TEAM_BETTING_ENGINE",
    controlling_prop_route="WOW_PROP_LANE",
    agents=BASKETBALL_COMMON,
    edge_classes=NBA_TEAM.edge_classes,
    red_team_checks=NBA_TEAM.red_team_checks,
    research_cycle=NBA_TEAM.research_cycle,
)

REGISTRY = {team.sport_key: team for team in (CFB_TEAM, NFL_TEAM, MLB_TEAM, NBA_TEAM, WNBA_TEAM)}

GENERIC_TEAM = ScoutTeam(
    team_id="GENERIC_MULTI_SPORT_SCOUT_TEAM",
    sport_key="*",
    sport_label="GENERIC",
    controlling_team_event_route="LLP_TEAM_BETTING_ENGINE",
    controlling_prop_route="WOW_PROP_LANE",
    agents=COMMON_AGENTS,
    edge_classes=("PERSONNEL_EDGE", "MATCHUP_EDGE", "MARKET_DISAGREEMENT_EDGE", "INFORMATION_EDGE", "REGRESSION_EDGE"),
    red_team_checks=("SMALL_SAMPLE", "STALE_LINE", "CONFLICTING_EVIDENCE", "DATA_FRESHNESS", "IDENTITY_AMBIGUITY"),
    research_cycle=("DAILY_DISCOVERY", "PRE_GAME_FINAL_BOARD"),
)


def scout_team_for(sport_key: str) -> ScoutTeam:
    return REGISTRY.get(str(sport_key), GENERIC_TEAM)


def registry_payload() -> dict[str, Any]:
    return {
        "schema_version": "wow.v17.scout_team_registry.v1",
        "research_ceiling": RESEARCH_CEILING,
        "can_execute": CAN_EXECUTE,
        "teams": {key: team.to_handoff() for key, team in REGISTRY.items()},
        "generic_team": GENERIC_TEAM.to_handoff(),
        "governance": {
            "scout_probability_authority": False,
            "sportsbook_probability_authority": False,
            "final_probability_requires_controlling_specialist": True,
            "v17_terminal_reducer_is_terminal_authority": True,
            "can_execute": False,
        },
    }


__all__ = ["ScoutAgent", "ScoutTeam", "REGISTRY", "GENERIC_TEAM", "scout_team_for", "registry_payload"]
