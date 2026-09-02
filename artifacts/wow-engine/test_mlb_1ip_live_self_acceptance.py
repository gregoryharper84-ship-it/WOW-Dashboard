from datetime import datetime, timezone

from mlb_1ip_live_self_acceptance import _next_probable_pitcher, _row


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_discovers_next_future_probable_pitcher():
    now = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)

    def fake_get(url, *, params, timeout):
        assert params["sportId"] == "1"
        assert "probablePitcher" in params["hydrate"]
        return _Response(
            {
                "dates": [
                    {
                        "games": [
                            {
                                "gamePk": 12345,
                                "gameDate": "2026-09-02T23:10:00Z",
                                "teams": {
                                    "away": {"probablePitcher": {"fullName": "Test Pitcher"}},
                                    "home": {"probablePitcher": {}},
                                },
                            }
                        ]
                    }
                ]
            }
        )

    result = _next_probable_pitcher(now=now, http_get=fake_get)
    assert result == {
        "event_id": "12345",
        "event_start_time": "2026-09-02T23:10:00+00:00",
        "player": "Test Pitcher",
    }


def test_self_acceptance_row_is_non_executable_governed_1ip():
    candidate = {
        "event_id": "12345",
        "event_start_time": "2026-09-02T23:10:00+00:00",
        "player": "Test Pitcher",
    }
    row = _row(candidate=candidate, row_key="supported-15.5", line=15.5)
    assert row["sport"] == "MLB"
    assert row["stat_type"] == "1ST_INNING_PITCHES_THROWN"
    assert row["line"] == 15.5
    assert row["money_lane_status"] == "PAYOUT_UNRESOLVED"
    assert "probability" not in row
