"""Research-only manifest for the WOW V17 cross-sport in-play winner program.

This module defines experiment ownership and evidence requirements only. It is not
imported by production scoring, does not produce probability, and cannot promote
or execute anything.
"""
from __future__ import annotations

from dataclasses import dataclass

from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
AUTOMATIC_PROMOTION = False
MARKET_PROBABILITY_AUTHORITY = False


@dataclass(frozen=True)
class LiveResearchLane:
    sport: str
    outcome_space: tuple[str, ...]
    state_families: tuple[str, ...]
    scoring_rule: str
    calibration_family: str
    lower_bound_family: str
    provider_probability_allowed: bool = False
    probability_publishable: bool = False
    automatic_promotion: bool = False
    can_execute: bool = False


LANES: dict[str, LiveResearchLane] = {
    "MLB": LiveResearchLane(
        "MLB",
        ("HOME_WIN", "AWAY_WIN"),
        ("inning_half_outs", "score", "base_state", "batting_order", "pitcher_bullpen", "remaining_opportunity", "extra_inning_regime"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "NFL": LiveResearchLane(
        "NFL",
        ("HOME_WIN", "AWAY_WIN"),
        ("quarter_clock", "score", "possession", "down_distance_field_position", "timeouts", "drive_state"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "NBA": LiveResearchLane(
        "NBA",
        ("HOME_WIN", "AWAY_WIN"),
        ("period_clock", "score", "possession", "foul_bonus", "timeouts", "lineup_availability"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "WNBA": LiveResearchLane(
        "WNBA",
        ("HOME_WIN", "AWAY_WIN"),
        ("period_clock", "score", "possession", "foul_bonus", "timeouts", "lineup_availability"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "NCAAF": LiveResearchLane(
        "NCAAF",
        ("HOME_WIN", "AWAY_WIN"),
        ("quarter_clock", "score", "possession", "down_distance_field_position", "timeouts", "drive_state"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "NCAAB": LiveResearchLane(
        "NCAAB",
        ("HOME_WIN", "AWAY_WIN"),
        ("period_clock", "score", "possession", "foul_bonus", "timeouts", "lineup_availability"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "NHL": LiveResearchLane(
        "NHL",
        ("HOME_WIN", "AWAY_WIN"),
        ("period_clock", "score", "manpower", "goalie_state", "shots_chances"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "SOCCER": LiveResearchLane(
        "SOCCER",
        ("HOME_WIN", "DRAW", "AWAY_WIN"),
        ("minute_stoppage", "score", "cards_player_count", "substitutions", "competition_extra_time_rules"),
        "MULTICLASS_LOG_LOSS_BRIER",
        "TIME_SPLIT_MULTICLASS_CALIBRATION",
        "CLASSWISE_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "TENNIS": LiveResearchLane(
        "TENNIS",
        ("PLAYER_A_WIN", "PLAYER_B_WIN"),
        ("set_game_point_score", "server", "surface", "retirement_settlement"),
        "BINARY_LOG_LOSS_BRIER",
        "TIME_SPLIT_BINARY_CALIBRATION",
        "LOCAL_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "PGA": LiveResearchLane(
        "PGA",
        ("FIELD_OUTCOMES",),
        ("round_hole", "strokes_position", "field_state", "cut_withdrawal_rules"),
        "MARKET_SPECIFIC_PROPER_SCORE",
        "MARKET_SPECIFIC_TIME_SPLIT_CALIBRATION",
        "MARKET_SPECIFIC_RELIABILITY_BOUND",
    ),
    "MMA": LiveResearchLane(
        "MMA",
        ("FIGHTER_A_WIN", "FIGHTER_B_WIN", "DRAW_NO_CONTEST"),
        ("round_clock", "objective_state_feed", "scheduled_rounds", "settlement_rules"),
        "MULTICLASS_LOG_LOSS_BRIER",
        "TIME_SPLIT_MULTICLASS_CALIBRATION",
        "CLASSWISE_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "BOXING": LiveResearchLane(
        "BOXING",
        ("FIGHTER_A_WIN", "FIGHTER_B_WIN", "DRAW_NO_CONTEST"),
        ("round_clock", "objective_state_feed", "scheduled_rounds", "settlement_rules"),
        "MULTICLASS_LOG_LOSS_BRIER",
        "TIME_SPLIT_MULTICLASS_CALIBRATION",
        "CLASSWISE_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
    "CRICKET": LiveResearchLane(
        "CRICKET",
        ("TEAM_A_WIN", "TEAM_B_WIN", "TIE_NO_RESULT"),
        ("innings", "overs_balls", "runs_wickets", "target_required_rate", "match_format", "tie_no_result_rules"),
        "MULTICLASS_LOG_LOSS_BRIER",
        "TIME_SPLIT_MULTICLASS_CALIBRATION",
        "CLASSWISE_RELIABILITY_PLUS_BLOCK_BOOTSTRAP",
    ),
}


def validate_manifest() -> dict[str, object]:
    expected = set(EXPECTED_TEAM_EVENT_SPORTS)
    actual = set(LANES)
    if actual != expected:
        raise ValueError(f"LIVE_RESEARCH_SPORT_COVERAGE_MISMATCH:{sorted(expected ^ actual)}")
    for sport, lane in LANES.items():
        if lane.sport != sport:
            raise ValueError(f"LIVE_RESEARCH_SPORT_IDENTITY_MISMATCH:{sport}")
        if lane.provider_probability_allowed:
            raise ValueError(f"LIVE_RESEARCH_PROVIDER_PROBABILITY_AUTHORITY_FORBIDDEN:{sport}")
        if lane.probability_publishable or lane.automatic_promotion or lane.can_execute:
            raise ValueError(f"LIVE_RESEARCH_GOVERNANCE_INVALID:{sport}")
        if not lane.outcome_space or not lane.state_families:
            raise ValueError(f"LIVE_RESEARCH_CONTRACT_INCOMPLETE:{sport}")
    return {
        "status": "EXPERIMENT_CREATED",
        "sports": sorted(actual),
        "sport_n": len(actual),
        "probability_publishable": False,
        "automatic_promotion": False,
        "market_probability_authority": False,
        "can_execute": False,
    }


MANIFEST_RECEIPT = validate_manifest()
