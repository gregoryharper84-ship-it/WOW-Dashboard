from v17.prop_capability_manifest import (
    BASKETBALL_PRA,
    BUILD_REQUIRED,
    CANDIDATE_ONLY,
    CERTIFIED_PRODUCTION,
    FANTASY_SCORE,
    MLB_1IP_STAT_TYPE,
    MLB_HITTER_FANTASY_SCORE,
    MLB_PITCHER_FANTASY_SCORE,
    NOT_DECLARED,
    SUPPORTED_HOLD_ONLY,
    WNBA_COMPOSITE_PROP_EXPERT,
    WNBA_PLAYER_PROP_EXPERT,
    declared_prop_lane_manifest,
    normalize_prop_stat,
    prop_capability,
)


def test_certified_mlb_lanes_remain_production_capable():
    for stat in (
        "PITCHER_STRIKEOUTS",
        "PITCHING_OUTS",
        "STRIKES_THROWN",
        "BALLS_THROWN",
        "PLATE_APPEARANCES",
    ):
        lane = prop_capability("MLB", stat)
        assert lane.lane_status == CERTIFIED_PRODUCTION
        assert lane.route_active is True
        assert lane.publication_allowed is True
        assert lane.blocker is None
        assert lane.can_execute is False


def test_mlb_1ip_remains_hold_only_and_exact_line_governed():
    lane = prop_capability("MLB", MLB_1IP_STAT_TYPE)
    assert lane.lane_status == SUPPORTED_HOLD_ONLY
    assert lane.route_active is True
    assert lane.publication_allowed is False
    assert lane.blocker == "MLB_1IP_PUBLICATION_HELD"
    assert lane.can_execute is False


def test_wnba_component_artifacts_are_hold_only_until_publication_ratification():
    for stat in ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"):
        lane = prop_capability("WNBA", stat)
        assert lane.lane_status == SUPPORTED_HOLD_ONLY
        assert lane.controlling_specialist == WNBA_PLAYER_PROP_EXPERT
        assert lane.route_active is True
        assert lane.publication_allowed is False
        assert lane.blocker == "WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE"
        assert lane.can_execute is False


def test_wnba_pra_and_composites_are_declared_without_fake_probability_authority():
    for stat in ("PRA", "POINTS_REBOUNDS", "POINTS_ASSISTS", "REBOUNDS_ASSISTS"):
        lane = prop_capability("WNBA", stat)
        assert lane.lane_status == CANDIDATE_ONLY
        assert lane.controlling_specialist == WNBA_COMPOSITE_PROP_EXPERT
        assert lane.route_active is False
        assert lane.publication_allowed is False
        assert lane.blocker == "WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING"
        assert lane.can_execute is False


def test_wnba_pra_aliases_resolve_to_one_exact_governed_identity():
    for raw in ("PRA", "PTS+REB+AST", "POINTS+REBOUNDS+ASSISTS", "POINTS_REBOUNDS_ASSISTS"):
        assert normalize_prop_stat("WNBA", raw) == BASKETBALL_PRA
        lane = prop_capability("WNBA", raw)
        assert lane.stat_type == BASKETBALL_PRA
        assert lane.blocker == "WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING"


def test_fantasy_score_lanes_are_visible_without_granting_authority():
    for sport, stat in (
        ("NFL", FANTASY_SCORE),
        ("NBA", FANTASY_SCORE),
        ("WNBA", FANTASY_SCORE),
        ("MLB", MLB_HITTER_FANTASY_SCORE),
        ("MLB", MLB_PITCHER_FANTASY_SCORE),
    ):
        lane = prop_capability(sport, stat)
        assert lane.lane_status == CANDIDATE_ONLY
        assert lane.route_active is False
        assert lane.publication_allowed is False
        assert lane.blocker == "FANTASY_SCORE_CANDIDATE_NOT_PROMOTED"
        assert lane.can_execute is False


def test_certified_nfl_direct_prop_lanes_are_exact_and_route_specific():
    for stat in ("PASSING_YARDS", "RUSHING_YARDS", "RECEIVING_YARDS", "ANYTIME_TD"):
        lane = prop_capability("NFL", stat)
        assert lane.lane_status == CERTIFIED_PRODUCTION
        assert lane.route_active is True
        assert lane.publication_allowed is True
        assert lane.controlling_specialist == "wow.nfl-player-prop-probability-expert"
        assert lane.blocker is None
        assert lane.can_execute is False


