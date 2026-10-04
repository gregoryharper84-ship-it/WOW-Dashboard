import json

import pytest

from v17 import full_board_runtime as runtime
from v17 import scout_secondary_source as secondary


def _event(index: int) -> dict:
    return {
        "id": str(index),
        "date": "2026-10-04T12:00:00Z",
        "status": {"type": {"name": "STATUS_SCHEDULED"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"displayName": f"Home {index}"}},
                {"homeAway": "away", "team": {"displayName": f"Away {index}"}},
            ],
            "unused_competition_blob": "x" * 2048,
        }],
        "unused_event_blob": "y" * 4096,
    }


def test_scoreboard_clamps_limit_partitions_cache_and_surfaces_truncation(monkeypatch):
    seen = []

    def fake_http_json(url, params=None):
        seen.append(int(params["limit"]))
        return secondary.SecondaryResult(True, {"events": [_event(1)]}, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    secondary._SCOREBOARD_CACHE.clear()
    params = {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"}

    one = secondary._scoreboard("baseball_mlb", params, provider_limit=0)
    two = secondary._scoreboard("baseball_mlb", params, provider_limit=2)
    cached_one = secondary._scoreboard("baseball_mlb", params, provider_limit=0)
    fallback = secondary._scoreboard("baseball_mlb", params, provider_limit="bad")

    assert seen == [1, 2, 1000]
    assert one.data["_wow_provider_limit"] == 1
    assert one.data["_wow_provider_truncated"] is True
    assert two.data["_wow_provider_limit"] == 2
    assert two.data["_wow_provider_truncated"] is False
    assert cached_one.data == one.data
    assert fallback.data["_wow_provider_limit"] == 1000
    assert fallback.data["_wow_provider_truncated"] is False


@pytest.mark.parametrize(
    "sport_key",
    ["baseball_mlb", "basketball_wnba", "icehockey_nhl"],
)
def test_compact_fixture_bounds_provider_and_never_hides_truncation(monkeypatch, sport_key):
    raw = {
        "events": [_event(i) for i in range(250)],
        "_wow_provider_limit": 250,
        "_wow_provider_result_count": 250,
        "_wow_provider_truncated": True,
    }
    seen = {}

    def fake_scoreboard(requested_sport, params=None, *, provider_limit=1000):
        seen["sport"] = requested_sport
        seen["limit"] = provider_limit
        return secondary.SecondaryResult(True, raw, 200)

    monkeypatch.setattr(secondary, "_scoreboard", fake_scoreboard)
    result = runtime.compact_espn_scoreboard(
        sport_key=sport_key,
        date="2026-10-04",
        page=1,
        page_size=100,
    )

    assert seen == {"sport": sport_key, "limit": 250}
    assert result["status"] == "DISCOVERY_INCOMPLETE_PROVIDER_LIMIT"
    assert result["provider_code"] == "ESPN_SCOREBOARD_PROVIDER_LIMIT_REACHED"
    assert result["provider_truncated"] is True
    assert result["coverage_complete"] is False
    assert result["prediction_authority"] is False
    assert result["exact_line_authority"] is False
    assert result["can_execute"] is False
    assert len(json.dumps(result)) < len(json.dumps(raw)) / 10



@pytest.mark.parametrize(
    "sport_key",
    ["baseball_mlb", "basketball_ncaab", "basketball_wnba", "icehockey_nhl"],
)
def test_multiday_fallback_uses_daily_espn_requests_and_dedupes(monkeypatch, sport_key):
    seen = []

    def fake_http_json(url, params=None):
        day = str(params["dates"])
        seen.append((day, int(params["limit"])))
        raw = _event(int(day[-2:]))
        raw["id"] = f"{sport_key}-{day}"
        raw["date"] = f"{day[:4]}-{day[4:6]}-{day[6:]}T18:00:00Z"
        return secondary.SecondaryResult(True, {"events": [raw]}, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    secondary._SCOREBOARD_CACHE.clear()

    result = secondary._scoreboard(
        sport_key,
        {
            "commenceTimeFrom": "2026-10-04T13:22:56Z",
            "commenceTimeTo": "2026-10-06T01:22:56Z",
        },
        provider_limit=250,
    )

    assert result.ok
    assert seen == [
        ("20261004", 250),
        ("20261005", 250),
        ("20261006", 250),
    ]
    assert result.data["_wow_provider_request_dates"] == [
        "20261004",
        "20261005",
        "20261006",
    ]
    assert result.data["_wow_provider_daily_request_count"] == 3
    assert result.data["_wow_provider_result_count"] == 3
    assert result.data["_wow_provider_truncated"] is False
    assert [event["id"] for event in result.data["events"]] == [
        f"{sport_key}-20261004",
        f"{sport_key}-20261005",
        f"{sport_key}-20261006",
    ]


def test_multiday_fallback_fails_closed_on_one_bad_daily_response(monkeypatch):
    seen = []

    def fake_http_json(url, params=None):
        day = str(params["dates"])
        seen.append(day)
        if day == "20261005":
            return secondary.SecondaryResult(False, status=400, code="ESPN_HTTP_400")
        return secondary.SecondaryResult(True, {"events": [_event(1)]}, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    secondary._SCOREBOARD_CACHE.clear()

    result = secondary._scoreboard(
        "baseball_mlb",
        {
            "commenceTimeFrom": "2026-10-04T13:22:56Z",
            "commenceTimeTo": "2026-10-06T01:22:56Z",
        },
        provider_limit=250,
    )

    assert result.ok is False
    assert result.status == 400
    assert result.code == "ESPN_HTTP_400"
    assert seen == ["20261004", "20261005"]


def test_multiday_fallback_propagates_any_daily_truncation(monkeypatch):
    def fake_http_json(url, params=None):
        day = str(params["dates"])
        events = [_event(i) for i in range(250 if day == "20261005" else 1)]
        return secondary.SecondaryResult(True, {"events": events}, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    secondary._SCOREBOARD_CACHE.clear()

    result = secondary._scoreboard(
        "basketball_ncaab",
        {
            "commenceTimeFrom": "2026-10-04T13:22:56Z",
            "commenceTimeTo": "2026-10-06T01:22:56Z",
        },
        provider_limit=250,
    )

    assert result.ok
    assert result.data["_wow_provider_truncated"] is True
