"""Every governed prop route must be advertised with its true authority."""
from v17.prop_capability_manifest import (
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
    WNBA_PLAYER_PROP_EXPERT,
    declared_prop_lane_manifest,
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


def test_wnba_fitted_candidates_are_visible_but_never_promoted_by_manifest():
    for stat in ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"):
        capability = prop_capability("WNBA", stat)
        assert capability.lane_status == CANDIDATE_ONLY
        assert capability.controlling_specialist == WNBA_PLAYER_PROP_EXPERT
        assert capability.route_active is False
        assert capability.publication_allowed is False
        assert capability.blocker == "WNBA_PROP_CANDIDATE_NOT_PROMOTED"
        assert capability.can_execute is False


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


def test_undeclared_lane_still_fails_closed_against_exact_route_contract():
    capability = prop_capability("NBA", "POINTS")
    assert capability.lane_status == NOT_DECLARED
    assert capability.publication_allowed is False
    assert capability.blocker == "PROP_LANE_NOT_DECLARED"


def test_manifest_reports_sport_agnostic_engine_without_claiming_universal_model_support():
    manifest = declared_prop_lane_manifest()
    advertised = {(lane["sport"], lane["stat_type"]) for lane in manifest["lanes"]}

    assert ("MLB", "PITCHER_STRIKEOUTS") in advertised
    assert ("MLB", "PITCHING_OUTS") in advertised
    assert ("MLB", "STRIKES_THROWN") in advertised
    assert ("MLB", "BALLS_THROWN") in advertised
    assert ("MLB", "PLATE_APPEARANCES") in advertised
    assert ("MLB", MLB_1IP_STAT_TYPE) in advertised
    assert ("WNBA", "POINTS") in advertised
    assert ("WNBA", "REBOUNDS") in advertised
    assert ("WNBA", "ASSISTS") in advertised
    assert ("WNBA", "THREE_POINTERS_MADE") in advertised
    assert ("NFL", FANTASY_SCORE) in advertised
    assert ("NBA", FANTASY_SCORE) in advertised
    assert ("WNBA", FANTASY_SCORE) in advertised
    assert ("MLB", MLB_HITTER_FANTASY_SCORE) in advertised
    assert ("MLB", MLB_PITCHER_FANTASY_SCORE) in advertised

    assert manifest["manifest_version"] == "WOW_V17_PROP_LANE_MANIFEST_V3"
    assert manifest["numerical_engine_scope"] == "SPORT_AGNOSTIC_BY_CERTIFIED_ADAPTER"
    assert manifest["production_authority_is_route_specific"] is True
    assert manifest["candidate_presence_does_not_grant_probability_authority"] is True
    assert manifest["unsupported_route_fallback_prohibited"] is True
    assert manifest["declared_lane_count"] == 15
    assert manifest["route_active_lane_count"] == 6
    assert manifest["publication_allowed_lane_count"] == 5
    assert manifest["candidate_lane_count"] == 9
    assert manifest["sports_declared"] == ["MLB", "NBA", "NFL", "WNBA"]
    assert manifest["declaration_is_not_capability"] is True
    assert manifest["adjacent_line_substitution_permitted"] is False
    assert manifest["can_execute"] is False


def test_sport_aliases_resolve_without_changing_authority():
    assert prop_capability("baseball", MLB_1IP_STAT_TYPE).lane_status == SUPPORTED_HOLD_ONLY
    assert prop_capability("womens_nba", "POINTS").lane_status == CANDIDATE_ONLY