def test_known_cross_sport_inventory_reports_model_build_required_not_undeclared():
    targets = (
        ("NBA", "POINTS"),
        ("NCAAF", "RUSHING_YARDS"),
        ("NCAAB", "PRA"),
        ("NHL", "POINTS"),
        ("SOCCER", "SHOTS_ON_TARGET"),
        ("TENNIS", "ACES"),
        ("GOLF", "BIRDIES"),
        ("MMA", "SIGNIFICANT_STRIKES"),
        ("BOXING", "PUNCHES_LANDED"),
    )
    for sport, stat in targets:
        lane = prop_capability(sport, stat)
        assert lane.lane_status == BUILD_REQUIRED
        assert lane.publication_allowed is False
        assert lane.blocker == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED"
        assert lane.can_execute is False


def test_truly_unknown_lane_fails_closed_without_guessing():
    lane = prop_capability("LACROSSE", "GROUND_BALLS")
    assert lane.lane_status == NOT_DECLARED
    assert lane.route_active is False
    assert lane.publication_allowed is False
    assert lane.controlling_specialist is None
    assert lane.blocker == "PROP_LANE_NOT_DECLARED"
    assert lane.can_execute is False


def test_manifest_reports_cross_sport_build_state_without_claiming_universal_model_support():
    manifest = declared_prop_lane_manifest()
    lanes = manifest["lanes"]
    advertised = {(lane["sport"], lane["stat_type"]) for lane in lanes}

    assert ("MLB", "PITCHER_STRIKEOUTS") in advertised
    assert ("MLB", MLB_1IP_STAT_TYPE) in advertised
    assert ("WNBA", "POINTS") in advertised
    assert ("WNBA", BASKETBALL_PRA) in advertised
    assert ("NFL", "PASSING_YARDS") in advertised
    assert ("NFL", "ANYTIME_TD") in advertised
    assert ("NFL", FANTASY_SCORE) in advertised
    assert ("NBA", FANTASY_SCORE) in advertised
    assert ("NBA", "POINTS") in advertised
    assert ("NCAAF", "RUSHING_YARDS") in advertised
    assert ("NCAAB", "PRA") in advertised
    assert ("NHL", "SHOTS_ON_GOAL") in advertised
    assert ("SOCCER", "SHOTS_ON_TARGET") in advertised
    assert ("TENNIS", "ACES") in advertised
    assert ("GOLF", "BIRDIES") in advertised
    assert ("MMA", "SIGNIFICANT_STRIKES") in advertised
    assert ("BOXING", "PUNCHES_LANDED") in advertised

    assert manifest["manifest_version"] == "WOW_V17_PROP_LANE_MANIFEST_V5"
    assert manifest["numerical_engine_scope"] == "SPORT_AGNOSTIC_BY_CERTIFIED_ADAPTER"
    assert manifest["production_authority_is_route_specific"] is True
    assert manifest["candidate_presence_does_not_grant_probability_authority"] is True
    assert manifest["build_target_presence_does_not_grant_probability_authority"] is True
    assert manifest["legacy_provisional_formulas_grant_v17_authority"] is False
    assert manifest["unsupported_route_fallback_prohibited"] is True
    assert manifest["declared_lane_count"] == len(lanes)
    assert manifest["route_active_lane_count"] == sum(1 for lane in lanes if lane["route_active"])
    assert manifest["publication_allowed_lane_count"] == sum(1 for lane in lanes if lane["publication_allowed"])
    assert manifest["candidate_lane_count"] == sum(1 for lane in lanes if lane["lane_status"] == CANDIDATE_ONLY)
    assert manifest["build_required_lane_count"] == sum(1 for lane in lanes if lane["lane_status"] == BUILD_REQUIRED)
    assert manifest["sports_declared"] == [
        "BOXING", "GOLF", "MLB", "MMA", "NBA", "NCAAB",
        "NCAAF", "NFL", "NHL", "SOCCER", "TENNIS", "WNBA",
    ]
    assert manifest["declaration_is_not_capability"] is True
    assert manifest["adjacent_line_substitution_permitted"] is False
    assert manifest["can_execute"] is False


def test_sport_aliases_resolve_without_changing_authority():
    assert prop_capability("baseball", MLB_1IP_STAT_TYPE).lane_status == SUPPORTED_HOLD_ONLY
    assert prop_capability("womens_nba", "POINTS").lane_status == SUPPORTED_HOLD_ONLY
    assert prop_capability("CFB", "PASS_YDS").lane_status == BUILD_REQUIRED
