"""Contract fixtures built from each provider's *documented native* structure.

The tests in ``test_v17_market_evidence_sources.py`` exercise the module's own
shapes. These exercise the shapes the providers actually return, which is the
gap that let an Odds-API-v4-assuming normaliser pass a green suite:

- TheRundown **V2**: ``event -> markets[] -> participants[] -> lines[] ->
  prices{affiliate_id}`` (docs.therundown.io data model; the V2 events and
  openers endpoints both return it).
- TheRundown **V1** (legacy): ``event.lines{affiliate_id}.{moneyline,spread,total}``.
- **SharpAPI**: row-major records keyed on sportsbook / event / market /
  selection / odds / line, plus the line-shopping variant that nests competing
  books under ``all_books``.

Each fixture asserts the adapter produces the internal Odds-API-v4 interchange
shape, so the provider disappears at the adapter boundary and everything
downstream sees one representation.
"""
from __future__ import annotations

import pytest

from v17 import market_evidence_sources as sources
from v17.nightly_multiscout import bookmaker_rows

CUBS = "Chicago Cubs"
BREWERS = "Milwaukee Brewers"


# ---------------------------------------------------------------------------
# TheRundown V2 native structure
# ---------------------------------------------------------------------------

def rundown_v2_event() -> dict:
    """One V2 event carrying moneyline, spread and total across two affiliates."""
    return {
        "event_id": "v2-evt-1",
        "event_date": "2026-09-14T23:05:00Z",
        "participants": [
            {"id": 101, "name": CUBS, "is_home": True, "is_away": False},
            {"id": 102, "name": BREWERS, "is_home": False, "is_away": True},
        ],
        "markets": [
            {
                "market_id": 3,
                "name": "moneyline",
                "lines": [
                    {"participant_id": 101, "prices": {
                        "3": {"affiliate_name": "Pinnacle", "american": -135, "date_updated": "2026-09-14T18:00:00Z"},
                        "9": {"affiliate_name": "Circa Sports", "american": -130},
                    }},
                    {"participant_id": 102, "prices": {
                        "3": {"affiliate_name": "Pinnacle", "american": 115},
                        "9": {"affiliate_name": "Circa Sports", "american": 112},
                    }},
                ],
            },
            {
                "market_id": 1,
                "name": "spread",
                "lines": [
                    {"participant_id": 101, "spread": -1.5, "prices": {"3": {"affiliate_name": "Pinnacle", "american": 140}}},
                    {"participant_id": 102, "spread": 1.5, "prices": {"3": {"affiliate_name": "Pinnacle", "american": -165}}},
                ],
            },
            {
                "market_id": 2,
                "name": "total",
                "lines": [
                    {"selection": "over", "total": 8.5, "prices": {"3": {"affiliate_name": "Pinnacle", "american": -105}}},
                    {"selection": "under", "total": 8.5, "prices": {"3": {"affiliate_name": "Pinnacle", "american": -115}}},
                ],
            },
        ],
    }


def test_rundown_v2_native_structure_becomes_odds_api_v4():
    event = sources.rundown_v2_event_to_odds_api_v4(rundown_v2_event(), sport_key="baseball_mlb")
    assert event is not None, "V2 payload must not fall through to the V1 adapter"
    assert event["id"] == "rundown-v2-evt-1"
    assert event["home_team"] == CUBS
    assert event["away_team"] == BREWERS
    assert event["commence_time"] == "2026-09-14T23:05:00Z"

    books = {book["title"]: book for book in event["bookmakers"]}
    assert set(books) == {"Pinnacle", "Circa Sports"}

    pinnacle = {m["key"]: m for m in books["Pinnacle"]["markets"]}
    assert set(pinnacle) == {"h2h", "spreads", "totals"}
    assert {(o["name"], o["price"]) for o in pinnacle["h2h"]["outcomes"]} == {(CUBS, -135), (BREWERS, 115)}
    assert {(o["name"], o["point"]) for o in pinnacle["spreads"]["outcomes"]} == {(CUBS, -1.5), (BREWERS, 1.5)}
    assert {(o["name"], o["point"]) for o in pinnacle["totals"]["outcomes"]} == {("Over", 8.5), ("Under", 8.5)}

    circa = {m["key"]: m for m in books["Circa Sports"]["markets"]}
    assert set(circa) == {"h2h"}, "a book quoting only one market must not inherit another book's markets"


def test_rundown_v2_flows_through_the_public_normaliser():
    result = sources.normalize_market_payload(
        {"events": [rundown_v2_event()]}, provider="RUNDOWN", capability="events", sport_key="baseball_mlb",
    )
    assert result.ok, result.code
    assert result.code == "MARKET_EVIDENCE_NORMALISED"
    rows = bookmaker_rows(result.data[0])
    assert {row["market_key"] for row in rows} == {"h2h", "spreads", "totals"}
    assert all(row["price"] is not None for row in rows)


def test_rundown_v2_openers_payload_uses_the_same_adapter():
    payload = {"events": [rundown_v2_event()]}
    result = sources.normalize_market_payload(
        payload, provider="RUNDOWN", capability="openers", sport_key="baseball_mlb",
    )
    assert result.ok
    assert result.data[0]["_wow_market_evidence"]["provider_detail"] == "openers"


