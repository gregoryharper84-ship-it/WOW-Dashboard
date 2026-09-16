"""V17 specialist input contracts for exact prop-market model boundaries.

This module is deliberately *not* a fitted sporting model. It provides routing,
validation and scoring-adapter boundaries that prevent research proxies from
being published as governed probability.

Governance invariants:
    can_execute = False
    DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

CAN_EXECUTE = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

SOCCER_PASSES_ATTEMPTED = "SOCCER_PASSES_ATTEMPTED"
NFL_DFS_FANTASY_SCORE = "NFL_DFS_FANTASY_SCORE"
NBA_DFS_FANTASY_SCORE = "NBA_DFS_FANTASY_SCORE"
WNBA_DFS_FANTASY_SCORE = "WNBA_DFS_FANTASY_SCORE"
MLB_HITTER_FANTASY_SCORE = "MLB_HITTER_FANTASY_SCORE"
MLB_PITCHER_FANTASY_SCORE = "MLB_PITCHER_FANTASY_SCORE"

FANTASY_SCORE_MARKETS = frozenset({
    NFL_DFS_FANTASY_SCORE,
    NBA_DFS_FANTASY_SCORE,
    WNBA_DFS_FANTASY_SCORE,
    MLB_HITTER_FANTASY_SCORE,
    MLB_PITCHER_FANTASY_SCORE,
})

FANTASY_SPECIALISTS = {
    NFL_DFS_FANTASY_SCORE: "wow.nfl-dfs-fantasy-score-expert",
    NBA_DFS_FANTASY_SCORE: "wow.nba-dfs-fantasy-score-expert",
    WNBA_DFS_FANTASY_SCORE: "wow.wnba-dfs-fantasy-score-expert",
    MLB_HITTER_FANTASY_SCORE: "wow.mlb-hitter-fantasy-score-expert",
    MLB_PITCHER_FANTASY_SCORE: "wow.mlb-pitcher-fantasy-score-expert",
}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().replace("_", " ").split())


def classify_specialist(sport: Any, prop_type: Any) -> str | None:
    """Return the exact specialist family when sport + market identity is unambiguous."""
    sport_n = _norm(sport)
    prop_n = _norm(prop_type)
    if sport_n in {"SOCCER", "FOOTBALL", "UEFA", "UCL", "MLS"} and prop_n in {
        "PASSES ATTEMPTED",
        "PASS ATTEMPTS",
        "PLAYER PASSES ATTEMPTED",
    }:
        return SOCCER_PASSES_ATTEMPTED
    if sport_n in {"NFL", "AMERICAN FOOTBALL"} and prop_n in {
        "FANTASY SCORE",
        "FANTASY POINTS",
        "DFS POINTS",
        "DFS FANTASY SCORE",
    }:
        return NFL_DFS_FANTASY_SCORE
    if sport_n == "NBA" and prop_n in {"FANTASY SCORE", "FANTASY POINTS", "DFS POINTS"}:
        return NBA_DFS_FANTASY_SCORE
    if sport_n in {"WNBA", "WOMENS NBA", "WOMEN'S NBA"} and prop_n in {
        "FANTASY SCORE", "FANTASY POINTS", "DFS POINTS"
    }:
        return WNBA_DFS_FANTASY_SCORE
    if sport_n in {"MLB", "BASEBALL", "MAJOR LEAGUE BASEBALL"} and prop_n in {
        "HITTER FANTASY SCORE", "HITTER FANTASY POINTS", "BATTER FANTASY SCORE",
    }:
        return MLB_HITTER_FANTASY_SCORE
    if sport_n in {"MLB", "BASEBALL", "MAJOR LEAGUE BASEBALL"} and prop_n in {
        "PITCHER FANTASY SCORE", "PITCHER FANTASY POINTS",
    }:
        return MLB_PITCHER_FANTASY_SCORE
    # MLB generic Fantasy Score is intentionally unresolved because hitter and pitcher
    # settlement/scoring identities are different.
    return None


@dataclass(frozen=True)
class ReadinessResult:
    market_family: str
    controlling_specialist: str
    model_input_ready: bool
    terminal_status: str
    missing_fields: tuple[str, ...]
    blockers: tuple[str, ...]
    can_execute: bool = CAN_EXECUTE
    execution_rule: str = EXECUTION_RULE

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_family": self.market_family,
            "controlling_specialist": self.controlling_specialist,
            "MODEL_INPUT_READY": self.model_input_ready,
            "terminal_status": self.terminal_status,
            "missing_fields": list(self.missing_fields),
            "blockers": list(self.blockers),
            "can_execute": self.can_execute,
            "execution_rule": self.execution_rule,
        }


def reconstruct_attempts_per90(*, accurate_passes_per90: float, completion_rate: float) -> dict[str, Any]:
    """Research-only reconstruction. It is never a governed probability."""
    rate = float(completion_rate)
    if rate > 1.0:
        rate /= 100.0
    if not (0.0 < rate <= 1.0):
        raise ValueError("completion_rate must be in (0, 1] or (0, 100]")
    attempts = float(accurate_passes_per90) / rate
    return {
        "reconstructed_attempts_per90": attempts,
        "source_status": "RECONSTRUCTED",
        "evidence_type": "EVIDENCE_ONLY",
        "governed_probability": None,
        "maximum_ceiling_without_specialist": "RESEARCH_INTEREST",
        "can_execute": CAN_EXECUTE,
    }


_SOCCER_REQUIRED = (
    "player",
    "event_id",
    "event_date",
    "team",
    "opponent",
    "exact_line",
    "side",
    "settlement_rule",
    "starting_probability",
    "expected_minutes_distribution",
    "team_pass_attempt_distribution",
    "player_pass_share_distribution",
    "opponent_environment",
    "score_state_model",
    "substitution_model",
)


def validate_soccer_pass_attempts_inputs(payload: Mapping[str, Any]) -> ReadinessResult:
    missing = tuple(k for k in _SOCCER_REQUIRED if payload.get(k) in (None, "", [], {}))
    blockers: list[str] = []
    if _norm(payload.get("stat_type") or payload.get("prop_type")) not in {
        "PASSES ATTEMPTED", "PASS ATTEMPTS", "PLAYER PASSES ATTEMPTED"
    }:
        blockers.append("STAT_DEFINITION_MISMATCH")
    if payload.get("accurate_passes_per90") is not None and payload.get("specialist_probability_package") is None:
        blockers.append("PER90_RESEARCH_PROXY_NOT_MODEL_PROBABILITY")
    ready = not missing and "STAT_DEFINITION_MISMATCH" not in blockers
    return ReadinessResult(
        market_family=SOCCER_PASSES_ATTEMPTED,
        controlling_specialist="wow.soccer-passes-attempted-expert",
        model_input_ready=ready,
        terminal_status="MODEL_READY" if ready else "MODEL_INPUTS_INSUFFICIENT",
        missing_fields=missing,
        blockers=tuple(blockers),
    )


_NFL_SCORING_REQUIRED = (
    "passing_yards_points",
    "passing_td_points",
    "interception_points",
    "rushing_yards_points",
    "rushing_td_points",
    "receiving_yards_points",
    "reception_points",
    "receiving_td_points",
    "fumble_lost_points",
    "two_point_conversion_points",
)

_NFL_COMMON_REQUIRED = (
    "player",
    "event_id",
    "event_date",
    "team",
    "opponent",
    "position",
    "exact_line",
    "side",
    "settlement_rule",
    "scoring_profile",
    "team_play_distribution",
    "game_state_model",
    "player_opportunity_model",
)


def validate_nfl_dfs_inputs(payload: Mapping[str, Any]) -> ReadinessResult:
    missing = [k for k in _NFL_COMMON_REQUIRED if payload.get(k) in (None, "", [], {})]
    scoring = payload.get("scoring_profile")
    if isinstance(scoring, Mapping):
        missing.extend(f"scoring_profile.{k}" for k in _NFL_SCORING_REQUIRED if scoring.get(k) is None)
        if not scoring.get("scoring_profile_id"):
            missing.append("scoring_profile.scoring_profile_id")
    elif "scoring_profile" not in missing:
        missing.append("scoring_profile")

    blockers: list[str] = []
    if _norm(payload.get("prop_type") or payload.get("market_type")) not in {
        "FANTASY SCORE", "FANTASY POINTS", "DFS POINTS", "DFS FANTASY SCORE"
    }:
        blockers.append("STAT_DEFINITION_MISMATCH")
    if payload.get("external_dfs_projection") is not None:
        blockers.append("EXTERNAL_DFS_PROJECTION_EVIDENCE_ONLY")
    if payload.get("generic_ppr_projection") is not None:
        blockers.append("GENERIC_SCORING_PROXY_EVIDENCE_ONLY")

    ready = not missing and "STAT_DEFINITION_MISMATCH" not in blockers
    return ReadinessResult(
        market_family=NFL_DFS_FANTASY_SCORE,
        controlling_specialist=FANTASY_SPECIALISTS[NFL_DFS_FANTASY_SCORE],
        model_input_ready=ready,
        terminal_status="MODEL_READY" if ready else "MODEL_INPUTS_INSUFFICIENT",
        missing_fields=tuple(dict.fromkeys(missing)),
        blockers=tuple(blockers),
    )


_NON_NFL_COMMON_REQUIRED = (
    "player",
    "event_id",
    "event_date",
    "team",
    "opponent",
    "exact_line",
    "side",
    "settlement_rule",
    "scoring_profile",
)

_NON_NFL_CONTEXT_REQUIRED = {
    NBA_DFS_FANTASY_SCORE: ("minutes_model", "pace_model", "usage_model"),
    WNBA_DFS_FANTASY_SCORE: ("minutes_model", "pace_model", "usage_model"),
    MLB_HITTER_FANTASY_SCORE: ("plate_appearance_model", "lineup_role_model", "opponent_pitching_model"),
    MLB_PITCHER_FANTASY_SCORE: ("workload_model", "opponent_model", "bullpen_hook_model"),
}


def validate_non_nfl_fantasy_score_inputs(payload: Mapping[str, Any]) -> ReadinessResult:
    """Validate NBA/WNBA/MLB candidate inputs at the same pre-certification boundary as NFL."""
    market_family = classify_specialist(payload.get("sport"), payload.get("prop_type") or payload.get("market_type"))
    if market_family not in _NON_NFL_CONTEXT_REQUIRED:
        sport_n = _norm(payload.get("sport"))
        if sport_n in {"MLB", "BASEBALL", "MAJOR LEAGUE BASEBALL"}:
            blocker = "MLB_FANTASY_SCORE_ROLE_IDENTITY_UNRESOLVED"
        else:
            blocker = "STAT_DEFINITION_MISMATCH"
        return ReadinessResult(
            market_family=str(market_family or "FANTASY_SCORE_UNRESOLVED"),
            controlling_specialist="UNRESOLVED",
            model_input_ready=False,
            terminal_status="MODEL_INPUTS_INSUFFICIENT",
            missing_fields=(),
            blockers=(blocker,),
        )

    missing = [k for k in _NON_NFL_COMMON_REQUIRED if payload.get(k) in (None, "", [], {})]
    missing.extend(k for k in _NON_NFL_CONTEXT_REQUIRED[market_family] if payload.get(k) in (None, "", [], {}))

    scoring = payload.get("scoring_profile")
    if isinstance(scoring, Mapping):
        if not scoring.get("profile_id"):
            missing.append("scoring_profile.profile_id")
        if scoring.get("verified") is not True:
            missing.append("scoring_profile.verified")
        if not isinstance(scoring.get("weights"), Mapping) or not scoring.get("weights"):
            missing.append("scoring_profile.weights")
    elif "scoring_profile" not in missing:
        missing.append("scoring_profile")

    blockers: list[str] = []
    if payload.get("external_dfs_projection") is not None:
        blockers.append("EXTERNAL_DFS_PROJECTION_EVIDENCE_ONLY")
    if payload.get("generic_fantasy_projection") is not None:
        blockers.append("GENERIC_SCORING_PROXY_EVIDENCE_ONLY")

    ready = not missing
    return ReadinessResult(
        market_family=market_family,
        controlling_specialist=FANTASY_SPECIALISTS[market_family],
        model_input_ready=ready,
        terminal_status="MODEL_READY" if ready else "MODEL_INPUTS_INSUFFICIENT",
        missing_fields=tuple(dict.fromkeys(missing)),
        blockers=tuple(blockers),
    )


def score_nfl_fantasy_components(components: Mapping[str, float], scoring_profile: Mapping[str, float]) -> float:
    """Convert one simulated NFL outcome into the exact verified fantasy scoring profile."""
    missing = [k for k in _NFL_SCORING_REQUIRED if scoring_profile.get(k) is None]
    if missing:
        raise ValueError(f"incomplete scoring profile: {', '.join(missing)}")

    def c(name: str) -> float:
        return float(components.get(name, 0.0) or 0.0)

    s = scoring_profile
    score = (
        c("passing_yards") * float(s["passing_yards_points"])
        + c("passing_td") * float(s["passing_td_points"])
        + c("interceptions") * float(s["interception_points"])
        + c("rushing_yards") * float(s["rushing_yards_points"])
        + c("rushing_td") * float(s["rushing_td_points"])
        + c("receiving_yards") * float(s["receiving_yards_points"])
        + c("receptions") * float(s["reception_points"])
        + c("receiving_td") * float(s["receiving_td_points"])
        + c("fumbles_lost") * float(s["fumble_lost_points"])
        + c("two_point_conversions") * float(s["two_point_conversion_points"])
    )
    bonuses = s.get("bonuses")
    if isinstance(bonuses, Mapping):
        for component_name, table in bonuses.items():
            if not isinstance(table, Mapping):
                continue
            value = c(str(component_name))
            for threshold, points in table.items():
                if value >= float(threshold):
                    score += float(points)
    return float(score)


def research_proxy_can_publish_governed_probability(source_kind: str) -> bool:
    """Central regression guard used by tests and adapters."""
    return _norm(source_kind) not in {
        "ACCURATE PASSES PER90",
        "RECONSTRUCTED ATTEMPTS PER90",
        "RAW L10 HIT RATE",
        "EXTERNAL DFS PROJECTION",
        "GENERIC PPR PROJECTION",
        "GENERIC FANTASY PROJECTION",
        "SPORTSBOOK IMPLIED PROBABILITY",
    }
