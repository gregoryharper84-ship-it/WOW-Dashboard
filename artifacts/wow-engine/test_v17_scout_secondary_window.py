from v17 import scout_secondary_source as secondary


def _event(event_id: str, commence_time: str, home: str, away: str) -> dict:
    return {
        "id": event_id,
        "date": commence_time,
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": home}},
                {"homeAway": "away", "team": {"displayName": away}},
            ]
        }],
    }


def test_secondary_events_enforce_exact_timestamp_window(monkeypatch):
    monkeypatch.setattr(
        secondary,
        "_scoreboard",
        lambda sport_key, params=None: secondary.SecondaryResult(
            True,
            {
                "events": [
                    _event("in-window", "2026-09-16T18:00:00Z", "Home A", "Away A"),
                    _event("after-cutoff", "2026-09-17T23:30:00Z", "Pittsburgh Panthers", "Syracuse Orange"),
                ]
            },
            200,
        ),
    )

    result = secondary.secondary_for_request(
        "/odds-api/v4/sports/americanfootball_ncaaf/events",
        {
            "commenceTimeFrom": "2026-09-15T13:42:14Z",
            "commenceTimeTo": "2026-09-17T01:42:14Z",
        },
        {},
    )

    assert result.ok is True
    assert [row["id"] for row in result.data] == ["espn-in-window"]
    assert result.data[0]["commence_time"] == "2026-09-16T18:00:00Z"
