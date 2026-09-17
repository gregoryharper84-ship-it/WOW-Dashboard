from datetime import datetime, timedelta, timezone

from v17 import rundown_board_primary_v2 as board

CALLS = []


def _priced_event_payload():
    return {
        "events": [
            {
                "event_id": 123,
                "event_date": "2026-09-17T18:00:00Z",
                "teams": [
                    {"name": "Away Club", "side": "away"},
                    {"name": "Home Club", "side": "home"},
                ],
                "markets": [
                    {
                        "market_id": 1,
                        "name": "Moneyline",
                        "participants": [
                            {"name": "Away Club", "lines": [{"prices": {"19": {"price": 120, "affiliate_name": "Book A", "updated_at": "2026-09-17T12:00:00Z"}}}]},
                            {"name": "Home Club", "lines": [{"prices": {"19": {"price": -140, "affiliate_name": "Book A", "updated_at": "2026-09-17T12:00:00Z"}}}]},
                        ],
                    },
                    {
                        "market_id": 900,
                        "name": "Pitcher Strikeouts",
                        "type": "player_prop",
                        "participants": [
                            {"name": "Jane Pitcher", "lines": [{"value": 5.5, "selection": "Over", "prices": {"19": {"price": -110, "affiliate_name": "Book A", "updated_at": "2026-09-17T12:00:00Z"}}}]},
                        ],
                    },
                ],
            }
        ]
    }


def _core_event_payload():
    return {
        "events": [
            {
                "event_id": 123,
                "event_date": "2026-09-17T18:00:00Z",
                "teams": [
                    {"name": "Away Club", "side": "away"},
                    {"name": "Home Club", "side": "home"},
                ],
                "markets": [],
            }
        ]
    }


def _fake_api(path, params=None):
    CALLS.append((path, dict(params or {})))
    if path == "/api/v2/sports":
        return board.BoardResult(True, [{"sport_id": 3, "sport_name": "MLB"}], 200)
    if path == "/api/v2/affiliates":
        return board.BoardResult(True, [{"affiliate_id": 19, "name": "Book A"}], 200)
    if path == "/api/v2/events/123/markets":
        return board.BoardResult(True, [
            {"market_id": 1, "name": "Moneyline"},
            {"market_id": 900, "name": "Pitcher Strikeouts", "type": "player_prop"},
        ], 200)
    if path.startswith("/api/v2/sports/3/events/"):
        if params and params.get("market_ids"):
            assert len(str(params["market_ids"]).split(",")) <= 12
            return board.BoardResult(True, _priced_event_payload(), 200)
        return board.BoardResult(True, _core_event_payload(), 200)
    raise AssertionError(path)


def test_board_primary_discovers_active_markets_then_batches_prices(monkeypatch):
    CALLS.clear()
    board.reset_cache()
    monkeypatch.setattr(board, "_api_get", _fake_api)
    monkeypatch.setenv("WOW_RUNDOWN_BOARD_PRIMARY_ENABLED", "true")

    sports = board.serve("/odds-api/v4/sports", {"all": "true"})
    assert sports.ok is True
    assert sports.data[0]["key"] == "baseball_mlb"

    now = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
    events = board.serve(
        "/odds-api/v4/sports/baseball_mlb/events",
        {
            "commenceTimeFrom": now.isoformat().replace("+00:00", "Z"),
            "commenceTimeTo": (now + timedelta(hours=12)).isoformat().replace("+00:00", "Z"),
        },
    )
    assert events.ok is True
    assert len(events.data) == 1
    event = events.data[0]
    assert event["id"] == "rundown-123"
    assert event["_wow_market_evidence"]["prediction_authority"] is False
    assert event["_wow_market_evidence"]["can_execute"] is False
    assert event["_wow_rundown_board_audit"]["coverage_complete"] is True
    assert event["_wow_rundown_board_audit"]["available_market_count"] == 2
    assert event["_wow_rundown_board_audit"]["priced_market_count"] == 2

    assert any(path == "/api/v2/events/123/markets" for path, _ in CALLS)
    priced_calls = [(path, params) for path, params in CALLS if path.startswith("/api/v2/sports/3/events/") and params.get("market_ids")]
    assert priced_calls
    assert all(len(params["market_ids"].split(",")) <= 12 for _, params in priced_calls)
    assert board.MARKET_CHUNK_SIZE <= 12

    inventory = board.serve(
        "/odds-api/v4/sports/baseball_mlb/events/rundown-123/markets",
        {"regions": "us"},
    )
    keys = {m["key"] for b in inventory.data["bookmakers"] for m in b["markets"]}
    assert "h2h" in keys
    assert "player_pitcher_strikeouts" in keys

    odds = board.serve(
        "/odds-api/v4/sports/baseball_mlb/events/rundown-123/odds",
        {"markets": "player_pitcher_strikeouts"},
    )
    filtered = {m["key"] for b in odds.data["bookmakers"] for m in b["markets"]}
    assert filtered == {"player_pitcher_strikeouts"}


def test_board_primary_auth_failure_is_typed_and_fail_closed(monkeypatch):
    board.reset_cache()
    monkeypatch.delenv("THERUNDOWN_API_KEY", raising=False)
    monkeypatch.delenv("RUNDOWN_API_KEY", raising=False)
    monkeypatch.delenv("WOW_RUNDOWN_API_KEY", raising=False)
    monkeypatch.setattr(board, "REQUEST_DELAY_SECONDS", 0.0)
    result = board._api_get("/api/v2/sports")
    assert result.ok is False
    assert result.status == 401
    assert result.code == "RUNDOWN_BOARD_AUTH_UNCONFIGURED"
    assert board.CAN_EXECUTE is False


def test_unknown_sports_are_exposed_not_silently_dropped(monkeypatch):
    board.reset_cache()
    monkeypatch.setattr(
        board,
        "_api_get",
        lambda path, params=None: board.BoardResult(True, [{"sport_id": 77, "sport_name": "New Sport"}], 200),
    )
    sports = board.sports_catalog()
    assert sports.ok is True
    assert sports.data[0]["key"] == "rundown_77_new_sport"
    assert sports.data[0]["_wow_market_evidence"]["can_execute"] is False


def test_incomplete_board_coverage_fails_closed(monkeypatch):
    board.reset_cache()

    def incomplete(path, params=None):
        if path == "/api/v2/sports":
            return board.BoardResult(True, [{"sport_id": 3, "sport_name": "MLB"}], 200)
        if path == "/api/v2/affiliates":
            return board.BoardResult(True, [{"affiliate_id": 19}], 200)
        if path == "/api/v2/events/123/markets":
            return board.BoardResult(False, status=403, code="RUNDOWN_BOARD_AUTH_OR_ENTITLEMENT_REJECTED")
        if path.startswith("/api/v2/sports/3/events/"):
            return board.BoardResult(True, _core_event_payload(), 200)
        raise AssertionError(path)

    monkeypatch.setattr(board, "_api_get", incomplete)
    result = board.serve(
        "/odds-api/v4/sports/baseball_mlb/events",
        {"commenceTimeFrom": "2026-09-17T12:00:00Z", "commenceTimeTo": "2026-09-17T20:00:00Z"},
    )
    assert result.ok is False
    assert result.code == "RUNDOWN_BOARD_COVERAGE_INCOMPLETE"
    assert result.data["audit"]["coverage_complete"] is False
