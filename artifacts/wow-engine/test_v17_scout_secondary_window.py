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

def test_secondary_event_preserves_espn_preseason_context():
    event = _event("nba-preseason-1", "2026-10-07T02:00:00Z", "Golden State Warriors", "Los Angeles Lakers")
    event["season"] = {"year": 2026, "type": 1, "slug": "preseason"}
    event["competitions"][0].update({
        "neutralSite": True,
        "venue": {"id": "venue-1", "fullName": "T-Mobile Arena"},
        "round": {"displayName": "Preseason Round"},
        "series": {"summary": "Exhibition series"},
        "importance": "EXHIBITION",
    })
    event["status"] = {"type": {"name": "STATUS_SCHEDULED"}}
    event["type"] = {"name": "Preseason"}

    row = secondary.espn_event_to_primary_shape(event, "basketball_nba")

    assert row is not None
    assert row["season_year"] == 2026
    assert row["season_type"] == 1
    assert row["season_slug"] == "preseason"
    assert row["season_phase"] == "PRESEASON"
    assert row["season_phase_source"] == "ESPN_SCOREBOARD"
    assert row["venue"] == "T-Mobile Arena"
    assert row["venue_id"] == "venue-1"
    assert row["neutral_site"] is True
    assert row["event_status"] == "STATUS_SCHEDULED"
    assert row["event_type"] == "Preseason"
    assert row["competition_round"] == "Preseason Round"
    assert row["series_state"] == "Exhibition series"
    assert row["competition_importance"] == "EXHIBITION"
    assert row["event_context_source"] == "ESPN_SCOREBOARD"

