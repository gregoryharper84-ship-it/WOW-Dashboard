from __future__ import annotations

from v17 import rundown_ml_board as board


def _primary():
    return [{
        "id": "primary-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-14T23:05:00Z",
        "home_team": "Chicago Cubs",
        "away_team": "Milwaukee Brewers",
        "bookmakers": [{"key": "primarybook", "title": "Primary", "markets": []}],
    }]


def _rundown_same_event():
    return [{
        "id": "rundown-evt-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-14T23:05:00Z",
        "home_team": "Chicago Cubs",
        "away_team": "Milwaukee Brewers",
        "bookmakers": [{"key": "pinnacle", "title": "Pinnacle", "markets": [{
            "key": "h2h",
            "outcomes": [
                {"name": "Chicago Cubs", "price": -135},
                {"name": "Milwaukee Brewers", "price": 115},
            ],
        }]}],
        "_wow_market_evidence": {
            "provider": "RUNDOWN_MARKET_EVIDENCE",
            "prediction_authority": False,
            "research_only": True,
            "can_execute": False,
        },
    }]


def test_default_is_enabled(monkeypatch):
    monkeypatch.delenv("WOW_RUNDOWN_ML_BOARD_ENABLED", raising=False)
    assert board.enabled() is True


def test_non_event_paths_are_not_augmented(monkeypatch):
    monkeypatch.setattr(board, "_collect_rundown", lambda *a, **k: (_rundown_same_event(), []))
    original = _primary()
    result, audit = board.augment_event_discovery("/odds-api/v4/sports", original)
    assert result == original
    assert audit["attempted"] is False


def test_rundown_is_additive_to_successful_primary_discovery(monkeypatch):
    monkeypatch.setattr(board, "_collect_rundown", lambda *a, **k: (_rundown_same_event(), []))
    result, audit = board.augment_event_discovery(
        "/odds-api/v4/sports/baseball_mlb/events",
        _primary(),
    )
    assert audit["attempted"] is True
    assert audit["status"] == "USED"
    assert audit["rows_primary"] == 1
    assert audit["rows_rundown"] == 1
    assert audit["rows_after_union"] == 1
    assert len(result) == 1
    assert {book["title"] for book in result[0]["bookmakers"]} == {"Primary", "Pinnacle"}
    governance = result[0]["_wow_rundown_board_governance"]
    assert governance["prediction_authority"] is False
    assert governance["market_role_evidence_only"] is True
    assert governance["can_execute"] is False


def test_unique_rundown_candidate_expands_candidate_universe(monkeypatch):
    extra = _rundown_same_event()[0]
    extra = {
        **extra,
        "id": "rundown-evt-2",
        "home_team": "Texas Rangers",
        "away_team": "Houston Astros",
        "commence_time": "2026-09-14T23:10:00Z",
    }
    monkeypatch.setattr(board, "_collect_rundown", lambda *a, **k: ([extra], []))
    result, audit = board.augment_event_discovery(
        "/odds-api/v4/sports/baseball_mlb/events",
        _primary(),
    )
    assert len(result) == 2
    assert audit["rows_after_union"] == 2
    assert any(row["home_team"] == "Texas Rangers" for row in result)


def test_rundown_failure_preserves_successful_primary_discovery(monkeypatch):
    monkeypatch.setattr(board, "_collect_rundown", lambda *a, **k: ([], ["RUNDOWN_HTTP_503"]))
    original = _primary()
    result, audit = board.augment_event_discovery(
        "/odds-api/v4/sports/baseball_mlb/events",
        original,
    )
    assert result == original
    assert audit["status"] == "UNAVAILABLE_PRESERVED_PRIMARY"
    assert audit["reason_codes"] == ["RUNDOWN_HTTP_503"]


def test_kill_switch_preserves_primary_without_calling_rundown(monkeypatch):
    monkeypatch.setenv("WOW_RUNDOWN_ML_BOARD_ENABLED", "false")
    monkeypatch.setattr(board, "_collect_rundown", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call")))
    original = _primary()
    result, audit = board.augment_event_discovery(
        "/odds-api/v4/sports/baseball_mlb/events",
        original,
    )
    assert result == original
    assert audit["status"] == "DISABLED"
