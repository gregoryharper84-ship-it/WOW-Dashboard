import json

import pytest

from v17 import market_evidence_sources as sources
from v17.rundown_market_ingestor import collect_snapshot, refresh_catalogs


class FakeTable:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def upsert(self, rows, **kwargs):
        if isinstance(rows, dict):
            rows = [rows]
        self.client.writes.setdefault(self.name, []).extend(rows)
        return self

    def execute(self):
        return object()


class FakeClient:
    def __init__(self):
        self.writes = {}

    def table(self, name):
        return FakeTable(self, name)


class FakeResponse:
    def __init__(self, payload, *, headers=None):
        self.payload = payload
        self.headers = headers or {}
        self.status = 200
        self.code = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _events(cursor="123"):
    return {
        "meta": {"delta_last_id": cursor},
        "events": [
            {
                "event_id": "evt-1",
                "event_date": "2026-09-23T00:10:00Z",
                "markets": [
                    {
                        "market_id": 1,
                        "name": "moneyline",
                        "participants": [
                            {
                                "participant_id": "home-1",
                                "name": "Home",
                                "type": "home",
                                "lines": [
                                    {
                                        "line_id": "h1",
                                        "selection": "Home",
                                        "prices": {
                                            "19": {
                                                "affiliate_name": "Pinnacle",
                                                "price": -125,
                                                "updated_at": "2026-09-22T23:59:50Z",
                                            }
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("THERUNDOWN_API_KEY", "test-only")


def _opener(*, delay=None, cursor="123"):
    def open_request(request, timeout=None):
        if request.full_url.endswith("/api/v2/sports"):
            return FakeResponse({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
        if request.full_url.endswith("/api/v2/markets"):
            return FakeResponse([{"market_id": 1, "market_name": "moneyline"}])
        if request.full_url.endswith("/api/v2/affiliates"):
            return FakeResponse([
                {"affiliate_id": 19, "affiliate_name": "Pinnacle", "active": True},
                {"affiliate_id": 27, "affiliate_name": "Retired", "active": True},
            ])
        if "/api/v2/sports/3/events/" in request.full_url:
            headers = {} if delay is None else {"X-Data-Delay-Seconds": str(delay), "X-Datapoints": "9"}
            return FakeResponse(_events(cursor), headers=headers)
        raise AssertionError(request.full_url)
    return open_request


def test_catalog_refresh_discovers_ids_and_marks_retired_affiliate_inactive():
    client = FakeClient()
    result = refresh_catalogs(client, opener=_opener(), pause_seconds=0)
    assert result["status"] == "COMPLETE"
    rows = client.writes["wow_market_provider_catalog"]
    assert len(rows) == 4
    retired = next(row for row in rows if row["provider_id"] == "27")
    assert retired["active"] is False
    assert all(row["can_execute"] is False for row in rows)


def test_delayed_account_stays_snapshot_even_with_cursor():
    client = FakeClient()
    result = collect_snapshot(
        client,
        sport_key="baseball_mlb",
        slate_date="2026-09-22",
        market_ids=("1",),
        affiliate_ids=("19",),
        opener=_opener(delay=60),
    )
    assert result["status"] == "COMPLETE"
    assert result["delta_eligible"] is False
    assert result["next_acquisition_mode"] == "SNAPSHOT"
    assert result["delta_cursor"] is None


def test_zero_delay_and_positive_cursor_are_both_required_for_delta_eligibility():
    client = FakeClient()
    result = collect_snapshot(
        client,
        sport_key="baseball_mlb",
        slate_date="2026-09-22",
        market_ids=("1",),
        affiliate_ids=("19", "27"),
        opener=_opener(delay=0, cursor="456"),
    )
    assert result["delta_eligible"] is True
    assert result["next_acquisition_mode"] == "DELTA"
    assert result["delta_cursor"] == 456
    state = client.writes["wow_market_feed_sync_state"][-1]
    assert state["affiliate_ids"] == ["19"]
    assert state["can_execute"] is False


def test_missing_delay_header_fails_closed_to_snapshot_mode():
    client = FakeClient()
    result = collect_snapshot(
        client,
        sport_key="baseball_mlb",
        slate_date="2026-09-22",
        market_ids=("1",),
        affiliate_ids=("19",),
        opener=_opener(delay=None, cursor="456"),
    )
    assert result["data_delay_seconds"] is None
    assert result["delta_eligible"] is False
    assert result["next_acquisition_mode"] == "SNAPSHOT"
