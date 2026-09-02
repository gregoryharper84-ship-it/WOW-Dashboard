from datetime import datetime, timezone

import pytest

from prop_auto_hydration import auto_hydrate_prop_evidence


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _game_log_payload():
    splits = []
    for day in range(1, 11):
        splits.append({
            "date": f"2026-08-{day:02d}",
            "opponent": {"abbreviation": "OPP"},
            "stat": {
                "gamesStarted": 1,
                "strikeOuts": 5,
                "inningsPitched": "6.0",
                "battersFaced": 24,
                "baseOnBalls": 2,
                "earnedRuns": 2,
            },
        })
    return {"stats": [{"splits": splits}]}


def _http_get_factory(*, lineup_confirmed=True, split_pa=40, split_so=6):
    hitter_ids = list(range(201, 210))

    def fake_get(url, *, params, timeout):
        if url.endswith("/people/search"):
            return FakeResponse({"people": [{"id": 101, "fullName": "Test Pitcher"}]})
        if url.endswith("/schedule"):
            return FakeResponse({
                "dates": [{
                    "games": [{
                        "gamePk": 555,
                        "gameDate": "2026-09-02T20:00:00Z",
                        "venue": {"name": "Test Park"},
                        "status": {"detailedState": "Scheduled"},
                        "teams": {
                            "home": {
                                "probablePitcher": {"id": 101},
                                "team": {"id": 1, "abbreviation": "HME"},
                            },
                            "away": {"team": {"id": 2, "abbreviation": "AWY"}},
                        },
                    }]
                }]
            })
        if url.endswith("/people/101/stats"):
            return FakeResponse(_game_log_payload())
        if url.endswith("/people/101"):
            return FakeResponse({"people": [{"id": 101, "pitchHand": {"code": "R"}}]})
        if url.endswith("/game/555/boxscore"):
            batting = hitter_ids if lineup_confirmed else []
            return FakeResponse({
                "teams": {
                    "away": {"battingOrder": batting},
                    "home": {"battingOrder": []},
                }
            })
        for hitter_id in hitter_ids:
            if url.endswith(f"/people/{hitter_id}/stats"):
                assert params["sitCodes"] == "vr"
                return FakeResponse({
                    "stats": [{"splits": [{"stat": {
                        "plateAppearances": split_pa,
                        "strikeOuts": split_so,
                    }}]}]
                })
        raise AssertionError(f"unexpected URL {url}")

    return fake_get


def _hydrate(fake_get):
    return auto_hydrate_prop_evidence(
        sport="MLB",
        player="Test Pitcher",
        stat_type="PITCHER_STRIKEOUTS",
        event_start_time="2026-09-02T20:00:00Z",
        now=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
        http_get=fake_get,
    )


def test_confirmed_lineup_hydrates_hand_split_k_rate_and_expected_bf():
    payload = _hydrate(_http_get_factory())
    context = payload["opponent_context"]
    assert context["pitcher_hand"] == "R"
    assert context["lineup_status"] == "CONFIRMED"
    assert context["lineup_hitter_split_n"] == 9
    assert context["split_plate_appearances"] == 360
    assert context["k_rate_per_pa"] == pytest.approx(54 / 360)
    assert context["expected_batters_faced"] == pytest.approx(24.0)
    assert payload["opportunity_ledger"]["model_opponent_context"] == "CONFIRMED_LINEUP_HAND_SPLIT"
    assert "MLB_STATS_API_OPPONENT_LINEUP" in payload["source_timestamps"]
    assert "MLB_STATS_API_HITTER_HAND_SPLITS" in payload["source_timestamps"]


def test_unconfirmed_lineup_remains_neutral_without_inventing_opponent_rate():
    payload = _hydrate(_http_get_factory(lineup_confirmed=False))
    assert "opponent_context" not in payload
    assert payload["opportunity_ledger"]["model_opponent_context"] == "NEUTRAL_OFFICIAL_LINEUP_NOT_CONFIRMED"
    assert "MLB_STATS_API_HITTER_HAND_SPLITS" not in payload["source_timestamps"]


def test_insufficient_hand_split_sample_remains_neutral():
    payload = _hydrate(_http_get_factory(split_pa=5, split_so=1))
    assert "opponent_context" not in payload
    assert payload["opportunity_ledger"]["model_opponent_context"] == "NEUTRAL_LINEUP_HAND_SPLIT_SAMPLE_INSUFFICIENT"


def test_optional_opponent_context_failure_does_not_break_existing_hydration():
    base = _http_get_factory()

    def fake_get(url, *, params, timeout):
        if url.endswith("/people/101"):
            raise RuntimeError("optional profile unavailable")
        return base(url, params=params, timeout=timeout)

    payload = _hydrate(fake_get)
    assert payload["game_log"] == [5.0] * 10
    assert "opponent_context" not in payload
    assert payload["opportunity_ledger"]["model_opponent_context"] == "NEUTRAL_OPTIONAL_OPPONENT_CONTEXT_UNAVAILABLE"
