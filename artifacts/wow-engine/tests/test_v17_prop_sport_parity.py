from v17.prop_sport_parity import prop_sport_parity, prop_sport_parity_summary
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def test_prop_parity_accounts_for_all_canonical_sports():
    rows = prop_sport_parity()
    assert set(rows) == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert len(rows) == 13
    assert rows["PGA"]["manifest_sport"] == "GOLF"
    assert rows["CRICKET"]["declared_lane_count"] == 0


def test_prop_parity_distinguishes_row_hydration_from_autonomous_discovery():
    rows = prop_sport_parity()
    assert rows["MLB"]["automatic_row_hydration_supported"] is True
    assert rows["MLB"]["daily_autonomous_discovery_supported"] is True
    assert rows["NFL"]["automatic_row_hydration_supported"] is True
    assert rows["NFL"]["daily_autonomous_discovery_supported"] is False
    assert rows["WNBA"]["automatic_row_hydration_supported"] is True
    assert rows["WNBA"]["daily_autonomous_discovery_supported"] is False


def test_prop_parity_never_converts_zero_rows_to_no_capability_or_fallback():
    summary = prop_sport_parity_summary()
    assert summary["zero_candidates_is_not_capability_proof"] is True
    for row in summary["sports"].values():
        assert row["daily_zero_rows_mean_no_slate"] is False
        assert row["unsupported_route_fallback_allowed"] is False
        assert row["market_probability_substitution_allowed"] is False
        assert row["generic_reasoning_substitution_allowed"] is False
        assert row["can_execute"] is False
