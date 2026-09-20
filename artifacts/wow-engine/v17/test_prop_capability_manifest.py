from v17.prop_capability_manifest import (
    CANDIDATE_ONLY,
    CERTIFIED_PRODUCTION,
    FANTASY_SCORE,
    MLB_1IP_STAT_TYPE,
    MLB_HITTER_FANTASY_SCORE,
    MLB_PITCHER_FANTASY_SCORE,
    NOT_DECLARED,
    SUPPORTED_HOLD_ONLY,
    declared_prop_lane_manifest,
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


def test_wnba_counting_prop_declarations_remain_candidate_only():
    for stat in ("POINTS", "REBOUNDS", "ASSISTS", "THREE_POINTERS_MADE"):
        lane = prop_capability("WNBA", stat)
        assert lane.lane_status == CANDIDATE_ONLY
        assert lane.route_active is False
        assert lane.publication_allowed is False
        assert lane.blocker == "WNBA_PROP_CANDIDATE_NOT_PROMOTED"
        assert lane.can_execute is False


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


def test_undeclared_lane_fails_closed_without_guessing():
    lane = prop_capability("NFL", "TACKLES")
    assert lane.lane_status == NOT_DECLARED
    assert lane.route_active is False
    assert lane.publication_allowed is False
    assert lane.controlling_specialist is None
    assert lane.blocker == "PROP_LANE_NOT_DECLARED"
    assert lane.can_execute is False


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
    assert ("NFL", "PASSING_YARDS") in advertised
    assert ("NFL", "RUSHING_YARDS") in advertised
    assert ("NFL", "RECEIVING_YARDS") in advertised
    assert ("NFL", "ANYTIME_TD") in advertised
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
    assert manifest["declared_lane_count"] == 19
    assert manifest["route_active_lane_count"] == 10
    assert manifest["publication_allowed_lane_count"] == 9
    assert manifest["candidate_lane_count"] == 9
    assert manifest["sports_declared"] == ["MLB", "NBA", "NFL", "WNBA"]
    assert manifest["declaration_is_not_capability"] is True
    assert manifest["adjacent_line_substitution_permitted"] is False
    assert manifest["can_execute"] is False


def test_sport_aliases_resolve_without_changing_authority():
    assert prop_capability("baseball", MLB_1IP_STAT_TYPE).lane_status == SUPPORTED_HOLD_ONLY
    assert prop_capability("womens_nba", "POINTS").lane_status == CANDIDATE_ONLY
