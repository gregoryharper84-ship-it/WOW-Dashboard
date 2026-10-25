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


def test_midnight_utc_event_relookup_uses_original_espn_scoreboard_date(monkeypatch):
    event = _event(401908014)
    event["date"] = "2026-10-05T00:00:00Z"
    event["competitions"][0]["odds"] = [{
        "homeTeamOdds": {"moneyLine": -110},
        "awayTeamOdds": {"moneyLine": -105},
        "provider": {"name": "Example Book"},
    }]
    seen = []

    def fake_http_json(url, params=None):
        day = str(params["dates"])
        seen.append(day)
        return secondary.SecondaryResult(
            True,
            {"events": [event] if day == "20261004" else []},
            200,
        )

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    secondary._SCOREBOARD_CACHE.clear()

    discovery = secondary.secondary_for_request(
        "/odds-api/v4/sports/baseball_mlb/events",
        {
            "commenceTimeFrom": "2026-10-04T13:22:56Z",
            "commenceTimeTo": "2026-10-05T01:22:56Z",
        },
        {},
    )

    assert discovery.ok is True
    row = discovery.data[0]
    assert row["id"] == "espn-401908014"
    assert row["_wow_secondary_scoreboard_dates"] == ["20261004"]

    context = {
        row["id"]: {
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "commence_time": row["commence_time"],
            "_wow_secondary_scoreboard_dates": row["_wow_secondary_scoreboard_dates"],
        }
    }
    secondary._SCOREBOARD_CACHE.clear()
    seen.clear()

    market = secondary.secondary_for_request(
        "/odds-api/v4/sports/baseball_mlb/events/espn-401908014/odds",
        {"markets": "h2h"},
        context,
        primary_failure="ODDS_PROVIDER_NON_JSON:HTTP_429",
    )

    assert market.ok is True
    assert seen == ["20261004"]
    assert market.data["_wow_secondary_source"]["prediction_authority"] is False
    assert market.data["_wow_secondary_source"]["can_execute"] is False


@pytest.mark.parametrize(
    ("sport_key", "core_fragment"),
    [
        ("baseball_mlb", "/baseball/leagues/mlb/"),
        ("basketball_ncaab", "/basketball/leagues/mens-college-basketball/"),
        ("basketball_wnba", "/basketball/leagues/wnba/"),
        ("icehockey_nhl", "/hockey/leagues/nhl/"),
    ],
)
def test_event_market_uses_espn_core_odds_when_scoreboard_has_no_h2h(
    monkeypatch,
    sport_key,
    core_fragment,
):
    event = _event(401908014)
    event["competitions"][0]["id"] = "401908014"
    event["competitions"][0]["odds"] = []
    event["_wow_secondary_scoreboard_dates"] = ["20261004"]

    monkeypatch.setattr(
        secondary,
        "_find_event",
        lambda requested_sport, event_id, context: secondary.SecondaryResult(True, event, 200),
    )
    seen = []

    def fake_http_json(url, params=None):
        seen.append((url, dict(params or {})))
        return secondary.SecondaryResult(
            True,
            {
                "items": [{
                    "provider": {"id": "41", "name": "DraftKings"},
                    "homeTeamOdds": {"moneyLine": -125},
                    "awayTeamOdds": {"moneyLine": 105},
                    "lastUpdated": "2026-10-04T15:00:00Z",
                }]
            },
            200,
        )

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)

    result = secondary.secondary_for_request(
        f"/odds-api/v4/sports/{sport_key}/events/espn-401908014/odds",
        {"markets": "h2h"},
        {"espn-401908014": {"_wow_secondary_scoreboard_dates": ["20261004"]}},
        primary_failure="ODDS_PROVIDER_NON_JSON:HTTP_429",
    )

    assert result.ok is True
    assert result.status == 200
    assert len(seen) == 1
    assert core_fragment in seen[0][0]
    assert seen[0][0].endswith(
        "/events/401908014/competitions/401908014/odds"
    )
    assert seen[0][1] == {"limit": 25}
    marker = result.data["_wow_secondary_source"]
    assert marker["provider"] == "ESPN_CORE_ODDS_RESEARCH_FALLBACK"
    assert marker["provider_detail"] == "DraftKings"
    assert marker["prediction_authority"] is False
    assert marker["exact_line_authority"] is False
    assert marker["can_execute"] is False
    market = result.data["bookmakers"][0]["markets"][0]
    assert market["key"] == "h2h"
    assert market["outcomes"] == [
        {"name": "Home 401908014", "price": -125},
        {"name": "Away 401908014", "price": 105},
    ]


def test_empty_espn_core_odds_stays_typed_h2h_unavailable(monkeypatch):
    event = _event(401908014)
    event["competitions"][0]["id"] = "401908014"
    event["competitions"][0]["odds"] = []

    monkeypatch.setattr(
        secondary,
        "_find_event",
        lambda requested_sport, event_id, context: secondary.SecondaryResult(True, event, 200),
    )
    monkeypatch.setattr(
        secondary,
        "_http_json",
        lambda url, params=None: secondary.SecondaryResult(True, {"items": []}, 200),
    )

    result = secondary.secondary_for_request(
        "/odds-api/v4/sports/baseball_mlb/events/espn-401908014/odds",
        {"markets": "h2h"},
        {},
        primary_failure="ODDS_PROVIDER_NON_JSON:HTTP_429",
    )

    assert result.ok is False
    assert result.status == 404
    assert result.code == "SECONDARY_SOURCE_H2H_UNAVAILABLE"
