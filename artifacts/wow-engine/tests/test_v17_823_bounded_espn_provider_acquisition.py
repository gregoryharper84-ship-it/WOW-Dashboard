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


@pytest.mark.parametrize("payload", [
    {"unexpected": []},
    {"events": None},
    {"events": {"id": "not-a-list"}},
    {"events": ["not-an-event-object"]},
])
def test_scoreboard_malformed_http_200_cannot_claim_empty_slate(monkeypatch, payload):
    calls = []

    def fake_http_json(_url, _params=None):
        calls.append(1)
        return secondary.SecondaryResult(True, payload, 200)

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    secondary._SCOREBOARD_CACHE.clear()
    params = {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"}

    for _ in range(2):
        result = secondary._scoreboard("americanfootball_nfl", params)
        assert result.ok is False
        assert result.code == "ESPN_SCOREBOARD_EVENTS_SCHEMA_UNRECOGNISED"
        assert result.status == 200
    # Invalid receipts must not be cached as successful discovery.
    assert len(calls) == 2


def test_scoreboard_verified_empty_array_remains_distinct_from_schema_failure(monkeypatch):
    monkeypatch.setattr(
        secondary, "_http_json",
        lambda _url, _params=None: secondary.SecondaryResult(True, {"events": []}, 200),
    )
    secondary._SCOREBOARD_CACHE.clear()
    result = secondary.secondary_for_request(
        "/odds-api/v4/sports/americanfootball_nfl/events",
        {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"},
        {},
    )
    assert result.ok is True
    assert result.data == []


def test_full_slate_secondary_refuses_truncated_espn_board(monkeypatch):
    monkeypatch.setattr(
        secondary, "_scoreboard",
        lambda *_args, **_kwargs: secondary.SecondaryResult(
            True,
            {"events": [_event(1)], "_wow_provider_truncated": True},
            200,
        ),
    )
    result = secondary.secondary_for_request(
        "/odds-api/v4/sports/americanfootball_nfl/events",
        {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"},
        {},
    )
    assert result.ok is False
    assert result.code == "ESPN_SCOREBOARD_PROVIDER_LIMIT_REACHED"
    assert result.data is None


def test_full_slate_secondary_accepts_proved_untruncated_event(monkeypatch):
    monkeypatch.setattr(
        secondary, "_scoreboard",
        lambda *_args, **_kwargs: secondary.SecondaryResult(
            True,
            {"events": [_event(1)], "_wow_provider_truncated": False},
            200,
        ),
    )
    result = secondary.secondary_for_request(
        "/odds-api/v4/sports/americanfootball_nfl/events",
        {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04T23:59:59Z"},
        {},
    )
    assert result.ok is True
    assert len(result.data) == 1
    assert result.data[0]["id"] == "espn-1"
    assert "probability" not in result.data[0]


@pytest.mark.parametrize("invalid", [
    {"date": "2026-10-04T12:00:00Z"},
    {"id": "401", "date": None},
    {"id": "401", "date": []},
    {"id": "401", "date": "garbage"},
])
def test_partial_event_identity_cannot_be_silently_dropped(monkeypatch, invalid):
    monkeypatch.setattr(
        secondary, "_http_json",
        lambda _url, _params=None: secondary.SecondaryResult(
            True, {"events": [_event(1), invalid]}, 200
        ),
    )
    secondary._SCOREBOARD_CACHE.clear()
    result = secondary._scoreboard(
        "americanfootball_nfl",
        {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"},
    )
    assert result.ok is False
    assert result.code == "ESPN_SCOREBOARD_EVENT_IDENTITY_SCHEMA_UNRECOGNISED"


def test_espn_event_board_requires_explicit_completeness_attestation(monkeypatch):
    monkeypatch.setattr(
        secondary, "_scoreboard",
        lambda *_args, **_kwargs: secondary.SecondaryResult(
            True, {"events": [_event(1)]}, 200
        ),
    )
    result = secondary.secondary_for_request(
        "/odds-api/v4/sports/americanfootball_nfl/events",
        {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"},
        {},
    )
    assert result.ok is False
    assert result.code == "ESPN_SCOREBOARD_COMPLETENESS_UNVERIFIED"


def test_acquisition_classifier_preserves_espn_schema_failure_category():
    from v17 import cross_sport_winner_discovery as discovery

    for code in (
        "ESPN_SCOREBOARD_EVENTS_SCHEMA_UNRECOGNISED",
        "ESPN_SCOREBOARD_EVENT_IDENTITY_SCHEMA_UNRECOGNISED",
    ):
        assert discovery.classify_acquisition_failure(code) == discovery.PROVIDER_SCHEMA_FAILURE


def test_same_process_scoreboard_cache_expires_before_future_discovery(monkeypatch):
    observed = []
    clock = [100.0]

    def fake_http_json(_url, _params=None):
        observed.append(len(observed) + 1)
        return secondary.SecondaryResult(
            True, {"events": [_event(observed[-1])]}, 200
        )

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    monkeypatch.setattr(secondary, "monotonic", lambda: clock[0])
    secondary._SCOREBOARD_CACHE.clear()
    params = {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"}

    first = secondary._scoreboard("americanfootball_nfl", params)
    clock[0] += secondary._SCOREBOARD_CACHE_TTL_SECONDS - 1
    recent = secondary._scoreboard("americanfootball_nfl", params)
    assert first.ok and recent.ok and first.data == recent.data
    assert len(observed) == 1

    clock[0] += 2
    refreshed = secondary._scoreboard("americanfootball_nfl", params)
    assert refreshed.ok is True
    assert refreshed.data["events"][0]["id"] == "2"
    assert len(observed) == 2


def test_stale_success_does_not_survive_refresh_failure(monkeypatch):
    observed = []
    clock = [100.0]

    def fake_http_json(_url, _params=None):
        observed.append(1)
        if len(observed) == 1:
            return secondary.SecondaryResult(True, {"events": [_event(1)]}, 200)
        return secondary.SecondaryResult(False, status=503, code="ESPN_HTTP_503")

    monkeypatch.setattr(secondary, "_http_json", fake_http_json)
    monkeypatch.setattr(secondary, "monotonic", lambda: clock[0])
    secondary._SCOREBOARD_CACHE.clear()
    params = {"commenceTimeFrom": "2026-10-04", "commenceTimeTo": "2026-10-04"}
    assert secondary._scoreboard("americanfootball_nfl", params).ok is True
    clock[0] += secondary._SCOREBOARD_CACHE_TTL_SECONDS + 1
    failure = secondary._scoreboard("americanfootball_nfl", params)
    assert failure.ok is False
    assert failure.code == "ESPN_HTTP_503"
    assert len(observed) == 2
