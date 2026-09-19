"""Machine-readable V17 player-prop lane classification.

Declaration is not capability. Production publication still requires the exact
controlling specialist, a promoted/active fitted artifact, calibration/bounds,
valid current inputs, and terminal governance. Candidate/development lanes are
visible so cross-sport build state is auditable, but remain non-publishable until
the governed lifecycle promotes them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CAN_EXECUTE = False

MLB_STRIKEOUT_EXPERT = "wow.mlb-pitcher-failure-path-expert"
MLB_FIRST_INNING_PITCH_COUNT_EXPERT = "wow.mlb-first-inning-pitch-count-expert"
MLB_PITCHING_OUTS_EXPERT = "wow.mlb-pitcher-outs-workload-expert"
MLB_PITCH_COMPOSITION_EXPERT = "wow.mlb-pitcher-pitch-composition-expert"
MLB_PLATE_APPEARANCES_EXPERT = "wow.mlb-batter-plate-appearances-expert"
WNBA_PLAYER_PROP_EXPERT = "wow.wnba-player-prop-probability-expert"
NFL_FANTASY_SCORE_EXPERT = "wow.nfl-dfs-fantasy-score-expert"
NBA_FANTASY_SCORE_EXPERT = "wow.nba-dfs-fantasy-score-expert"
WNBA_FANTASY_SCORE_EXPERT = "wow.wnba-dfs-fantasy-score-expert"
MLB_HITTER_FANTASY_SCORE_EXPERT = "wow.mlb-hitter-fantasy-score-expert"
MLB_PITCHER_FANTASY_SCORE_EXPERT = "wow.mlb-pitcher-fantasy-score-expert"

CERTIFIED_PRODUCTION = "CERTIFIED_PRODUCTION"
SUPPORTED_HOLD_ONLY = "SUPPORTED_HOLD_ONLY"
CANDIDATE_ONLY = "CANDIDATE_ONLY"
TEST_ONLY = "TEST_ONLY"
NOT_DECLARED = "NOT_DECLARED"

PUBLICATION_ALLOWED_LANES = frozenset({CERTIFIED_PRODUCTION})
EXACT_CERTIFIED_LINES_ONLY = "EXACT_CERTIFIED_LINES_ONLY_REJECT_OOD"
CONTINUOUS_LINE_SUPPORT = "CONTINUOUS_LINE_SUPPORT"

MLB_PITCHER_STRIKEOUTS = "PITCHER_STRIKEOUTS"
MLB_1IP_STAT_TYPE = "1ST_INNING_PITCHES_THROWN"
MLB_PITCHING_OUTS = "PITCHING_OUTS"
MLB_STRIKES_THROWN = "STRIKES_THROWN"
MLB_BALLS_THROWN = "BALLS_THROWN"
MLB_PLATE_APPEARANCES = "PLATE_APPEARANCES"
WNBA_POINTS = "POINTS"
WNBA_REBOUNDS = "REBOUNDS"
WNBA_ASSISTS = "ASSISTS"
WNBA_THREES_MADE = "THREE_POINTERS_MADE"
FANTASY_SCORE = "FANTASY_SCORE"
MLB_HITTER_FANTASY_SCORE = "HITTER_FANTASY_SCORE"
MLB_PITCHER_FANTASY_SCORE = "PITCHER_FANTASY_SCORE"


@dataclass(frozen=True)
class PropCapability:
    sport: str
    stat_type: str
    lane_status: str
    controlling_specialist: str | None
    route_active: bool
    declared_skill_status: str | None
    exact_line_support_policy: str | None
    certified_line_support_source: str | None
    publication_allowed: bool
    blocker: str | None
    notes: str | None = None
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "stat_type": self.stat_type,
            "lane_status": self.lane_status,
            "controlling_specialist": self.controlling_specialist,
            "route_active": self.route_active,
            "declared_skill_status": self.declared_skill_status,
            "exact_line_support_policy": self.exact_line_support_policy,
            "certified_line_support_source": self.certified_line_support_source,
            "publication_allowed": self.publication_allowed,
            "blocker": self.blocker,
            "notes": self.notes,
            "can_execute": False,
        }


def _certified_mlb(stat_type: str, specialist: str, *, notes: str | None = None) -> PropCapability:
    return PropCapability(
        sport="MLB",
        stat_type=stat_type,
        lane_status=CERTIFIED_PRODUCTION,
        controlling_specialist=specialist,
        route_active=True,
        declared_skill_status="PRODUCTION",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source="wow_prop_certified_model_artifact",
        publication_allowed=True,
        blocker=None,
        notes=notes or "Exact route still requires runtime artifact/input/calibration gates.",
    )


def _wnba_candidate(stat_type: str) -> PropCapability:
    return PropCapability(
        sport="WNBA",
        stat_type=stat_type,
        lane_status=CANDIDATE_ONLY,
        controlling_specialist=WNBA_PLAYER_PROP_EXPERT,
        route_active=False,
        declared_skill_status="CANDIDATE",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=False,
        blocker="WNBA_PROP_CANDIDATE_NOT_PROMOTED",
        notes=(
            "Fitted WNBA candidate/trainer, model adapter, calibration adapter, and hydration provider exist. "
            "The lane remains non-publishable until governed registration, lifecycle review, certification, "
            "promotion, and exact runtime artifact readiness pass."
        ),
    )


def _fantasy_candidate(
    sport: str,
    stat_type: str,
    specialist: str,
    *,
    source: str,
) -> PropCapability:
    return PropCapability(
        sport=sport,
        stat_type=stat_type,
        lane_status=CANDIDATE_ONLY,
        controlling_specialist=specialist,
        route_active=False,
        declared_skill_status="CANDIDATE",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source=source,
        publication_allowed=False,
        blocker="FANTASY_SCORE_CANDIDATE_NOT_PROMOTED",
        notes=(
            "NFL-parity fitted candidate stage only: whole-event chronological split, joint residual simulation, "
            "50k standard simulation floor, exact verified scoring profile, and failure-regime support exist. "
            "Exact-line calibration, certification, promotion, and production registration are still required."
        ),
    )


DECLARED_PROP_LANES: dict[tuple[str, str], PropCapability] = {
    ("MLB", MLB_PITCHER_STRIKEOUTS): _certified_mlb(
        MLB_PITCHER_STRIKEOUTS, MLB_STRIKEOUT_EXPERT,
        notes="Certified failure-path negative-binomial pitcher strikeout route.",
    ),
    ("MLB", MLB_PITCHING_OUTS): _certified_mlb(
        MLB_PITCHING_OUTS, MLB_PITCHING_OUTS_EXPERT,
        notes="Certified pitcher workload/outs route.",
    ),
    ("MLB", MLB_STRIKES_THROWN): _certified_mlb(
        MLB_STRIKES_THROWN, MLB_PITCH_COMPOSITION_EXPERT,
        notes="Certified pitcher pitch-composition strikes-thrown route.",
    ),
    ("MLB", MLB_BALLS_THROWN): _certified_mlb(
        MLB_BALLS_THROWN, MLB_PITCH_COMPOSITION_EXPERT,
        notes="Certified pitcher pitch-composition balls-thrown route.",
    ),
    ("MLB", MLB_PLATE_APPEARANCES): _certified_mlb(
        MLB_PLATE_APPEARANCES, MLB_PLATE_APPEARANCES_EXPERT,
        notes="Certified batter plate-appearances route.",
    ),
    ("MLB", MLB_1IP_STAT_TYPE): PropCapability(
        sport="MLB",
        stat_type=MLB_1IP_STAT_TYPE,
        lane_status=SUPPORTED_HOLD_ONLY,
        controlling_specialist=MLB_FIRST_INNING_PITCH_COUNT_EXPERT,
        route_active=True,
        declared_skill_status=TEST_ONLY,
        exact_line_support_policy=EXACT_CERTIFIED_LINES_ONLY,
        certified_line_support_source="wow_prop_fitted_model_artifacts.validation_metrics.validated_lines",
        publication_allowed=False,
        blocker="MLB_1IP_PUBLICATION_HELD",
        notes=(
            "The exact fitted artifact is registry-ready, but this lane remains hold-only under its separate "
            "serving/publication contract. Unsupported exact lines terminate REJECT_OOD; adjacent-line "
            "substitution is prohibited."
        ),
    ),
    ("WNBA", WNBA_POINTS): _wnba_candidate(WNBA_POINTS),
    ("WNBA", WNBA_REBOUNDS): _wnba_candidate(WNBA_REBOUNDS),
    ("WNBA", WNBA_ASSISTS): _wnba_candidate(WNBA_ASSISTS),
    ("WNBA", WNBA_THREES_MADE): _wnba_candidate(WNBA_THREES_MADE),

    # Fantasy Score candidate parity. These declarations do not activate publication authority.
    ("NFL", FANTASY_SCORE): _fantasy_candidate(
        "NFL", FANTASY_SCORE, NFL_FANTASY_SCORE_EXPERT,
        source="services/nfl_dfs_fitted_simulator.py",
    ),
    ("NBA", FANTASY_SCORE): _fantasy_candidate(
        "NBA", FANTASY_SCORE, NBA_FANTASY_SCORE_EXPERT,
        source="services/fantasy_score_fitted_candidates.py",
    ),
    ("WNBA", FANTASY_SCORE): _fantasy_candidate(
        "WNBA", FANTASY_SCORE, WNBA_FANTASY_SCORE_EXPERT,
        source="services/fantasy_score_fitted_candidates.py",
    ),
    ("MLB", MLB_HITTER_FANTASY_SCORE): _fantasy_candidate(
        "MLB", MLB_HITTER_FANTASY_SCORE, MLB_HITTER_FANTASY_SCORE_EXPERT,
        source="services/fantasy_score_fitted_candidates.py",
    ),
    ("MLB", MLB_PITCHER_FANTASY_SCORE): _fantasy_candidate(
        "MLB", MLB_PITCHER_FANTASY_SCORE, MLB_PITCHER_FANTASY_SCORE_EXPERT,
        source="services/fantasy_score_fitted_candidates.py",
    ),
}


def normalize_prop_sport(value: str) -> str:
    sport = str(value or "").strip().upper()
    aliases = {
        "BASEBALL": "MLB",
        "BASEBALL_MLB": "MLB",
        "MAJOR LEAGUE BASEBALL": "MLB",
        "MAJOR_LEAGUE_BASEBALL": "MLB",
        "WOMENS_NBA": "WNBA",
        "WOMEN'S NBA": "WNBA",
    }
    return aliases.get(sport, sport)


def prop_capability(sport: str, stat_type: str) -> PropCapability:
    """Classify one prop lane. An undeclared lane fails closed, never guesses."""
    normalized_sport = normalize_prop_sport(sport)
    normalized_stat = str(stat_type or "").strip().upper()
    declared = DECLARED_PROP_LANES.get((normalized_sport, normalized_stat))
    if declared is not None:
        return declared
    return PropCapability(
        sport=normalized_sport,
        stat_type=normalized_stat,
        lane_status=NOT_DECLARED,
        controlling_specialist=None,
        route_active=False,
        declared_skill_status=None,
        exact_line_support_policy=None,
        certified_line_support_source=None,
        publication_allowed=False,
        blocker="PROP_LANE_NOT_DECLARED",
        notes="Undeclared prop lane terminates against the route-specific model-capability contract.",
    )


def declared_prop_lane_manifest() -> dict[str, Any]:
    """Advertise production, hold-only, and candidate routes without conflating them."""
    lanes = [capability.as_dict() for capability in DECLARED_PROP_LANES.values()]
    return {
        "manifest_version": "WOW_V17_PROP_LANE_MANIFEST_V3",
        "numerical_engine_scope": "SPORT_AGNOSTIC_BY_CERTIFIED_ADAPTER",
        "production_authority_is_route_specific": True,
        "candidate_presence_does_not_grant_probability_authority": True,
        "unsupported_route_fallback_prohibited": True,
        "lanes": lanes,
        "declared_lane_count": len(lanes),
        "publication_allowed_lane_count": sum(1 for lane in lanes if lane["publication_allowed"]),
        "route_active_lane_count": sum(1 for lane in lanes if lane["route_active"]),
        "candidate_lane_count": sum(1 for lane in lanes if lane["lane_status"] == CANDIDATE_ONLY),
        "sports_declared": sorted({lane["sport"] for lane in lanes}),
        "declaration_is_not_capability": True,
        "adjacent_line_substitution_permitted": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CANDIDATE_ONLY",
    "CERTIFIED_PRODUCTION",
    "CONTINUOUS_LINE_SUPPORT",
    "DECLARED_PROP_LANES",
    "EXACT_CERTIFIED_LINES_ONLY",
    "FANTASY_SCORE",
    "MLB_1IP_STAT_TYPE",
    "MLB_BALLS_THROWN",
    "MLB_FIRST_INNING_PITCH_COUNT_EXPERT",
    "MLB_HITTER_FANTASY_SCORE",
    "MLB_HITTER_FANTASY_SCORE_EXPERT",
    "MLB_PITCHER_FANTASY_SCORE",
    "MLB_PITCHER_FANTASY_SCORE_EXPERT",
    "MLB_PITCHING_OUTS",
    "MLB_PITCHING_OUTS_EXPERT",
    "MLB_PITCHER_STRIKEOUTS",
    "MLB_PLATE_APPEARANCES",
    "MLB_PLATE_APPEARANCES_EXPERT",
    "MLB_PITCH_COMPOSITION_EXPERT",
    "MLB_STRIKES_THROWN",
    "MLB_STRIKEOUT_EXPERT",
    "NBA_FANTASY_SCORE_EXPERT",
    "NFL_FANTASY_SCORE_EXPERT",
    "NOT_DECLARED",
    "PUBLICATION_ALLOWED_LANES",
    "SUPPORTED_HOLD_ONLY",
    "TEST_ONLY",
    "WNBA_ASSISTS",
    "WNBA_FANTASY_SCORE_EXPERT",
    "WNBA_PLAYER_PROP_EXPERT",
    "WNBA_POINTS",
    "WNBA_REBOUNDS",
    "WNBA_THREES_MADE",
    "PropCapability",
    "declared_prop_lane_manifest",
    "normalize_prop_sport",
    "prop_capability",
]
