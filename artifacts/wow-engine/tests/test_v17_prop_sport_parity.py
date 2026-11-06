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
    assert rows["NFL"]["daily_autonomous_discovery_supported"] is True
    assert rows["NFL"]["certified_production_lane_count"] == 4
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
    assert per["NFL"]["autonomous_discovery_status"] == "ENABLED"
    assert receipt["total_prop_rows"] == 3 and receipt["all_reconciled"] is False
    assert per["NFL"]["reconciled"] is None
    assert "moneyline" in receipt["scope_note"]
    assert receipt["can_execute"] is False
    assert all(v["can_execute"] is False for v in per.values())
    assert all(r["row_status"] == "REJECTED" for r in rows)


def test_coverage_receipt_keeps_unrequested_sport_rows_visible():
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    receipt = prop_sport_coverage_receipt(requested_sports=["MLB"], rows=[_row("WNBA", False)])
    assert receipt["per_sport"]["WNBA"]["requested"] is False
    assert receipt["per_sport"]["MLB"]["coverage_status"] == "NO_ROWS_DISCOVERY_ENABLED"
    assert receipt["all_reconciled"] is False
    assert receipt["silent_sport_omission"] is False


def test_nba_nhl_remain_without_autonomous_discovery_or_hydration():
    rows = prop_sport_parity()
    for sport in ("NBA", "NHL"):
        assert rows[sport]["daily_autonomous_discovery_supported"] is False
        assert rows[sport]["automatic_row_hydration_supported"] is False
        assert rows[sport]["certified_production_lane_count"] == 0
    summary = prop_sport_parity_summary()
    assert {"NBA", "NHL"} <= set(summary["sports_without_daily_autonomous_discovery"])


def test_default_full_board_receipt_does_not_claim_explicit_sport_selection():
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    receipt = prop_sport_coverage_receipt(requested_sports=None, rows=[])
    assert receipt["request_selection_scope"] == "ALL_CATALOGED_DEFAULT"
    assert len(receipt["per_sport"]) == 13
    assert all(v["requested"] and v["explicitly_requested"] is False for v in receipt["per_sport"].values())
    assert receipt["can_execute"] is False


def test_coverage_reconciliation_compares_independent_attempted_receipts():
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    acquisition = {
        "sport_coverage": [{"sport": "NFL", "status": "COMPLETED", "attempted": 2}],
        "receipts": [
            {"sport": "NFL", "write_status": "SNAPSHOT_WRITE_SUCCEEDED"},
            {"sport": "NFL", "write_status": "PREWRITE_EXCLUDED"},
        ],
    }
    r = prop_sport_coverage_receipt(requested_sports=["NFL"], rows=[_row("NFL", True)], acquisition=acquisition)
    assert r["per_sport"]["NFL"]["reconciled"] is True
    assert r["all_reconciled"] is True
    assert r["canonical_persistence_readback_proven_here"] is False
    acquisition["sport_coverage"][0]["attempted"] = 3
    bad = prop_sport_coverage_receipt(requested_sports=["NFL"], rows=[_row("NFL", True)], acquisition=acquisition)
    assert bad["per_sport"]["NFL"]["reconciled"] is False
    assert bad["all_reconciled"] is False


def test_coverage_receipt_names_data_outage_instead_of_empty_slate():
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    acquisition = {"sport_coverage": [{"sport": "NFL", "status": "DATA_UNOBTAINABLE", "attempted": 0}],
                   "receipts": []}
    r = prop_sport_coverage_receipt(requested_sports=["NFL"], rows=[], acquisition=acquisition)
    assert r["per_sport"]["NFL"]["coverage_status"] == "DISCOVERY_DATA_UNOBTAINABLE"
    assert r["per_sport"]["NFL"]["zero_rows_is_not_empty_slate_proof"] is True
    assert r["can_execute"] is False


def test_coverage_receipt_missing_sport_leg_fails_reconciliation():
    """An NFL receipt cannot borrow an MLB zero-attempt reconciliation."""
    from v17.prop_sport_parity import prop_sport_coverage_receipt

    acquisition = {
        "sport_coverage": [{"sport": "MLB", "status": "COMPLETED", "attempted": 0}],
        "receipts": [{"sport": "NFL", "write_status": "SNAPSHOT_WRITE_SUCCEEDED"}],
    }
    result = prop_sport_coverage_receipt(
        requested_sports=["MLB", "NFL"], rows=[], acquisition=acquisition,
    )
    assert result["per_sport"]["MLB"]["reconciled"] is True
    assert result["per_sport"]["NFL"]["reconciled"] is None
    assert result["all_reconciled"] is False
    assert result["can_execute"] is False
