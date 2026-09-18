"""Every governed prop route must be advertised with its true authority."""
from v17.prop_capability_manifest import (
    BASKETBALL_PRA,
    BUILD_REQUIRED,
    CANDIDATE_ONLY,
    CERTIFIED_PRODUCTION,
    EXACT_CERTIFIED_LINES_ONLY,
    FANTASY_SCORE,
    MLB_1IP_STAT_TYPE,
    MLB_HITTER_FANTASY_SCORE,
    MLB_PITCHER_FANTASY_SCORE,
    NOT_DECLARED,
    SUPPORTED_HOLD_ONLY,
    TEST_ONLY,
    WNBA_COMPOSITE_PROP_EXPERT,
    WNBA_PLAYER_PROP_EXPERT,
    declared_prop_lane_manifest,
    normalize_prop_stat,
    prop_capability,
)


def test_all_certified_mlb_fitted_routes_are_advertised_as_production():
    for stat in (
        "PITCHER_STRIKEOUTS",
        "PITCHING_OUTS",
        "STRIKES_THROWN",
        "BALLS_THROWN",
        "PLATE_APPEARANCES",
    ):
        capability = prop_capability("MLB", stat)
        assert capability.lane_status == CERTIFIED_PRODUCTION
        assert capability.route_active is True
        assert capability.publication_allowed is True
        assert capability.controlling_specialist not in {None, "MODEL_UNAVAILABLE"}
        assert capability.can_execute is False


def test_mlb_1ip_remains_explicit_hold_only_with_exact_line_contract():
    capability = prop_capability("MLB", MLB_1IP_STAT_TYPE)
    assert capability.lane_status == SUPPORTED_HOLD_ONLY
    assert capability.route_active is True
    assert capability.declared_skill_status == TEST_ONLY
    assert capability.publication_allowed is False
    assert capability.blocker == "MLB_1IP_PUBLICATION_HELD"
    assert capability.exact_line_support_policy == EXACT_CERTIFIED_LINES_ONLY
    assert capability.certified_line_support_source.endswith("validated_lines")


def test_wnba_component_artifacts_are_hold_only_until_publication_ratification():
    for stat in ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"):
        capability = prop_capability("WNBA", stat)
        assert capability.lane_status == SUPPORTED_HOLD_ONLY
        assert capability.controlling_specialist == WNBA_PLAYER_PROP_EXPERT
        assert capability.route_active is True
        assert capability.publication_allowed is False
        assert capability.blocker == "WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE"
        assert capability.can_execute is False


def test_wnba_pra_and_composites_are_declared_without_fake_probability_authority():
    for stat in (
        "PRA",
        "POINTS_REBOUNDS",
        "POINTS_ASSISTS",
        "REBOUNDS_ASSISTS",
    ):
        capability = prop_capability("WNBA", stat)
        assert capability.lane_status == CANDIDATE_ONLY
        assert capability.controlling_specialist == WNBA_COMPOSITE_PROP_EXPERT
        assert capability.route_active is False
        assert capability.publication_allowed is False
        assert capability.blocker == "WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING"
        assert capability.can_execute is False


def test_wnba_pra_aliases_resolve_to_one_exact_governed_identity():
    for raw in ("PRA", "PTS+REB+AST", "POINTS+REBOUNDS+ASSISTS", "POINTS_REBOUNDS_ASSISTS"):
        assert normalize_prop_stat("WNBA", raw) == BASKETBALL_PRA
        capability = prop_capability("WNBA", raw)
        assert capability.stat_type == BASKETBALL_PRA
        assert capability.blocker == "WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING"


def test_fantasy_score_candidate_parity_is_visible_without_promotion():
    for sport, stat in (
        ("NFL", FANTASY_SCORE),
        ("NBA", FANTASY_SCORE),
        ("WNBA", FANTASY_SCORE),
        ("MLB", MLB_HITTER_FANTASY_SCORE),
        ("MLB", MLB_PITCHER_FANTASY_SCORE),
    ):
        capability = prop_capability(sport, stat)
        assert capability.lane_status == CANDIDATE_ONLY
        assert capability.route_active is False
        assert capability.declared_skill_status == "CANDIDATE"
        assert capability.publication_allowed is False
        assert capability.blocker == "FANTASY_SCORE_CANDIDATE_NOT_PROMOTED"
        assert capability.controlling_specialist
        assert capability.can_execute is False