def test_rundown_v1_legacy_structure_still_translates():
    v1 = {
        "event_id": "v1-evt-1",
        "event_date": "2026-09-14T23:05:00Z",
        "teams_normalized": [
            {"name": CUBS, "is_home": True, "is_away": False},
            {"name": BREWERS, "is_home": False, "is_away": True},
        ],
        "lines": {"3": {
            "affiliate": {"affiliate_id": 3, "affiliate_name": "Pinnacle"},
            "moneyline": {"moneyline_home": -135, "moneyline_away": 115},
        }},
    }
    result = sources.normalize_market_payload({"events": [v1]}, provider="RUNDOWN", capability="events")
    assert result.ok
    assert result.data[0]["id"] == "rundown-v1-evt-1"


def test_rundown_market_ids_can_be_pinned_when_names_are_absent(monkeypatch):
    payload = rundown_v2_event()
    for market in payload["markets"]:
        market.pop("name")
    assert sources.rundown_v2_event_to_odds_api_v4(payload) is None

    monkeypatch.setenv("WOW_RUNDOWN_MARKET_ID_MAP_JSON", '{"3": "h2h", "1": "spreads", "2": "totals"}')
    event = sources.rundown_v2_event_to_odds_api_v4(payload)
    assert event is not None
    keys = {m["key"] for book in event["bookmakers"] for m in book["markets"]}
    assert keys == {"h2h", "spreads", "totals"}


# ---------------------------------------------------------------------------
# SharpAPI native structure
# ---------------------------------------------------------------------------

def sharpapi_rows() -> list[dict]:
    """Row-major records: one sportsbook/event/market/selection per row."""
    event = {"id": "sa-evt-1", "home_team": CUBS, "away_team": BREWERS, "start_time": "2026-09-14T23:05:00Z"}
    return [
        {"sportsbook": "Pinnacle", "event": event, "market": "moneyline", "selection": CUBS, "odds": -134},
        {"sportsbook": "Pinnacle", "event": event, "market": "moneyline", "selection": BREWERS, "odds": 116},
        {"sportsbook": "DraftKings", "event": event, "market": "moneyline", "selection": CUBS, "odds": -140},
        {"sportsbook": "DraftKings", "event": event, "market": "moneyline", "selection": BREWERS, "odds": 120},
        {"sportsbook": "Pinnacle", "event": event, "market": "spread", "selection": CUBS, "odds": 141, "line": -1.5},
        {"sportsbook": "Pinnacle", "event": event, "market": "total", "selection": "Over", "odds": -106, "line": 8.5},
    ]


def test_sharpapi_row_major_odds_become_one_multi_book_event():
    events = sources.sharpapi_rows_to_odds_api_v4(sharpapi_rows(), sport_key="baseball_mlb")
    assert len(events) == 1, "rows for one game must collapse into one event, not one event per row"
    event = events[0]
    assert event["id"] == "sharpapi-sa-evt-1"
    assert event["home_team"] == CUBS
    assert event["away_team"] == BREWERS

    books = {book["title"]: book for book in event["bookmakers"]}
    assert set(books) == {"Pinnacle", "DraftKings"}
    pinnacle = {m["key"]: m for m in books["Pinnacle"]["markets"]}
    assert set(pinnacle) == {"h2h", "spreads", "totals"}
    assert {(o["name"], o["price"]) for o in pinnacle["h2h"]["outcomes"]} == {(CUBS, -134), (BREWERS, 116)}
    assert {m["key"] for m in books["DraftKings"]["markets"]} == {"h2h"}


def test_sharpapi_line_shopping_all_books_expands_into_bookmakers():
    rows = [{
        "event": {"id": "sa-evt-2", "home_team": CUBS, "away_team": BREWERS},
        "market": "moneyline",
        "selection": CUBS,
        "sportsbook": "Pinnacle",
        "odds": -134,
        "all_books": [
            {"sportsbook": "BetMGM", "odds": -138},
            {"sportsbook": "Circa Sports", "odds": -131},
        ],
    }]
    event = sources.sharpapi_rows_to_odds_api_v4(rows, sport_key="baseball_mlb")[0]
    assert {book["title"] for book in event["bookmakers"]} == {"Pinnacle", "BetMGM", "Circa Sports"}
    prices = {
        book["title"]: book["markets"][0]["outcomes"][0]["price"] for book in event["bookmakers"]
    }
    assert prices == {"Pinnacle": -134, "BetMGM": -138, "Circa Sports": -131}


def test_sharpapi_all_books_mapping_form_also_expands():
    rows = [{
        "event_id": "sa-evt-3", "home_team": CUBS, "away_team": BREWERS,
        "market": "moneyline", "selection": CUBS, "sportsbook": "Pinnacle", "odds": -134,
        "all_books": {"betmgm": {"odds": -138}, "circa": {"odds": -131}},
    }]
    event = sources.sharpapi_rows_to_odds_api_v4(rows, sport_key="baseball_mlb")[0]
    assert {book["title"] for book in event["bookmakers"]} == {"Pinnacle", "betmgm", "circa"}


