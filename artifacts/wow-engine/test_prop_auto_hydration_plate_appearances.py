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


def _pa_get(event_start, *, side="home", complete_lineup=True):
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append((url, params, timeout))
        if url.endswith("/people/search"):
            return _Response({"people": [{"id": 123, "fullName": "Test Batter"}]})
        if url.endswith("/people/123"):
            return _Response(
                {
                    "people": [
                        {
                            "id": 123,
                            "fullName": "Test Batter",
                            "currentTeam": {"id": 10, "name": "Home Club" if side == "home" else "Away Club"},
                        }
                    ]
                }
            )
        if url.endswith("/schedule"):
            home_id = 10 if side == "home" else 20
            away_id = 20 if side == "home" else 10
            return _Response(
                {
                    "dates": [
                        {
                            "games": [
                                {
                                    "gamePk": 999,
                                    "gameDate": event_start.isoformat(),
                                    "teams": {
                                        "home": {"team": {"id": home_id, "name": "Home Club", "abbreviation": "HOM"}},
                                        "away": {"team": {"id": away_id, "name": "Away Club", "abbreviation": "AWY"}},
                                    },
                                    "venue": {"name": "Test Park"},
                                    "status": {"detailedState": "Scheduled"},
                                }
                            ]
                        }
                    ]
                }
            )
        if url.endswith("/game/999/boxscore"):
            batting_order = [111, 123, 222, 333, 444, 555, 666, 777, 888] if complete_lineup else [111, 123]
            return _Response(
                {
                    "teams": {
                        side: {"battingOrder": batting_order},
                        ("away" if side == "home" else "home"): {"battingOrder": [901, 902, 903, 904, 905, 906, 907, 908, 909]},
                    }
                }
            )
        if url.endswith("/people/123/stats"):
            season = int(params["season"])
            if season != event_start.year:
                return _Response({"stats": [{"splits": []}]})
            splits = [
                {
                    "date": event_start.date().isoformat(),
                    "game": {"gamePk": 10000},
                    "opponent": {"abbreviation": "LEAK"},
                    "stat": {"plateAppearances": 9},
                }
            ]
            for i in range(12):
                splits.append(
                    {
                        "date": (event_start.date() - timedelta(days=i + 1)).isoformat(),
                        "game": {"gamePk": 9000 - i},
                        "opponent": {"abbreviation": f"O{i}"},
                        "stat": {"plateAppearances": 3 + (i % 3)},
                    }
                )
            return _Response({"stats": [{"splits": splits}]})
        raise AssertionError(f"unexpected URL: {url} params={params}")

    return fake_get, calls


@pytest.mark.parametrize(("side", "expected_alignment"), [("home", 1), ("away", 0)])
def test_plate_appearances_auto_hydration_matches_training_semantics(side, expected_alignment):
    now = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)
    fake_get, calls = _pa_get(event_start, side=side)

    evidence = hydration.auto_hydrate_prop_evidence(
        sport="MLB",
        player="Test Batter",
        stat_type="PLATE_APPEARANCES",
        event_start_time=event_start.isoformat(),
        http_get=fake_get,
        now=now,
        source_capture_timestamp=(now - timedelta(minutes=1)).isoformat(),
        source_label="SCREENSHOT:PRIZEPICKS",
    )

    assert len(evidence["game_log"]) == 10
    assert len(evidence["box_score_log"]) == 10
    assert 9.0 not in evidence["game_log"]  # same-day row is conservatively excluded
    assert evidence["role_status"]["role"] == "STARTING_BATTER"
    assert evidence["role_status"]["official_game_pk"] == 999
    ledger = evidence["opportunity_ledger"]
    assert ledger["status"] == "READY"
    assert ledger["batting_slot"] == 2
    assert ledger["team_alignment"] == expected_alignment
    assert ledger["team_alignment_semantics"] == "1=HOME,0=AWAY"
    assert ledger["prior_pa_log"] == [int(value) for value in evidence["game_log"]]
    assert ledger["lineup_status"] == "CONFIRMED"
    assert evidence["evidence_version"] == "PROP_EVIDENCE_V1"
    assert "MLB_STATS_API_OFFICIAL_BATTING_ORDER" in evidence["source_timestamps"]
    assert any(url.endswith("/game/999/boxscore") for url, _params, _timeout in calls)


def test_plate_appearances_auto_hydration_holds_when_official_lineup_is_not_complete():
    now = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)
    fake_get, _calls = _pa_get(event_start, complete_lineup=False)

    with pytest.raises(hydration.PropAutoHydrationError) as exc_info:
        hydration.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Test Batter",
            stat_type="PLATE_APPEARANCES",
            event_start_time=event_start.isoformat(),
            http_get=fake_get,
            now=now,
        )

    assert exc_info.value.code == "MLB_LINEUP_NOT_CONFIRMED"


def test_plate_appearances_auto_hydration_holds_when_batter_is_not_in_starting_nine():
    now = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    event_start = now + timedelta(hours=6)

    def fake_get(url, *, params, timeout):
        base_get, _calls = _pa_get(event_start)
        if url.endswith("/game/999/boxscore"):
            return _Response(
                {
                    "teams": {
                        "home": {"battingOrder": [111, 222, 333, 444, 555, 666, 777, 888, 999]},
                        "away": {"battingOrder": [901, 902, 903, 904, 905, 906, 907, 908, 909]},
                    }
                }
            )
        return base_get(url, params=params, timeout=timeout)

    with pytest.raises(hydration.PropAutoHydrationError) as exc_info:
        hydration.auto_hydrate_prop_evidence(
            sport="MLB",
            player="Test Batter",
            stat_type="PLATE_APPEARANCES",
            event_start_time=event_start.isoformat(),
            http_get=fake_get,
            now=now,
        )

    assert exc_info.value.code == "MLB_BATTER_NOT_IN_CONFIRMED_LINEUP"
