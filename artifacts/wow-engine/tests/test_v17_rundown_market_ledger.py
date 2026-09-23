from v17.rundown_market_ledger import price_observations_from_rundown_event


def test_native_v2_quote_flattening_preserves_provider_timestamp_and_affiliate():
    event = {
        "event_id": "evt-1",
        "event_date": "2026-09-23T00:10:00Z",
        "markets": [
            {
                "market_id": 1,
                "name": "Moneyline",
                "participants": [
                    {
                        "participant_id": "home-1",
                        "name": "Home Club",
                        "type": "home",
                        "lines": [
                            {
                                "line_id": "line-home",
                                "selection": "Home Club",
                                "is_main_line": True,
                                "prices": {
                                    "7": {
                                        "affiliate_name": "Pinnacle",
                                        "price": -125,
                                        "updated_at": "2026-09-22T23:59:50Z",
                                    }
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }
    rows = price_observations_from_rundown_event(
        event,
        sport_key="baseball_mlb",
        snapshot_kind="CURRENT",
        fetched_at="2026-09-23T00:00:00Z",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["provider_event_id"] == "evt-1"
    assert row["affiliate_id"] == "7"
    assert row["sportsbook"] == "Pinnacle"
    assert row["price_updated_at"] == "2026-09-22T23:59:50Z"
    assert row["fetched_at"] == "2026-09-23T00:00:00Z"
    assert row["american_odds"] == -125
    assert row["decimal_odds"] == 1.8
    assert row["prediction_authority"] is False
    assert row["can_execute"] is False


def test_fetch_time_does_not_replace_missing_price_update_time():
    event = {
        "event_id": "evt-1",
        "markets": [
            {
                "market_id": 1,
                "participants": [
                    {"name": "Home", "lines": [{"prices": {"7": {"price": -110}}}]}
                ],
            }
        ],
    }
    row = price_observations_from_rundown_event(
        event,
        sport_key="baseball_mlb",
        snapshot_kind="CURRENT",
        fetched_at="2026-09-23T00:00:00Z",
    )[0]
    assert row["price_updated_at"] is None
    assert row["fetched_at"] == "2026-09-23T00:00:00Z"
