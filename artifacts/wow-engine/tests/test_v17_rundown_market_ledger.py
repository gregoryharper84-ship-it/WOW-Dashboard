from v17.rundown_market_ledger import price_observations_from_rundown_event


def _event(price=-125, *, updated_at="2026-09-22T23:59:50Z"):
    return {
        "event_id": "evt-1",
        "event_date": "2026-09-23T00:10:00Z",
        "markets": [
            {
                "market_id": 1,
                "name": "moneyline",
                "participants": [
                    {
                        "participant_id": "home-1",
                        "name": "Home",
                        "type": "home",
                        "lines": [
                            {
                                "line_id": "h1",
                                "selection": "Home",
                                "is_main_line": True,
                                "prices": {
                                    "19": {
                                        "affiliate_name": "Pinnacle",
                                        "price": price,
                                        "updated_at": updated_at,
                                    }
                                },
                            }
                        ],
                    }
                ],
            }
        ],
    }


def test_provider_quote_time_and_fetch_time_are_distinct():
    row = price_observations_from_rundown_event(
        _event(),
        sport_key="baseball_mlb",
        snapshot_kind="CURRENT",
        fetched_at="2026-09-23T00:00:00Z",
    )[0]
    assert row["price_updated_at"] == "2026-09-22T23:59:50Z"
    assert row["fetched_at"] == "2026-09-23T00:00:00Z"
    assert row["american_odds"] == -125
    assert row["decimal_odds"] == 1.8
    assert row["is_available"] is True
    assert row["prediction_authority"] is False
    assert row["can_execute"] is False


def test_fetch_time_never_backfills_missing_provider_quote_time():
    event = _event(updated_at=None)
    row = price_observations_from_rundown_event(
        event,
        sport_key="baseball_mlb",
        snapshot_kind="CURRENT",
        fetched_at="2026-09-23T00:00:00Z",
    )[0]
    assert row["price_updated_at"] is None
    assert row["fetched_at"] == "2026-09-23T00:00:00Z"


def test_off_board_sentinel_is_stored_as_unavailable_not_as_price():
    row = price_observations_from_rundown_event(
        _event(price=0.0001),
        sport_key="baseball_mlb",
        snapshot_kind="CURRENT",
        fetched_at="2026-09-23T00:00:00Z",
    )[0]
    assert row["is_available"] is False
    assert row["american_odds"] is None
    assert row["decimal_odds"] is None
    assert row["prediction_authority"] is False
    assert row["can_execute"] is False