def test_sharpapi_rows_without_an_event_id_group_on_identity():
    rows = [
        {"sportsbook": "Pinnacle", "home_team": CUBS, "away_team": BREWERS, "start_time": "2026-09-14T23:05:00Z",
         "market": "moneyline", "selection": CUBS, "odds": -134},
        {"sportsbook": "Pinnacle", "home_team": CUBS, "away_team": BREWERS, "start_time": "2026-09-14T23:05:00Z",
         "market": "moneyline", "selection": BREWERS, "odds": 116},
    ]
    events = sources.sharpapi_rows_to_odds_api_v4(rows)
    assert len(events) == 1
    assert len(events[0]["bookmakers"][0]["markets"][0]["outcomes"]) == 2


def test_sharpapi_flows_through_the_public_normaliser():
    result = sources.normalize_market_payload(
        {"data": sharpapi_rows()}, provider="SHARPAPI", capability="odds", sport_key="baseball_mlb",
    )
    assert result.ok, result.code
    rows = bookmaker_rows(result.data[0])
    assert {row["bookmaker_title"] for row in rows} == {"Pinnacle", "DraftKings"}
    assert all(row["market_key"] in sources.CANONICAL_MARKET_KEYS for row in rows)


def test_sharpapi_rows_missing_a_price_or_market_are_dropped_not_defaulted():
    rows = [
        {"sportsbook": "Pinnacle", "event_id": "e", "home_team": CUBS, "away_team": BREWERS,
         "market": "moneyline", "selection": CUBS},                      # no price
        {"sportsbook": "Pinnacle", "event_id": "e", "home_team": CUBS, "away_team": BREWERS,
         "market": "player_points", "selection": CUBS, "odds": -110},    # unmapped market
    ]
    assert sources.sharpapi_rows_to_odds_api_v4(rows) == []


# ---------------------------------------------------------------------------
# Request-shaping contracts
# ---------------------------------------------------------------------------

def test_sharpapi_league_identifiers_are_explicit_and_pinnable(monkeypatch):
    assert sources.sharpapi_league("baseball_mlb") == "mlb"
    assert sources.sharpapi_league("americanfootball_ncaaf") == "ncaaf"
    assert sources.sharpapi_league("cricket_ipl") is None
    monkeypatch.setenv("WOW_SHARPAPI_LEAGUE_CRICKET_IPL", "ipl")
    assert sources.sharpapi_league("cricket_ipl") == "ipl"


def test_sharpapi_unmapped_sport_fails_closed_without_a_call(monkeypatch):
    monkeypatch.setattr(sources, "ENABLED", True)
    monkeypatch.setenv("SHARPAPI_API_KEY", "k")

    def must_not_call(request, timeout=None):
        raise AssertionError("unmapped sport must not reach the provider")

    result = sources.sharpapi_market_evidence("cricket_ipl", opener=must_not_call)
    assert result.code == "MARKET_EVIDENCE_UNSUPPORTED_SPORT"


@pytest.mark.parametrize(
    ("when", "expected"),
    [("2026-09-13T12:00:00+00:00", 300), ("2026-01-13T12:00:00+00:00", 360)],
)
def test_rundown_date_offset_tracks_the_local_slate_day(when, expected, monkeypatch):
    from datetime import datetime

    monkeypatch.delenv("WOW_RUNDOWN_DATE_OFFSET_MINUTES", raising=False)
    monkeypatch.setenv("WOW_USER_TIMEZONE", "America/Chicago")
    assert sources.rundown_date_offset_minutes(datetime.fromisoformat(when)) == expected


def test_rundown_date_offset_is_overridable(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_DATE_OFFSET_MINUTES", "0")
    assert sources.rundown_date_offset_minutes() == 0


# ---------------------------------------------------------------------------
# Failure boundary: evidence loss is not model loss
# ---------------------------------------------------------------------------

def test_market_failure_is_evidence_side_not_model_side(monkeypatch):
    from v17 import market_evidence_snapshot as snapshot

    monkeypatch.setattr(sources, "ENABLED", False)
    payload = snapshot.collect(["baseball_mlb"])
    assert payload["status"] == sources.MARKET_DATA_UNOBTAINABLE
    assert payload["affects_fitted_model_availability"] is False
    assert "MODEL_UNAVAILABLE" not in __import__("json").dumps(payload)


def test_acceptance_only_indicts_credentialed_providers(monkeypatch):
    from v17 import market_evidence_snapshot as snapshot

    for name in ("RUNDOWN_API_KEY", "WOW_RUNDOWN_API_KEY", "THERUNDOWN_API_KEY",
                 "SHARPAPI_API_KEY", "WOW_SHARPAPI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    payload = {"provider_capture": {"RUNDOWN": 0, "SHARPAPI": 0}}
    assert snapshot.acceptance_failures(payload) == []

    monkeypatch.setenv("RUNDOWN_API_KEY", "k")
    assert snapshot.acceptance_failures(payload) == ["RUNDOWN"]
    assert snapshot.acceptance_failures({"provider_capture": {"RUNDOWN": 4}}) == []
