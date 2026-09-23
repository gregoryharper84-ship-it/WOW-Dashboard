import json
from types import SimpleNamespace

import pytest

from v17 import market_evidence_sources as sources
from v17.rundown_market_ingestor import collect_snapshot, refresh_catalogs
from v17.rundown_market_ledger import price_observations_from_rundown_event


class _Execute:
    def __init__(self, value=None):
        self.data = value or []


class FakeTable:
    def __init__(self, client, name):
        self.client = client
        self.name = name

    def upsert(self, rows, **kwargs):
        if isinstance(rows, dict):
            rows = [rows]
        self.client.writes.setdefault(self.name, []).extend(rows)
        self.client.options.append((self.name, kwargs))
        return self

    def execute(self):
        return _Execute()


class FakeClient:
    def __init__(self):
        self.writes = {}
        self.options = []

    def table(self, name):
        return FakeTable(self, name)


class FakeResponse:
    def __init__(self, payload, *, headers=None, status=200):
        self.payload = payload
        self.headers = headers or {}
        self.status = status
        self.code = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _event_payload(cursor="123"):
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
                                        "is_main_line": True,
                                        "prices": {
                                            "19": {
                                                "affiliate_name": "Pinnacle",
                                                "price": -125,
                                                "updated_at": "2026-09-22T23:59:50Z",
                                            }
                                        },
                                    }
                                ],
                            },
                            {
                                "participant_id": "away-1",
                                "name": "Away",
                                "type": "away",
                                "lines": [
                                    {
                                        "line_id": "a1",
                                        "selection": "Away",
                                        "is_main_line": True,
                                        "prices": {
                                            "19": {
                                                "affiliate_name": "Pinnacle",
                                                "price": 110,
                                                "updated_at": "2026-09-22T23:59:50Z",
                                            }
                                        },
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        ],
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("THERUNDOWN_API_KEY", "not-a-real-key")
    monkeypatch.delenv("RUNDOWN_API_KEY", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_API_KEY", raising=False)


def test_reference_catalog_refresh_persists_all_three_and_retires_affiliate_27():
    client = FakeClient()

    def opener(request, timeout=None):
        url = request.full_url
        if url.endswith("/api/v2/sports"):
            return FakeResponse({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
        if url.endswith("/api/v2/markets"):
            return FakeResponse([{"id": 1, "name": "moneyline"}])
        if url.endswith("/api/v2/affiliates"):
            return FakeResponse([
                {"affiliate_id": 19, "affiliate_name": "Pinnacle", "active": True},
                {"affiliate_id": 27, "affiliate_name": "Retired", "active": True},
            ])
        raise AssertionError(url)

    result = refresh_catalogs(client, opener=opener, pause_seconds=0)
    assert result["status"] == "COMPLETE"
    rows = client.writes["wow_market_provider_catalog"]
    assert len(rows) == 4
    retired = next(row for row in rows if row["provider_id"] == "27")
    assert retired["active"] is False
    assert all(row["can_execute"] is False for row in rows)


def test_delayed_snapshot_never_seeds_delta_even_when_body_contains_cursor():
    client = FakeClient()

    def opener(request, timeout=None):
        if request.full_url.endswith("/api/v2/sports"):
            return FakeResponse({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
        if "/api/v2/sports/3/events/" in request.full_url:
            return FakeResponse(
                _event_payload(),
                headers={"X-Data-Delay-Seconds": "60", "X-Datapoints": "7"},
            )
        raise AssertionError(request.full_url)

    result = collect_snapshot(
        client,
        sport_key="baseball_mlb",
        slate_date="2026-09-22",
        market_ids=("1",),
        affiliate_ids=("19",),
        opener=opener,
    )
    assert result["status"] == "COMPLETE"
    assert result["next_acquisition_mode"] == "SNAPSHOT"
    assert result["delta_eligible"] is False
    assert result["delta_cursor"] is None
    state = client.writes["wow_market_feed_sync_state"][-1]
    assert state["data_delay_seconds"] == 60
    assert state["delta_cursor"] is None
    assert state["metadata"]["datapoints"] == 7


def test_zero_delay_and_positive_cursor_are_both_required_for_delta_eligibility():
    client = FakeClient()

    def opener(request, timeout=None):
        if request.full_url.endswith("/api/v2/sports"):
            return FakeResponse({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
        if "/api/v2/sports/3/events/" in request.full_url:
            return FakeResponse(
                _event_payload("456"),
                headers={"X-Data-Delay-Seconds": "0", "X-Datapoints": "9"},
            )
        raise AssertionError(request.full_url)

    result = collect_snapshot(
        client,
        sport_key="baseball_mlb",
        slate_date="2026-09-22",
        market_ids=("1", "2", "3"),
        affiliate_ids=("19", "27"),
        opener=opener,
    )
    assert result["delta_eligible"] is True
    assert result["next_acquisition_mode"] == "DELTA"
    assert result["delta_cursor"] == 456
    assert result["rows_written"] == 2
    state = client.writes["wow_market_feed_sync_state"][-1]
    assert state["affiliate_ids"] == ["19"]
    assert state["can_execute"] is False


def test_missing_delay_header_fails_closed_to_snapshot_mode():
    client = FakeClient()

    def opener(request, timeout=None):
        if request.full_url.endswith("/api/v2/sports"):
            return FakeResponse({"sports": [{"sport_id": 3, "sport_name": "MLB"}]})
        if "/api/v2/sports/3/events/" in request.full_url:
            return FakeResponse(_event_payload("456"), headers={})
        raise AssertionError(request.full_url)

    result = collect_snapshot(
        client,
        sport_key="baseball_mlb",
        slate_date="2026-09-22",
        market_ids=("1",),
        affiliate_ids=("19",),
        opener=opener,
    )
    assert result["data_delay_seconds"] is None
    assert result["delta_eligible"] is False
    assert result["next_acquisition_mode"] == "SNAPSHOT"


def test_off_board_sentinel_is_preserved_as_unavailable_state_not_probability_or_price():
    raw = _event_payload()["events"][0]
    raw["markets"][0]["participants"][0]["lines"][0]["prices"]["19"]["price"] = 0.0001
    rows = price_observations_from_rundown_event(
        raw,
        sport_key="baseball_mlb",
        snapshot_kind="CURRENT",
        fetched_at="2026-09-23T00:00:00Z",
    )
    home = next(row for row in rows if row["participant_id"] == "home-1")
    assert home["is_available"] is False
    assert home["american_odds"] is None
    assert home["decimal_odds"] is None
    assert home["prediction_authority"] is False
    assert home["can_execute"] is False
