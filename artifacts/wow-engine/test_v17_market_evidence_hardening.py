from __future__ import annotations

from datetime import datetime, timezone

from v17 import market_evidence_hardening as hardening
from v17 import market_evidence_native_live as live
from v17 import market_evidence_snapshot as snapshot
from v17 import market_evidence_sources as sources

NOW = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)


def _marker(provider: str, capability: str = "events") -> dict:
    return {
        "provider": f"{provider}_MARKET_EVIDENCE",
        "provider_detail": capability,
        "source_class": "SPORTSBOOK_FEED",
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }


def _event(provider: str, price: int, *, book="Pinnacle", point=None, updated="2026-09-14T19:58:00Z", capability="events") -> dict:
    outcome = {"name": "Texas Rangers", "price": price}
    away = {"name": "Houston Astros", "price": 110}
    if point is not None:
        outcome["point"] = point
        away["point"] = -point
    market = "h2h" if point is None else "spreads"
    return {
        "id": f"{provider}-e1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-14T20:05:00Z",
        "home_team": "Texas Rangers",
        "away_team": "Houston Astros",
        "_wow_market_evidence": _marker(provider, capability),
        "bookmakers": [{
            "key": f"{provider.lower()}_{book.lower()}",
            "title": book,
            "last_update": updated,
            "markets": [{"key": market, "last_update": updated, "outcomes": [outcome, away]}],
        }],
    }


def _ok(provider: str, events: list[dict]) -> sources.MarketEvidenceResult:
    return sources.MarketEvidenceResult(
        True, provider, "events", data=events, status=200,
        code="MARKET_EVIDENCE_NORMALISED", observed_at="2026-09-14T20:00:00Z",
    )


def _fail(provider: str, status: int) -> sources.MarketEvidenceResult:
    return sources.MarketEvidenceResult(
        False, provider, "events", data=None, status=status,
        code=f"{provider}_HTTP_{status}", observed_at="2026-09-14T20:00:00Z",
    )


def test_partial_provider_429_preserves_other_provider_and_never_becomes_model_unavailable(monkeypatch):
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_429_RETRIES", "1")
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_429_BACKOFF_SECONDS", "0")
    sharp_calls = {"count": 0}
    rundown_calls = {"count": 0}

    def sharp(*args, **kwargs):
        sharp_calls["count"] += 1
        return _ok("SHARPAPI", [_event("SHARPAPI", -120)])

    def rundown(*args, **kwargs):
        rundown_calls["count"] += 1
        return _fail("RUNDOWN", 429)

    monkeypatch.setattr(snapshot.live, "sharpapi_market_evidence", sharp)
    monkeypatch.setattr(snapshot.live, "rundown_market_evidence", rundown)
    payload = snapshot.collect(["baseball_mlb"], dates=["2026-09-14"])

    assert payload["status"] == "MARKET_EVIDENCE_CAPTURED"
    assert payload["provider_capture"]["SHARPAPI"] > 0
    assert payload["provider_capture"]["RUNDOWN"] == 0
    assert sharp_calls["count"] == 1
    assert rundown_calls["count"] == 4  # events + openers, each retried once
    assert all(row["degradation_class"] == "RATE_LIMITED" for row in payload["provider_degradation"]["RUNDOWN"])
    assert "MODEL_UNAVAILABLE" not in str(payload)
    assert payload["affects_fitted_model_availability"] is False
    assert payload["can_execute"] is False


def test_auth_rejection_is_fail_closed_and_is_not_retried(monkeypatch):
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_429_RETRIES", "2")
    monkeypatch.setenv("WOW_MARKET_EVIDENCE_429_BACKOFF_SECONDS", "0")
    sharp_calls = {"count": 0}

    def sharp(*args, **kwargs):
        sharp_calls["count"] += 1
        return _fail("SHARPAPI", 401)

    monkeypatch.setattr(snapshot.live, "sharpapi_market_evidence", sharp)
    monkeypatch.setattr(snapshot.live, "rundown_market_evidence", lambda *a, **k: _ok("RUNDOWN", [_event("RUNDOWN", -118)]))
    payload = snapshot.collect(["baseball_mlb"], dates=["2026-09-14"])

    assert sharp_calls["count"] == 1
    assert payload["provider_degradation"]["SHARPAPI"][0]["degradation_class"] == "AUTH_REJECTED"
    assert payload["status"] == "MARKET_EVIDENCE_CAPTURED"
    assert payload["can_execute"] is False


def test_disagreement_alert_requires_same_event_book_market_outcome_and_line():
    alerts = hardening.disagreement_alerts([
        _event("RUNDOWN", -200),
        _event("SHARPAPI", -120),
    ], threshold=0.02)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["code"] == "MARKET_SOURCE_DISAGREEMENT"
    assert alert["bookmaker"] == "Pinnacle"
    assert alert["market_key"] == "h2h"
    assert alert["providers"] == ["RUNDOWN", "SHARPAPI"]
    assert alert["model_probability_mutated"] is False
    assert alert["prediction_authority"] is False
    assert alert["can_execute"] is False

    assert hardening.disagreement_alerts([
        _event("RUNDOWN", -200, book="Pinnacle"),
        _event("SHARPAPI", -120, book="DraftKings"),
    ], threshold=0.02) == []
    assert hardening.disagreement_alerts([
        _event("RUNDOWN", -200, point=-1.5),
        _event("SHARPAPI", -120, point=-2.5),
    ], threshold=0.02) == []


