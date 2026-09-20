"""Machine-readable V17 player-prop lane classification.

Declaration is not capability. Production publication still requires the exact
controlling specialist, a promoted/active fitted artifact, calibration/bounds,
valid current inputs, and terminal governance. Candidate/development/build-required
lanes remain non-publishable until the governed lifecycle promotes them.
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
WNBA_COMPOSITE_PROP_EXPERT = "wow.wnba-composite-prop-expert"
NFL_PLAYER_PROP_EXPERT = "wow.nfl-player-prop-probability-expert"
NFL_FANTASY_SCORE_EXPERT = "wow.nfl-dfs-fantasy-score-expert"
NBA_FANTASY_SCORE_EXPERT = "wow.nba-dfs-fantasy-score-expert"
WNBA_FANTASY_SCORE_EXPERT = "wow.wnba-dfs-fantasy-score-expert"
MLB_HITTER_FANTASY_SCORE_EXPERT = "wow.mlb-hitter-fantasy-score-expert"
MLB_PITCHER_FANTASY_SCORE_EXPERT = "wow.mlb-pitcher-fantasy-score-expert"

CERTIFIED_PRODUCTION = "CERTIFIED_PRODUCTION"
SUPPORTED_HOLD_ONLY = "SUPPORTED_HOLD_ONLY"
CANDIDATE_ONLY = "CANDIDATE_ONLY"
BUILD_REQUIRED = "BUILD_REQUIRED"
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
BASKETBALL_PRA = "PRA"
BASKETBALL_POINTS_REBOUNDS = "POINTS_REBOUNDS"
BASKETBALL_POINTS_ASSISTS = "POINTS_ASSISTS"
BASKETBALL_REBOUNDS_ASSISTS = "REBOUNDS_ASSISTS"
NFL_PASSING_YARDS = "PASSING_YARDS"
NFL_RUSHING_YARDS = "RUSHING_YARDS"
NFL_RECEIVING_YARDS = "RECEIVING_YARDS"
NFL_ANYTIME_TD = "ANYTIME_TD"
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


def _certified(sport: str, stat_type: str, specialist: str, notes: str) -> PropCapability:
    return PropCapability(
        sport=sport,
        stat_type=stat_type,
        lane_status=CERTIFIED_PRODUCTION,
        controlling_specialist=specialist,
        route_active=True,
        declared_skill_status="PRODUCTION",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=True,
        blocker=None,
        notes=notes,
    )


def _certified_mlb(stat_type: str, specialist: str, *, notes: str | None = None) -> PropCapability:
    row = _certified(
        "MLB",
        stat_type,
        specialist,
        notes or "Exact route still requires runtime artifact/input/calibration gates.",
    )
    return PropCapability(
        **{**row.as_dict(), "certified_line_support_source": "wow_prop_certified_model_artifact"}
    )


def _wnba_component_hold(stat_type: str) -> PropCapability:
    return PropCapability(
        sport="WNBA",
        stat_type=stat_type,
        lane_status=SUPPORTED_HOLD_ONLY,
        controlling_specialist=WNBA_PLAYER_PROP_EXPERT,
        route_active=True,
        declared_skill_status="PROSPECTIVE_CERTIFIED",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=False,
        blocker="WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE",
        notes=(
            "A promoted/active prospective WNBA component artifact exists, but the governed "
            "artifact registry still marks probability_publishable=false."
        ),
    )


def _wnba_composite_candidate(stat_type: str) -> PropCapability:
    return PropCapability(
        sport="WNBA",
        stat_type=stat_type,
        lane_status=CANDIDATE_ONLY,
        controlling_specialist=WNBA_COMPOSITE_PROP_EXPERT,
        route_active=False,
        declared_skill_status="RESEARCH_ONLY_FORWARD_TEST",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=False,
        blocker="WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING",
        notes=(
            "Joint P/R/A fitted-candidate infrastructure exists. Exact-line calibration, forward "
            "certification, governed promotion, production registration, and Action-canary proof remain required."
        ),
    )


def _fantasy_candidate(sport: str, stat_type: str, specialist: str, *, source: str) -> PropCapability:
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
            "Fitted candidate stage only: chronological split, joint residual simulation, 50k standard "
            "simulation floor, exact scoring profile, and failure-regime support exist."
        ),
    )


def _build_required(sport: str, stat_type: str, *, blocker: str = "PROP_FITTED_SPECIALIST_BUILD_REQUIRED", notes: str | None = None) -> PropCapability:
    return PropCapability(
        sport=sport,
        stat_type=stat_type,
        lane_status=BUILD_REQUIRED,
        controlling_specialist=None,
        route_active=False,
        declared_skill_status="MODEL_BUILD_REQUIRED",
        exact_line_support_policy=CONTINUOUS_LINE_SUPPORT,
        certified_line_support_source="wow_prop_fitted_model_artifacts",
        publication_allowed=False,
        blocker=blocker,
        notes=notes or (
            "This stat family is in the V17 cross-sport inventory but has no route-specific governed "
            "fitted specialist artifact/calibrator registered in production."
        ),
    )


DECLARED_PROP_LANES: dict[tuple[str, str], PropCapability] = {
    ("MLB", MLB_PITCHER_STRIKEOUTS): _certified_mlb(MLB_PITCHER_STRIKEOUTS, MLB_STRIKEOUT_EXPERT, notes="Certified failure-path negative-binomial pitcher strikeout route."),
    ("MLB", MLB_PITCHING_OUTS): _certified_mlb(MLB_PITCHING_OUTS, MLB_PITCHING_OUTS_EXPERT, notes="Certified pitcher workload/outs route."),
    ("MLB", MLB_STRIKES_THROWN): _certified_mlb(MLB_STRIKES_THROWN, MLB_PITCH_COMPOSITION_EXPERT, notes="Certified pitcher pitch-composition strikes-thrown route."),
    ("MLB", MLB_BALLS_THROWN): _certified_mlb(MLB_BALLS_THROWN, MLB_PITCH_COMPOSITION_EXPERT, notes="Certified pitcher pitch-composition balls-thrown route."),
    ("MLB", MLB_PLATE_APPEARANCES): _certified_mlb(MLB_PLATE_APPEARANCES, MLB_PLATE_APPEARANCES_EXPERT, notes="Certified batter plate-appearances route."),
    ("MLB", MLB_1IP_STAT_TYPE): PropCapability(
        sport="MLB", stat_type=MLB_1IP_STAT_TYPE, lane_status=SUPPORTED_HOLD_ONLY,
        controlling_specialist=MLB_FIRST_INNING_PITCH_COUNT_EXPERT, route_active=True,
        declared_skill_status=TEST_ONLY, exact_line_support_policy=EXACT_CERTIFIED_LINES_ONLY,
        certified_line_support_source="wow_prop_fitted_model_artifacts.validation_metrics.validated_lines",
        publication_allowed=False, blocker="MLB_1IP_PUBLICATION_HELD",
        notes="Exact fitted 1IP artifact is registry-ready but remains hold-only; unsupported lines REJECT_OOD.",
    ),
    ("WNBA", WNBA_POINTS): _wnba_component_hold(WNBA_POINTS),
    ("WNBA", WNBA_REBOUNDS): _wnba_component_hold(WNBA_REBOUNDS),
    ("WNBA", WNBA_ASSISTS): _wnba_component_hold(WNBA_ASSISTS),
    ("WNBA", WNBA_THREES_MADE): _wnba_component_hold(WNBA_THREES_MADE),
    ("WNBA", BASKETBALL_PRA): _wnba_composite_candidate(BASKETBALL_PRA),
    ("WNBA", BASKETBALL_POINTS_REBOUNDS): _wnba_composite_candidate(BASKETBALL_POINTS_REBOUNDS),
    ("WNBA", BASKETBALL_POINTS_ASSISTS): _wnba_composite_candidate(BASKETBALL_POINTS_ASSISTS),
    ("WNBA", BASKETBALL_REBOUNDS_ASSISTS): _wnba_composite_candidate(BASKETBALL_REBOUNDS_ASSISTS),

    # NFL direct-prop production routes from the governed NFL build.
    ("NFL", NFL_PASSING_YARDS): _certified("NFL", NFL_PASSING_YARDS, NFL_PLAYER_PROP_EXPERT, "Validated rolling fitted NFL passing-yards route; runtime artifact/input/calibration gates remain mandatory."),
    ("NFL", NFL_RUSHING_YARDS): _certified("NFL", NFL_RUSHING_YARDS, NFL_PLAYER_PROP_EXPERT, "Validated rolling fitted NFL rushing-yards route; runtime artifact/input/calibration gates remain mandatory."),
    ("NFL", NFL_RECEIVING_YARDS): _certified("NFL", NFL_RECEIVING_YARDS, NFL_PLAYER_PROP_EXPERT, "Validated rolling fitted NFL receiving-yards route; runtime artifact/input/calibration gates remain mandatory."),
    ("NFL", NFL_ANYTIME_TD): _certified("NFL", NFL_ANYTIME_TD, NFL_PLAYER_PROP_EXPERT, "Validated fitted NFL anytime-TD Bernoulli route; runtime artifact/input/calibration gates remain mandatory."),

    # Fitted research candidates: explicit, nonpublishable until exact-route lifecycle graduation.
    ("NFL", FANTASY_SCORE): _fantasy_candidate("NFL", FANTASY_SCORE, NFL_FANTASY_SCORE_EXPERT, source="services/nfl_dfs_fitted_simulator.py"),
    ("NBA", FANTASY_SCORE): _fantasy_candidate("NBA", FANTASY_SCORE, NBA_FANTASY_SCORE_EXPERT, source="services/fantasy_score_fitted_candidates.py"),
    ("WNBA", FANTASY_SCORE): _fantasy_candidate("WNBA", FANTASY_SCORE, WNBA_FANTASY_SCORE_EXPERT, source="services/fantasy_score_fitted_candidates.py"),
    ("MLB", MLB_HITTER_FANTASY_SCORE): _fantasy_candidate("MLB", MLB_HITTER_FANTASY_SCORE, MLB_HITTER_FANTASY_SCORE_EXPERT, source="services/fantasy_score_fitted_candidates.py"),
    ("MLB", MLB_PITCHER_FANTASY_SCORE): _fantasy_candidate("MLB", MLB_PITCHER_FANTASY_SCORE, MLB_PITCHER_FANTASY_SCORE_EXPERT, source="services/fantasy_score_fitted_candidates.py"),
}


_CROSS_SPORT_BUILD_TARGETS: dict[str, tuple[str, ...]] = {
    "NBA": ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE", BASKETBALL_PRA, BASKETBALL_POINTS_REBOUNDS, BASKETBALL_POINTS_ASSISTS, BASKETBALL_REBOUNDS_ASSISTS),
    "NFL": ("PASSING_YARDS", "PASSING_TOUCHDOWNS", "PASS_ATTEMPTS", "COMPLETIONS", "RUSHING_YARDS", "RUSH_ATTEMPTS", "RECEIVING_YARDS", "RECEPTIONS"),
    "NCAAF": ("PASSING_YARDS", "PASSING_TOUCHDOWNS", "PASS_ATTEMPTS", "COMPLETIONS", "RUSHING_YARDS", "RUSH_ATTEMPTS", "RECEIVING_YARDS", "RECEPTIONS"),
    "NCAAB": ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE", BASKETBALL_PRA),
    "NHL": ("GOALS", "ASSISTS", "POINTS", "SHOTS_ON_GOAL", "SAVES"),
    "SOCCER": ("SHOTS", "SHOTS_ON_TARGET", "GOALS", "ASSISTS", "SAVES"),
    "TENNIS": ("ACES", "DOUBLE_FAULTS", "GAMES_WON", "SETS_WON"),
    "GOLF": ("BIRDIES", "ROUND_SCORE", "GREENS_IN_REGULATION"),
    "MMA": ("SIGNIFICANT_STRIKES", "TAKEDOWNS", "FIGHT_TIME"),
    "BOXING": ("PUNCHES_LANDED", "FIGHT_TIME"),
}

for _sport, _stats in _CROSS_SPORT_BUILD_TARGETS.items():
    for _stat in _stats:
        if (_sport, _stat) == ("NHL", "SHOTS_ON_GOAL"):
            DECLARED_PROP_LANES.setdefault(
                (_sport, _stat),
                _build_required(
                    _sport,
                    _stat,
                    blocker="NHL_SOG_REAL_MULTI_SEASON_CORPUS_REQUIRED",
                    notes=(
                        "NHL SOG identity, ingestion, leakage-safe hydration, feature hardening, challenger fitting, "
                        "ablation and OOS-validation pipeline exist, but Phase 3 produced NO_PHASE4_CANDIDATE because "
                        "a real multi-season training corpus is not yet available."
                    ),
                ),
            )
        else:
            DECLARED_PROP_LANES.setdefault((_sport, _stat), _build_required(_sport, _stat))


SPORT_ALIASES = {
    "BASEBALL": "MLB", "BASEBALL_MLB": "MLB", "MAJOR LEAGUE BASEBALL": "MLB", "MAJOR_LEAGUE_BASEBALL": "MLB",
    "WOMENS_NBA": "WNBA", "WOMEN'S NBA": "WNBA", "WOMEN'S BASKETBALL": "WNBA",
    "COLLEGE FOOTBALL": "NCAAF", "CFB": "NCAAF", "COLLEGE BASKETBALL": "NCAAB", "CBB": "NCAAB",
    "ATP": "TENNIS", "WTA": "TENNIS", "UFC": "MMA", "MLS": "SOCCER", "EPL": "SOCCER",
}

STAT_ALIASES: dict[tuple[str, str], str] = {
    ("WNBA", "PTS"): WNBA_POINTS, ("WNBA", "POINT"): WNBA_POINTS,
    ("WNBA", "REB"): WNBA_REBOUNDS, ("WNBA", "REBOUND"): WNBA_REBOUNDS,
    ("WNBA", "AST"): WNBA_ASSISTS, ("WNBA", "ASSIST"): WNBA_ASSISTS,
    ("WNBA", "3PM"): WNBA_THREES_MADE, ("WNBA", "3PT_MADE"): WNBA_THREES_MADE,
    ("WNBA", "3_PT_MADE"): WNBA_THREES_MADE, ("WNBA", "THREES_MADE"): WNBA_THREES_MADE,
    ("WNBA", "THREE_POINTERS"): WNBA_THREES_MADE,
    ("WNBA", "PTS+REB+AST"): BASKETBALL_PRA, ("WNBA", "POINTS+REBOUNDS+ASSISTS"): BASKETBALL_PRA,
    ("WNBA", "POINTS_REBOUNDS_ASSISTS"): BASKETBALL_PRA, ("WNBA", "PTS_REB_AST"): BASKETBALL_PRA,
    ("WNBA", "PTS+REB"): BASKETBALL_POINTS_REBOUNDS, ("WNBA", "POINTS+REBOUNDS"): BASKETBALL_POINTS_REBOUNDS,
    ("WNBA", "PTS_REB"): BASKETBALL_POINTS_REBOUNDS,
    ("WNBA", "PTS+AST"): BASKETBALL_POINTS_ASSISTS, ("WNBA", "POINTS+ASSISTS"): BASKETBALL_POINTS_ASSISTS,
    ("WNBA", "PTS_AST"): BASKETBALL_POINTS_ASSISTS,
    ("WNBA", "REB+AST"): BASKETBALL_REBOUNDS_ASSISTS, ("WNBA", "REBOUNDS+ASSISTS"): BASKETBALL_REBOUNDS_ASSISTS,
    ("WNBA", "REB_AST"): BASKETBALL_REBOUNDS_ASSISTS,
    ("NFL", "PASS_YDS"): NFL_PASSING_YARDS, ("NFL", "PASSING_YDS"): NFL_PASSING_YARDS,
    ("NFL", "RUSH_YDS"): NFL_RUSHING_YARDS, ("NFL", "REC_YDS"): NFL_RECEIVING_YARDS,
    ("NFL", "ANYTIME_TOUCHDOWN"): NFL_ANYTIME_TD, ("NFL", "ANYTIME_TOUCHDOWN_SCORER"): NFL_ANYTIME_TD,
}

for _basketball_sport in ("NBA", "NCAAB"):
    STAT_ALIASES.update({
        (_basketball_sport, "PTS"): "POINTS", (_basketball_sport, "REB"): "REBOUNDS",
        (_basketball_sport, "AST"): "ASSISTS", (_basketball_sport, "3PM"): "THREE_POINTERS_MADE",
        (_basketball_sport, "PTS+REB+AST"): BASKETBALL_PRA,
        (_basketball_sport, "POINTS+REBOUNDS+ASSISTS"): BASKETBALL_PRA,
        (_basketball_sport, "POINTS_REBOUNDS_ASSISTS"): BASKETBALL_PRA,
        (_basketball_sport, "PTS+REB"): BASKETBALL_POINTS_REBOUNDS,
        (_basketball_sport, "PTS+AST"): BASKETBALL_POINTS_ASSISTS,
        (_basketball_sport, "REB+AST"): BASKETBALL_REBOUNDS_ASSISTS,
    })

for _football_sport in ("NFL", "NCAAF"):
    STAT_ALIASES.update({
        (_football_sport, "PASS_YDS"): "PASSING_YARDS", (_football_sport, "PASSING_YDS"): "PASSING_YARDS",
        (_football_sport, "PASS_TDS"): "PASSING_TOUCHDOWNS", (_football_sport, "PASS_TD"): "PASSING_TOUCHDOWNS",
        (_football_sport, "RUSH_YDS"): "RUSHING_YARDS", (_football_sport, "REC_YDS"): "RECEIVING_YARDS",
        (_football_sport, "REC"): "RECEPTIONS",
    })


def normalize_prop_sport(value: str) -> str:
    sport = str(value or "").strip().upper()
    return SPORT_ALIASES.get(sport, sport)


def normalize_prop_stat(sport: str, stat_type: str) -> str:
    normalized_sport = normalize_prop_sport(sport)
    raw = "_".join(str(stat_type or "").strip().upper().replace("-", " ").split())
    return STAT_ALIASES.get((normalized_sport, raw), raw)


def runtime_prop_stat_aliases() -> dict[tuple[str, str], str]:
    return dict(STAT_ALIASES)


def prop_capability(sport: str, stat_type: str) -> PropCapability:
    normalized_sport = normalize_prop_sport(sport)
    normalized_stat = normalize_prop_stat(normalized_sport, stat_type)
    declared = DECLARED_PROP_LANES.get((normalized_sport, normalized_stat))
    if declared is not None:
        return declared
    return PropCapability(
        sport=normalized_sport, stat_type=normalized_stat, lane_status=NOT_DECLARED,
        controlling_specialist=None, route_active=False, declared_skill_status=None,
        exact_line_support_policy=None, certified_line_support_source=None,
        publication_allowed=False, blocker="PROP_LANE_NOT_DECLARED",
        notes="Undeclared prop lane terminates against the route-specific model-capability contract.",
    )


def declared_prop_lane_manifest() -> dict[str, Any]:
    lanes = [capability.as_dict() for capability in DECLARED_PROP_LANES.values()]
    return {
        "manifest_version": "WOW_V17_PROP_LANE_MANIFEST_V5",
        "numerical_engine_scope": "SPORT_AGNOSTIC_BY_CERTIFIED_ADAPTER",
        "production_authority_is_route_specific": True,
        "candidate_presence_does_not_grant_probability_authority": True,
        "build_target_presence_does_not_grant_probability_authority": True,
        "unsupported_route_fallback_prohibited": True,
        "legacy_provisional_formulas_grant_v17_authority": False,
        "lanes": lanes,
        "declared_lane_count": len(lanes),
        "publication_allowed_lane_count": sum(1 for lane in lanes if lane["publication_allowed"]),
        "route_active_lane_count": sum(1 for lane in lanes if lane["route_active"]),
        "candidate_lane_count": sum(1 for lane in lanes if lane["lane_status"] == CANDIDATE_ONLY),
        "build_required_lane_count": sum(1 for lane in lanes if lane["lane_status"] == BUILD_REQUIRED),
        "sports_declared": sorted({lane["sport"] for lane in lanes}),
        "declaration_is_not_capability": True,
        "adjacent_line_substitution_permitted": False,
        "can_execute": False,
    }


__all__ = [
    "BASKETBALL_PRA", "BASKETBALL_POINTS_ASSISTS", "BASKETBALL_POINTS_REBOUNDS", "BASKETBALL_REBOUNDS_ASSISTS",
    "BUILD_REQUIRED", "CAN_EXECUTE", "CANDIDATE_ONLY", "CERTIFIED_PRODUCTION", "CONTINUOUS_LINE_SUPPORT",
    "DECLARED_PROP_LANES", "EXACT_CERTIFIED_LINES_ONLY", "FANTASY_SCORE",
    "MLB_1IP_STAT_TYPE", "MLB_BALLS_THROWN", "MLB_FIRST_INNING_PITCH_COUNT_EXPERT",
    "MLB_HITTER_FANTASY_SCORE", "MLB_HITTER_FANTASY_SCORE_EXPERT", "MLB_PITCHER_FANTASY_SCORE",
    "MLB_PITCHER_FANTASY_SCORE_EXPERT", "MLB_PITCHING_OUTS", "MLB_PITCHING_OUTS_EXPERT",
    "MLB_PITCHER_STRIKEOUTS", "MLB_PLATE_APPEARANCES", "MLB_PLATE_APPEARANCES_EXPERT",
    "MLB_PITCH_COMPOSITION_EXPERT", "MLB_STRIKES_THROWN", "MLB_STRIKEOUT_EXPERT",
    "NBA_FANTASY_SCORE_EXPERT", "NFL_ANYTIME_TD", "NFL_FANTASY_SCORE_EXPERT", "NFL_PASSING_YARDS",
    "NFL_PLAYER_PROP_EXPERT", "NFL_RECEIVING_YARDS", "NFL_RUSHING_YARDS", "NOT_DECLARED",
    "PUBLICATION_ALLOWED_LANES", "STAT_ALIASES", "SUPPORTED_HOLD_ONLY", "TEST_ONLY",
    "WNBA_ASSISTS", "WNBA_COMPOSITE_PROP_EXPERT", "WNBA_FANTASY_SCORE_EXPERT", "WNBA_PLAYER_PROP_EXPERT",
    "WNBA_POINTS", "WNBA_REBOUNDS", "WNBA_THREES_MADE", "PropCapability", "declared_prop_lane_manifest",
    "normalize_prop_sport", "normalize_prop_stat", "prop_capability", "runtime_prop_stat_aliases",
]
