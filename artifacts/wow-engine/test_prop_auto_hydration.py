from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from prop_auto_hydration import PropAutoHydrationError, auto_hydrate_prop_candidate


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeInsertQuery:
    def __init__(self, owner, payload):
        self.owner = owner
        self.payload = payload

    def execute(self):
        self.owner.inserted.append(self.payload)
        return SimpleNamespace(data=[{"source_snapshot_id": self.payload["source_snapshot_id"]}])


class FakeTable:
    def __init__(self, owner):
        self.owner = owner

    def insert(self, payload):
        return FakeInsertQuery(self.owner, payload)


class FakeClient:
    def __init__(self):
        self.inserted = []

    def table(self, name):
        assert name == "wow_prop_evidence_snapshots"
        return FakeTable(self)


def _request(*, start=None, source_snapshot_id=None, sport="MLB", stat_type="PITCHER_STRIKEOUTS"):
    start = start or datetime(2026, 8, 30, 2, 5, tzinfo=timezone.utc)
    return SimpleNamespace(
        event_id="MLB-2026-08-29-BAL-ATH",
        event_start_time=start.isoformat(),
        sport=sport,
        player="Test Pitcher",
        stat_type=stat_type,
        line=5.5,
        direction="MORE",
        source_snapshot_id=source_snapshot_id,
    )


def _game_splits(n=10):
    rows = []
    for i in range(n):
        rows.append(
            {
                "date": f"2026-08-{20-i:02d}",
                "opponent": {"abbreviation": f"T{i:02d}"},
                "stat": {
                    "gamesStarted": 1,
                    "inningsPitched": "6.1" if i % 2 else "6.0",
                    "strikeOuts": 4 + (i % 5),
                    "baseOnBalls": i % 3,
                    "earnedRuns": i % 4,
                },
            }
        )
    return rows


def _http_get_factory(*, starts=10, schedule_player_id=12345):
    calls = []

    def fake_get(url, *, params, timeout):
        calls.append((url, params, timeout))
        if url.endswith("/people/search"):
            return FakeResponse({"people": [{"id": 12345, "fullName": "Test Pitcher"}]})
        if url.endswith("/people/12345/stats"):
            return FakeResponse({"stats": [{"splits": _game_splits(starts)}]})
        if url.endswith("/schedule"):
            return FakeResponse(
                {
                    "dates": [
                        {
                            "games": [
                                {
                                    "gamePk": 999001,
                                    "gameDate": "2026-08-30T02:05:00Z",
                                    "status": {"detailedState": "Scheduled"},
                                    "venue": {"name": "Test Park"},
                                    "teams": {
                                        "home": {
                                            "team": {"abbreviation": "BAL", "name": "Baltimore"},
                                            "probablePitcher": {"id": schedule_player_id, "fullName": "Test Pitcher"},
                                        },
                                        "away": {"team": {"abbreviation": "ATH", "name": "Athletics"}},
                                    },
                                }
                            ]
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected URL {url}")

    return fake_get, calls


def test_auto_hydration_builds_exact_current_contract_and_writes_snapshot():
    req = _request()
    client = FakeClient()
    http_get, calls = _http_get_factory()
    now = datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc)

    result = auto_hydrate_prop_candidate(
        req,
        client=client,
        http_get=http_get,
        now=now,
        board_source="PrizePicks screenshot",
        board_capture="2026-08-29T19:59:00+00:00",
    )

    assert result["code"] == "PROP_AUTO_HYDRATION_WRITTEN"
    assert result["provider"] == "MLB_STATS_API_OFFICIAL_V1"
    assert result["official_game_pk"] == 999001
    assert result["starter_status"] == "STARTER_PROBABLE_OFFICIAL_SCHEDULE"
    assert result["historical_start_count"] == 10
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False

    assert len(client.inserted) == 1
    evidence = client.inserted[0]
    assert evidence["event_id"] == req.event_id
    assert evidence["sport"] == "MLB"
    assert evidence["player"] == "Test Pitcher"
    assert evidence["stat_type"] == "PITCHER_STRIKEOUTS"
    assert evidence["line"] == 5.5
    assert len(evidence["game_log"]) == 10
    assert len(evidence["box_score_log"]) == 10
    assert evidence["game_log"][0] == evidence["box_score_log"][0]["so"]
    assert evidence["box_score_log"][1]["outs"] == 19
    assert evidence["role_status"]["status"] == "STARTER_PROBABLE_OFFICIAL_SCHEDULE"
    assert evidence["opportunity_ledger"]["status"] == "READY"
    assert evidence["source_timestamps"]["board_source"] == "PrizePicks screenshot"
    assert evidence["hydration_status"] == "PASS"
    assert evidence["blockers"] == []
    assert evidence["can_execute"] is False
    assert len(calls) == 3


def test_auto_hydration_rejects_unsupported_route_without_network_or_write():
    req = _request(sport="WNBA", stat_type="REB")
    client = FakeClient()
    http_get, calls = _http_get_factory()

    with pytest.raises(PropAutoHydrationError) as exc_info:
        auto_hydrate_prop_candidate(req, client=client, http_get=http_get)

    assert exc_info.value.code == "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE"
    assert calls == []
    assert client.inserted == []


def test_auto_hydration_requires_ten_official_prior_starts():
    req = _request()
    client = FakeClient()
    http_get, _calls = _http_get_factory(starts=9)

    with pytest.raises(PropAutoHydrationError) as exc_info:
        auto_hydrate_prop_candidate(
            req,
            client=client,
            http_get=http_get,
            now=datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc),
        )

    assert exc_info.value.code == "MLB_RECENT_STARTS_INSUFFICIENT"
    assert client.inserted == []


def test_auto_hydration_requires_official_probable_pitcher_match():
    req = _request()
    client = FakeClient()
    http_get, _calls = _http_get_factory(schedule_player_id=77777)

    with pytest.raises(PropAutoHydrationError) as exc_info:
        auto_hydrate_prop_candidate(
            req,
            client=client,
            http_get=http_get,
            now=datetime(2026, 8, 29, 20, 0, tzinfo=timezone.utc),
        )

    assert exc_info.value.code == "MLB_STARTER_STATUS_UNRESOLVED"
    assert client.inserted == []


def test_auto_hydration_never_creates_pregame_evidence_after_start():
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    req = _request(start=start)
    client = FakeClient()
    http_get, calls = _http_get_factory()

    with pytest.raises(PropAutoHydrationError) as exc_info:
        auto_hydrate_prop_candidate(req, client=client, http_get=http_get)

    assert exc_info.value.code == "EVENT_ALREADY_STARTED"
    assert calls == []
    assert client.inserted == []