def test_snapshot_freshness_counts_current_stale_unknown_and_historical_rows():
    events = [
        _event("SHARPAPI", -120, updated="2026-09-14T19:58:00Z"),
        _event("RUNDOWN", -118, updated="2026-09-14T19:30:00Z"),
        _event("RUNDOWN", -115, updated="2026-09-14T18:00:00Z", capability="openers"),
    ]
    report = hardening.analyze_snapshot_events(events, now=NOW)
    assert report["counts"]["FRESH"] == 2
    assert report["counts"]["STALE"] == 2
    assert report["counts"]["HISTORICAL_OPENER"] == 2
    assert report["current_rows_usable"] == 2
    assert report["current_rows_stale_or_unknown"] == 2
    assert report["historical_opener_rows"] == 2
    assert report["prediction_authority"] is False
    assert report["can_execute"] is False


def test_sharpapi_live_adapter_handles_observed_fields_all_books_list_and_mapping():
    rows = [
        {
            "event_id": "sharp-1",
            "home_team": "Texas Rangers",
            "away_team": "Houston Astros",
            "event_start_time": "2026-09-14T20:05:00Z",
            "market_type": "moneyline",
            "selection_type": "Texas Rangers",
            "sportsbook": "Pinnacle",
            "odds_american": -120,
            "timestamp": "2026-09-14T19:58:00Z",
            "all_books": [
                {"sportsbook": "DraftKings", "odds_american": -118},
                {"sportsbook": "FanDuel", "odds_american": -119},
            ],
        },
        {
            "event_id": "sharp-1",
            "home_team": "Texas Rangers",
            "away_team": "Houston Astros",
            "event_start_time": "2026-09-14T20:05:00Z",
            "market_type": "spread",
            "selection": "Texas Rangers",
            "sportsbook": "Pinnacle",
            "odds_american": 130,
            "point": -1.5,
            "all_books": {"betmgm": {"odds_american": 125, "point": -1.5}},
        },
    ]
    events = live.sharpapi_rows_to_odds_api_v4(rows, sport_key="baseball_mlb")
    assert len(events) == 1
    event = events[0]
    titles = {book["title"] for book in event["bookmakers"]}
    assert {"Pinnacle", "DraftKings", "FanDuel", "betmgm"}.issubset(titles)
    assert event["commence_time"] == "2026-09-14T20:05:00Z"
    assert event["home_team"] == "Texas Rangers"
    assert event["away_team"] == "Houston Astros"
    assert all("probability" not in outcome for book in event["bookmakers"] for market in book["markets"] for outcome in market["outcomes"])


def test_sharpapi_live_adapter_handles_nested_event_and_nested_data_container():
    row = {
        "event": {
            "id": "nested-1",
            "home_team": "Texas Rangers",
            "away_team": "Houston Astros",
            "start_time": "2026-09-14T20:05:00Z",
        },
        "market": "moneyline",
        "selection": "Texas Rangers",
        "sportsbook": "Pinnacle",
        "odds": -120,
    }
    assert live._candidate_events({"data": {"odds": [row]}}) == [row]
    event = live.sharpapi_rows_to_odds_api_v4([row], sport_key="baseball_mlb")[0]
    assert event["id"] == "sharpapi-nested-1"


def test_rundown_live_adapter_uses_participant_lines_and_rejects_off_board_sentinel():
    raw = {
        "event_id": "rd-1",
        "event_date": "2026-09-14T20:05:00Z",
        "participants": [
            {"id": 1, "name": "Texas Rangers", "type": "home"},
            {"id": 2, "name": "Houston Astros", "type": "away"},
        ],
        "markets": [{
            "market_id": 1,
            "name": "moneyline",
            "participants": [
                {"id": 1, "name": "Texas Rangers", "type": "home", "lines": [{"prices": {
                    "3": {"affiliate_name": "Pinnacle", "price": -120, "updated_at": "2026-09-14T19:58:00Z"},
                    "22": {"affiliate_name": "BetMGM", "price": 0.0001},
                }}]},
                {"id": 2, "name": "Houston Astros", "type": "away", "lines": [{"prices": {
                    "3": {"affiliate_name": "Pinnacle", "price": 110, "updated_at": "2026-09-14T19:58:00Z"},
                }}]},
            ],
        }],
    }
    event = live.rundown_v2_event_to_odds_api_v4(raw, sport_key="baseball_mlb")
    assert event is not None
    assert event["home_team"] == "Texas Rangers"
    assert event["away_team"] == "Houston Astros"
    assert {book["title"] for book in event["bookmakers"]} == {"Pinnacle"}
    outcomes = event["bookmakers"][0]["markets"][0]["outcomes"]
    assert {(row["name"], row["price"]) for row in outcomes} == {("Texas Rangers", -120), ("Houston Astros", 110)}


def test_unknown_or_malformed_live_shapes_fail_closed_without_probability():
    assert live.sharpapi_rows_to_odds_api_v4([{"sportsbook": "x", "probability": 0.9}]) == []
    assert live.rundown_v2_event_to_odds_api_v4({"event_id": "x", "markets": [{"name": "player_points"}]}) is None
    assert hardening.provider_degradation("SHARPAPI_SCHEMA_UNRECOGNISED") == "SCHEMA_UNRECOGNISED"
    assert hardening.provider_degradation("RUNDOWN_HTTP_429", 429) == "RATE_LIMITED"
    assert hardening.provider_degradation("RUNDOWN_HTTP_401", 401) == "AUTH_REJECTED"
