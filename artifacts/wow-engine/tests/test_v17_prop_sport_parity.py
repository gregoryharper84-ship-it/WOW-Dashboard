from v17.prop_sport_parity import prop_sport_parity, prop_sport_parity_summary
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


def test_prop_parity_accounts_for_all_canonical_sports():
    rows = prop_sport_parity()
    assert set(rows) == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert len(rows) == 13
    assert rows["PGA"]["manifest_sport"] == "PGA"
    assert rows["PGA"]["build_required_lane_count"] == 3
    assert rows["CRICKET"]["manifest_sport"] == "CRICKET"
    assert rows["CRICKET"]["declared_lane_count"] == 2
    for sport in ("PGA", "CRICKET"):
        assert rows[sport]["certified_production_lane_count"] == 0
        assert rows[sport]["automatic_row_hydration_supported"] is False
        assert rows[sport]["daily_autonomous_discovery_supported"] is False


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


def _row(sport, blocked):
    payload = {"route_preflight_blocked": True, "specialist_scoring_attempted": False} if blocked else {"probability_publishable": False}
    return {
        "lane": "PROPS",
        "identity": {"sport": sport},
        "row_status": "REJECTED",
        "result": {"outcomes": [{"direction": "MORE", "payload": payload}, {"direction": "LESS", "payload": payload}]},
    }


def test_coverage_receipt_reports_every_requested_sport_and_reconciles():
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    rows = [_row("NFL", False), _row("NFL", False), _row("NBA", True)]
    receipt = prop_sport_coverage_receipt(requested_sports=["NFL", "NBA", "NHL", "GOLF"], rows=rows)
    per = receipt["per_sport"]
    assert set(per) == {"NFL", "NBA", "NHL", "PGA"}
    assert per["NFL"]["scored"] == 2 and per["NFL"]["unscored"] == 0
    assert per["NBA"]["scored"] == 0 and per["NBA"]["unscored"] == 1
    assert per["NHL"]["coverage_status"] == "NO_ROWS_DISCOVERY_NOT_IMPLEMENTED"
    assert per["NHL"]["zero_rows_is_not_empty_slate_proof"] is True
    assert per["NFL"]["autonomous_discovery_status"] == "NOT_IMPLEMENTED"
    assert receipt["total_prop_rows"] == 3 and receipt["all_reconciled"] is True
    assert "moneyline" in receipt["scope_note"]
    assert receipt["can_execute"] is False
    assert all(v["can_execute"] is False for v in per.values())
    assert all(r["row_status"] == "REJECTED" for r in rows)


def test_coverage_receipt_keeps_unrequested_sport_rows_visible():
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    receipt = prop_sport_coverage_receipt(requested_sports=["MLB"], rows=[_row("WNBA", False)])
    assert receipt["per_sport"]["WNBA"]["requested"] is False
    assert receipt["per_sport"]["MLB"]["coverage_status"] == "NO_ROWS_DISCOVERY_ENABLED"
    assert receipt["all_reconciled"] is True
    assert receipt["silent_sport_omission"] is False
