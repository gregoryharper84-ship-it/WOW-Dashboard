from datetime import datetime, timedelta, timezone

import pytest

import prop_auto_hydration as hydration


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _successful_get(event_start):
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append((url, params, timeout))
        if url.endswith("/people/search"):
            return _Response({"people": [{"id": 123, "fullName": "Test Pitcher"}]})
        if url.endswith("/schedule"):
            return _Response(
                {
                    "dates": [
                        {
                            "games": [
                                {
                                    "gamePk": 999,
                                    "gameDate": event_start.isoformat(),
                                    "teams": {
                                        "home": {
                                            "team": {"name": "Home Club", "abbreviation": "HOM"},
                                            "probablePitcher": {"id": 123, "fullName": "Test Pitcher"},
                                        },
                                        "away": {
                                            "team": {"name": "Away Club", "abbreviation": "AWY"},
                                        },
                                    },
                                    "venue": {"name": "Test Park"},
                                    "status": {"detailedState": "Scheduled"},
                                }
                            ]
                        }
                    ]
                }
            )
        if url.endswith("/people/123/stats"):
            splits = []
            for i in range(12):
                game_date = (event_start.date() - timedelta(days=i + 1)).isoformat()
                splits.append(
                    {
                        "date": game_date,
                        "opponent": {"abbreviation": f"O{i}"},
                        "stat": {
                            "gamesStarted": 1,
                            "strikeOuts": 4 + (i % 5),
                            "inningsPitched": "6.2" if i == 0 else "6.0",
                            "baseOnBalls": 2,
                            "earnedRuns": 2,
                        },
                    }
                )
            return _Response({"stats": [{"splits": splits}]})
        raise AssertionError(f"unexpected URL: {url}")

    return fake_get, calls


def test_supported_mlb_pitcher_strikeout_route_hydrates_exact_l10():
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)
    fake_get, calls = _successful_get(event_start)

    evidence = hydration.auto_hydrate_prop_evidence(
        sport="MLB",
        player="Test Pitcher",
        stat_type="PITCHER_STRIKEOUTS",
        event_start_time=event_start.isoformat(),
        http_get=fake_get,
        now=now,
        source_capture_timestamp=(now - timedelta(minutes=2)).isoformat(),
        source_label="SCREENSHOT:PRIZEPICKS",
    )

    assert len(evidence["game_log"]) == 10
    assert len(evidence["box_score_log"]) == 10
    assert evidence["game_log"][0] == 4.0
    assert evidence["box_score_log"][0]["outs"] == 20
    assert evidence["role_status"]["role"] == "STARTING_PITCHER"
    assert evidence["role_status"]["official_game_pk"] == 999
    assert evidence["role_status"]["opponent"] == "AWY"
    assert evidence["opportunity_ledger"]["status"] == "READY"
    assert evidence["opportunity_ledger"]["regular_season_prior_starts"] == 10
    assert evidence["evidence_version"] == "PROP_EVIDENCE_V1"
    assert "INPUT_CAPTURE_SCREENSHOT:PRIZEPICKS" in evidence["source_timestamps"]
    # The first three calls are the required baseline acquisition contract.
    # Optional opponent-context hydration may make additional official-source
    # calls, but any failure there must remain neutral/backward-compatible.
    assert len(calls) >= 3
    assert calls[0][0].endswith("/people/search")
    assert calls[1][0].endswith("/schedule")
    assert calls[2][0].endswith("/people/123/stats")


def test_unsupported_route_fails_before_any_external_request():
    called = {"value": False}

    def should_not_call(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("unsupported route must not touch provider")

    with pytest.raises(hydration.PropAutoHydrationError) as exc_info:
        hydration.auto_hydrate_prop_evidence(
            sport="WNBA",
            player="Player",
            stat_type="REB",
            event_start_time="2026-08-30T20:00:00+00:00",
            http_get=should_not_call,
        )

    assert exc_info.value.code == "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE"
    assert called["value"] is False


def test_event_already_started_fails_before_provider_calls():
    now = datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc)
    called = {"value": False}

    def should_not_call(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("post-start hydration must not touch provider")

    with pytest.raises(hydration.PropAutoHydrationError) as exc_info:
        hydration.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Test Pitcher",
            stat_type="PITCHER_STRIKEOUTS",
            event_start_time=(now - timedelta(minutes=1)).isoformat(),
            http_get=should_not_call,
            now=now,
        )

    assert exc_info.value.code == "EVENT_ALREADY_STARTED"
    assert called["value"] is False


def test_missing_official_probable_pitcher_fails_closed():
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)

    def fake_get(url, *, params, timeout):
        if url.endswith("/people/search"):
            return _Response({"people": [{"id": 123, "fullName": "Test Pitcher"}]})
        if url.endswith("/schedule"):
            return _Response(
                {
                    "dates": [
                        {
                            "games": [
                                {
                                    "gamePk": 999,
                                    "gameDate": event_start.isoformat(),
                                    "teams": {
                                        "home": {"team": {"name": "Home"}, "probablePitcher": {"id": 456}},
                                        "away": {"team": {"name": "Away"}},
                                    },
                                }
                            ]
                        }
                    ]
                }
            )
        raise AssertionError("game log must not be requested before starter confirmation")

    with pytest.raises(hydration.PropAutoHydrationError) as exc_info:
        hydration.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Test Pitcher",
            stat_type="PITCHER_STRIKEOUTS",
            event_start_time=event_start.isoformat(),
            http_get=fake_get,
            now=now,
        )

    assert exc_info.value.code == "MLB_STARTER_STATUS_UNRESOLVED"


def test_fewer_than_ten_prior_starts_fails_closed():
    now = datetime(2026, 8, 29, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)

    def fake_get(url, *, params, timeout):
        if url.endswith("/people/search"):
            return _Response({"people": [{"id": 123, "fullName": "Test Pitcher"}]})
        if url.endswith("/schedule"):
            return _Response(
                {
                    "dates": [
                        {
                            "games": [
                                {
                                    "gamePk": 999,
                                    "gameDate": event_start.isoformat(),
                                    "teams": {
                                        "home": {
                                            "team": {"name": "Home"},
                                            "probablePitcher": {"id": 123},
                                        },
                                        "away": {"team": {"name": "Away"}},
                                    },
                                }
                            ]
                        }
                    ]
                }
            )
        if url.endswith("/people/123/stats"):
            splits = []
            for i in range(9):
                splits.append(
                    {
                        "date": (event_start.date() - timedelta(days=i + 1)).isoformat(),
                        "stat": {
                            "gamesStarted": 1,
                            "strikeOuts": 5,
                            "inningsPitched": "6.0",
                        },
                    }
                )
            return _Response({"stats": [{"splits": splits}]})
        raise AssertionError(f"unexpected URL: {url}")

    with pytest.raises(hydration.PropAutoHydrationError) as exc_info:
        hydration.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Test Pitcher",
            stat_type="PITCHER_STRIKEOUTS",
            event_start_time=event_start.isoformat(),
            http_get=fake_get,
            now=now,
        )

    assert exc_info.value.code == "MLB_RECENT_STARTS_INSUFFICIENT"
    assert exc_info.value.detail["starts_found"] == 9