def test_known_cross_sport_inventory_reports_model_build_required_not_undeclared():
    targets = (
        ("NBA", "POINTS"),
        ("NFL", "PASSING_YARDS"),
        ("NCAAF", "RUSHING_YARDS"),
        ("NCAAB", "PRA"),
        ("NHL", "SHOTS_ON_GOAL"),
        ("SOCCER", "SHOTS_ON_TARGET"),
        ("TENNIS", "ACES"),
        ("GOLF", "BIRDIES"),
        ("MMA", "SIGNIFICANT_STRIKES"),
        ("BOXING", "PUNCHES_LANDED"),
    )
    for sport, stat in targets:
        capability = prop_capability(sport, stat)
        assert capability.lane_status == BUILD_REQUIRED
        assert capability.publication_allowed is False
        assert capability.blocker == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED"
        assert capability.can_execute is False


def test_legacy_sport_aliases_normalize_to_same_build_target_without_granting_support():
    assert prop_capability("CFB", "PASS_YDS").sport == "NCAAF"
    assert prop_capability("CFB", "PASS_YDS").stat_type == "PASSING_YARDS"
    assert prop_capability("CBB", "PTS").sport == "NCAAB"
    assert prop_capability("UFC", "SIGNIFICANT_STRIKES").sport == "MMA"
    assert prop_capability("MLS", "SHOTS_ON_TARGET").sport == "SOCCER"
    assert prop_capability("ATP", "ACES").sport == "TENNIS"


def test_truly_unknown_lane_still_fails_closed_against_exact_route_contract():
    capability = prop_capability("LACROSSE", "GROUND_BALLS")
    assert capability.lane_status == NOT_DECLARED
    assert capability.publication_allowed is False
    assert capability.blocker == "PROP_LANE_NOT_DECLARED"


def test_manifest_reports_all_sports_build_state_without_claiming_universal_model_support():
    manifest = declared_prop_lane_manifest()
    advertised = {(lane["sport"], lane["stat_type"]) for lane in manifest["lanes"]}

    assert ("MLB", "PITCHER_STRIKEOUTS") in advertised
    assert ("MLB", MLB_1IP_STAT_TYPE) in advertised
    assert ("WNBA", "POINTS") in advertised
    assert ("WNBA", BASKETBALL_PRA) in advertised
    assert ("NFL", FANTASY_SCORE) in advertised
    assert ("NBA", FANTASY_SCORE) in advertised
    assert ("NBA", "POINTS") in advertised
    assert ("NFL", "PASSING_YARDS") in advertised
    assert ("NCAAF", "RUSHING_YARDS") in advertised
    assert ("NCAAB", "PRA") in advertised
    assert ("NHL", "SHOTS_ON_GOAL") in advertised
    assert ("SOCCER", "SHOTS_ON_TARGET") in advertised
    assert ("TENNIS", "ACES") in advertised
    assert ("GOLF", "BIRDIES") in advertised
    assert ("MMA", "SIGNIFICANT_STRIKES") in advertised
    assert ("BOXING", "PUNCHES_LANDED") in advertised

    assert manifest["manifest_version"] == "WOW_V17_PROP_LANE_MANIFEST_V4"
    assert manifest["numerical_engine_scope"] == "SPORT_AGNOSTIC_BY_CERTIFIED_ADAPTER"
    assert manifest["production_authority_is_route_specific"] is True
    assert manifest["candidate_presence_does_not_grant_probability_authority"] is True
    assert manifest["build_target_presence_does_not_grant_probability_authority"] is True
    assert manifest["legacy_provisional_formulas_grant_v17_authority"] is False
    assert manifest["unsupported_route_fallback_prohibited"] is True
    assert manifest["declared_lane_count"] == 70
    assert manifest["route_active_lane_count"] == 10
    assert manifest["publication_allowed_lane_count"] == 5
    assert manifest["candidate_lane_count"] == 9
    assert manifest["build_required_lane_count"] == 51
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
