import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from gate_engine.board_intake import normalize_row
from gate_engine import auto_enrichment
from services import odds_api, status as status_service


def _row(player="LeBron James", sport="NBA", prop_type="Points",
         line=27.5, direction="MORE", row_id=None):
    raw = {
        "player": player, "sport": sport, "prop_type": prop_type,
        "line": line, "direction": direction, "slate_date": "2026-07-04",
    }
    if row_id:
        raw["row_id"] = row_id
    return normalize_row(raw)


def _patch_odds(monkeypatch, props):
    monkeypatch.setattr(
        odds_api, "fetch_all_props",
        lambda sport: (props, {"events": "AVAILABLE (remaining=99)", "props": "AVAILABLE (remaining=99)"}),
    )


def _patch_odds_failure(monkeypatch):
    monkeypatch.setattr(
        odds_api, "fetch_all_props",
        lambda sport: ([], {"events": "FAILED: quota exhausted", "props": "NOT_CALLED"}),
    )


def _patch_injuries(monkeypatch, injuries):
    monkeypatch.setattr(status_service, "get_injuries", lambda sport: (injuries, "AVAILABLE"))


def _patch_injuries_failure(monkeypatch):
    monkeypatch.setattr(status_service, "get_injuries", lambda sport: ({}, "FAILED: HTTP 503"))


def test_fills_market_line_when_missing(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 27.0, "bookmaker": "fanduel", "price": -105},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row()
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert enrichment[key]["sportsbook_line"] == 26.5
    assert enrichment[key]["best_available"] == 26.5  # MORE -> lowest line easiest to clear
    assert enrichment[key]["consensus_line"] == pytest.approx(26.75)
    assert status["sports"]["NBA"]["market"]["events"] == "AVAILABLE (remaining=99)"
    assert status["sports"]["NBA"]["market_props_found"] == 2


def test_best_available_for_under_side(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "LESS",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
        {"player": "LeBron James", "prop": "player_points", "side": "LESS",
         "line": 27.5, "bookmaker": "fanduel", "price": -105},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row(direction="LESS")
    enrichment, _ = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert enrichment[key]["best_available"] == 27.5  # LESS -> highest line easiest to stay under


def test_never_overwrites_caller_supplied_market_line(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 99.0, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row()
    base = {"lebron james:points": {"sportsbook_line": 26.5}}
    enrichment, _ = auto_enrichment.build_auto_enrichment([row], base_enrichment=base)

    key = "lebron james:points"
    assert enrichment[key]["sportsbook_line"] == 26.5
    assert enrichment[key]["best_available"] == 99.0  # still fills the missing field


def test_unmapped_prop_type_leaves_market_fields_empty(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row(prop_type="Double-Double")  # not in the mapping table
    enrichment, _ = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:double-double"
    assert key not in enrichment or "sportsbook_line" not in enrichment.get(key, {})


def test_odds_api_failure_fills_nothing_and_reports_honestly(monkeypatch):
    _patch_odds_failure(monkeypatch)
    _patch_injuries(monkeypatch, {})

    row = _row()
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert "sportsbook_line" not in enrichment.get(key, {})
    assert status["sports"]["NBA"]["market"]["events"] == "FAILED: quota exhausted"
    assert status["sports"]["NBA"]["market_props_found"] == 0


def test_status_payload_auto_filled_from_espn(monkeypatch):
    _patch_odds(monkeypatch, [])
    _patch_injuries(monkeypatch, {
        "lebron james": {"flag": 1, "status_raw": "questionable"},
    })

    row = _row()
    enrichment, _ = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    payload = enrichment[key]["status_payload"]
    assert payload["status"] == "questionable"
    assert payload["source"] == "ESPN"
    assert payload["dnp_risk"] is False


def test_status_payload_never_overwrites_caller_supplied(monkeypatch):
    _patch_odds(monkeypatch, [])
    _patch_injuries(monkeypatch, {
        "lebron james": {"flag": 2, "status_raw": "out"},
    })

    row = _row()
    base = {"lebron james:points": {"status_payload": {"status": "ACTIVE", "source": "Rotowire"}}}
    enrichment, _ = auto_enrichment.build_auto_enrichment([row], base_enrichment=base)

    key = "lebron james:points"
    assert enrichment[key]["status_payload"]["source"] == "Rotowire"


def test_status_failure_leaves_status_payload_unset(monkeypatch):
    _patch_odds(monkeypatch, [])
    _patch_injuries_failure(monkeypatch)

    row = _row()
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    key = "lebron james:points"
    assert "status_payload" not in enrichment.get(key, {})
    assert status["sports"]["NBA"]["status"] == "FAILED: HTTP 503"


def test_unsupported_sport_is_skipped_cleanly(monkeypatch):
    row = _row(sport="PGA", prop_type="Points")
    enrichment, status = auto_enrichment.build_auto_enrichment([row])

    assert status["sports"]["PGA"]["market"] == "NOT_CALLED: sport not supported by odds_api"
    assert status["sports"]["PGA"]["status"] == "NOT_CALLED: sport not supported by status service"


def test_preserves_row_id_key_when_caller_used_it(monkeypatch):
    _patch_odds(monkeypatch, [
        {"player": "LeBron James", "prop": "player_points", "side": "MORE",
         "line": 26.5, "bookmaker": "draftkings", "price": -110},
    ])
    _patch_injuries(monkeypatch, {})

    row = _row(row_id="row_0_abc123")
    base = {"row_0_abc123": {}}
    enrichment, _ = auto_enrichment.build_auto_enrichment([row], base_enrichment=base)

    assert enrichment["row_0_abc123"]["sportsbook_line"] == 26.5
    assert "lebron james:points" not in enrichment


def test_multi_sport_batch_fetches_each_sport_once(monkeypatch):
    calls = []

    def fake_fetch(sport):
        calls.append(sport)
        return [], {"events": "AVAILABLE", "props": "AVAILABLE"}

    monkeypatch.setattr(odds_api, "fetch_all_props", fake_fetch)
    _patch_injuries(monkeypatch, {})

    rows = [
        _row(player="A", sport="NBA"),
        _row(player="B", sport="NBA"),
        _row(player="C", sport="MLB", prop_type="Hits"),
    ]
    auto_enrichment.build_auto_enrichment(rows)

    assert sorted(calls) == ["MLB", "NBA"]
