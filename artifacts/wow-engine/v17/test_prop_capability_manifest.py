"""Every reachable prop lane must be declared with its true classification."""
from v17.prop_capability_manifest import (
    CERTIFIED_PRODUCTION,
    EXACT_CERTIFIED_LINES_ONLY,
    MLB_1IP_STAT_TYPE,
    NOT_DECLARED,
    SUPPORTED_HOLD_ONLY,
    TEST_ONLY,
    declared_prop_lane_manifest,
    prop_capability,
)


def test_pitcher_strikeouts_remains_the_certified_production_lane():
    capability = prop_capability("MLB", "PITCHER_STRIKEOUTS")
    assert capability.lane_status == CERTIFIED_PRODUCTION
    assert capability.publication_allowed is True
    assert capability.can_execute is False


def test_mlb_1ip_is_declared_hold_only_rather_than_silently_absent():
    capability = prop_capability("MLB", MLB_1IP_STAT_TYPE)
    assert capability.lane_status == SUPPORTED_HOLD_ONLY
    assert capability.route_active is True
    assert capability.declared_skill_status == TEST_ONLY
    assert capability.publication_allowed is False
    assert capability.blocker == "MLB_1IP_ARTIFACT_PROSPECTIVE_CERTIFIED_NOT_PROMOTED"


def test_mlb_1ip_requires_exact_certified_lines_and_forbids_adjacent_substitution():
    capability = prop_capability("MLB", MLB_1IP_STAT_TYPE)
    assert capability.exact_line_support_policy == EXACT_CERTIFIED_LINES_ONLY
    # The certified line set is registry-resolved, never hardcoded in the manifest.
    assert capability.certified_line_support_source.endswith("validated_lines")
    assert declared_prop_lane_manifest()["adjacent_line_substitution_permitted"] is False


def test_undeclared_lane_fails_closed_against_a_known_contract():
    capability = prop_capability("MLB", "HOME_RUNS")
    assert capability.lane_status == NOT_DECLARED
    assert capability.publication_allowed is False
    assert capability.blocker == "PROP_LANE_NOT_DECLARED"


def test_manifest_advertises_every_active_route():
    manifest = declared_prop_lane_manifest()
    advertised = {(lane["sport"], lane["stat_type"]) for lane in manifest["lanes"]}
    assert ("MLB", "PITCHER_STRIKEOUTS") in advertised
    assert ("MLB", MLB_1IP_STAT_TYPE) in advertised
    assert manifest["route_active_lane_count"] == 2
    assert manifest["publication_allowed_lane_count"] == 1
    assert manifest["declaration_is_not_capability"] is True
    assert manifest["can_execute"] is False


def test_sport_aliases_resolve_to_the_declared_lane():
    assert prop_capability("baseball", MLB_1IP_STAT_TYPE).lane_status == SUPPORTED_HOLD_ONLY
