from unittest.mock import patch

from services.odds_api import fetch_all_props


def test_fetch_all_props_reports_partial_event_coverage_when_one_event_fails():
    events = [{"id": "event-1"}, {"id": "event-2"}]

    with patch("services.odds_api.get_events", return_value=(events, "AVAILABLE")), patch(
        "services.odds_api.get_player_props",
        side_effect=[
            (None, "FAILED: timeout"),
            ({
                "bookmakers": [{
                    "key": "book",
                    "markets": [{
                        "key": "player_points",
                        "outcomes": [{
                            "description": "Player",
                            "name": "Over",
                            "point": 20.5,
                            "price": -110,
                        }],
                    }],
                }],
            }, "AVAILABLE"),
        ],
    ):
        props, status = fetch_all_props("NBA")

    assert len(props) == 1
    assert status["props"] == "AVAILABLE"
    assert status["coverage_props"] == "PARTIAL: 1/2 event prop fetches failed"
    assert status["event_prop_failure_count"] == 1
    assert len(status["event_prop_statuses"]) == 2


def test_fetch_all_props_attempts_every_active_event_not_only_the_first_ten():
    events = [{"id": f"event-{index}"} for index in range(11)]
    with patch("services.odds_api.get_events", return_value=(events, "AVAILABLE")), patch(
        "services.odds_api.get_player_props",
        return_value=(None, "AVAILABLE"),
    ) as fetch_event:
        props, status = fetch_all_props("NBA")

    assert props == []
    assert fetch_event.call_count == 11
    assert status["event_count"] == 11
    assert len(status["event_prop_statuses"]) == 11
    assert status["coverage_props"] == "AVAILABLE: 11/11 event prop fetches completed"