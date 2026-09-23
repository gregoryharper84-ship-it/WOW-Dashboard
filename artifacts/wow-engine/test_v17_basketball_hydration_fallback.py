from datetime import date

import basketball_event_hydration_runtime as hydration


def _espn_event(*, event_id="401", game_date="2026-09-20", home_score="88", away_score="81"):
    return {
        "id": event_id,
        "date": f"{game_date}T23:00Z",
        "status": {"type": {"completed": True, "description": "Final"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": home_score, "team": {"id": "10"}},
                {"homeAway": "away", "score": away_score, "team": {"id": "20"}},
            ]
        }],
    }


def _bdl_event(*, event_id="bdl-401", game_date="2026-09-20", home_score=88, away_score=81):
    return {
        "id": event_id,
        "date": game_date,
        "season": int(game_date[:4]),
        "status": "Final",
        "home_team_score": home_score,
        "visitor_team_score": away_score,
        "home_team": {"id": "10"},
        "visitor_team": {"id": "20"},
    }


def test_normalize_espn_game_creates_provenance_complete_settled_row():
    row = hydration.normalize_espn_game(
        _espn_event(),
        "WNBA",
        "2026-09-22T19:00:00+00:00",
    )
    assert row is not None
    assert row["game_id"] == "espn-401"
    assert row["game_date"] == "2026-09-20"
    assert row["home_team_id"] == "espn-10"
    assert row["away_team_id"] == "espn-20"
    assert row["home_win"] is True
    assert row["source_provider"] == "ESPN_SCOREBOARD"
    assert len(row["source_payload_sha256"]) == 64
    assert row["settled"] is True


def test_hydrate_falls_back_to_espn_and_only_appends_newer_rows(monkeypatch):
    def primary_fail(*_args, **_kwargs):
        raise hydration.BasketballHydrationError("BALLDONTLIE_API_KEY unavailable")

    monkeypatch.setattr(hydration, "fetch_games", primary_fail)
    monkeypatch.setattr(
        hydration,
        "fetch_espn_games",
        lambda sport, season: [
            _espn_event(event_id="old", game_date="2022-09-18"),
            _espn_event(event_id="new", game_date="2026-09-20"),
        ],
    )

    latest_calls = iter([date(2022, 9, 18), date(2026, 9, 20)])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda client, sport: next(latest_calls))
    persisted = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: persisted.extend(rows))

    result = hydration.hydrate("WNBA", [2026], client=object())

    assert result["source_by_season"][2026] == "ESPN_SCOREBOARD"
    assert result["fallback_reasons"][2026] == "BALLDONTLIE_API_KEY unavailable"
    assert result["settled_rows"] == 1
    assert result["latest_game_date_before"] == "2022-09-18"
    assert result["latest_game_date_after"] == "2026-09-20"
    assert [row["game_id"] for row in persisted] == ["espn-new"]
    assert result["can_execute"] is False


def test_hydrate_falls_back_when_primary_http_success_is_empty(monkeypatch):
    monkeypatch.setattr(hydration, "fetch_games", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        hydration,
        "fetch_espn_games",
        lambda sport, season: [_espn_event(event_id="new", game_date="2026-09-20")],
    )
    latest_calls = iter([date(2023, 4, 2), date(2026, 9, 20)])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda client, sport: next(latest_calls))
    persisted = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: persisted.extend(rows))

    result = hydration.hydrate("NBA", [2026], client=object())

    assert result["source_by_season"][2026] == "ESPN_SCOREBOARD"
    assert result["fallback_reasons"][2026] == "BALLDONTLIE_EMPTY_RESPONSE"
    assert result["settled_rows"] == 1
    assert [row["game_id"] for row in persisted] == ["espn-new"]


def test_hydrate_falls_back_when_primary_does_not_advance_stale_corpus(monkeypatch):
    monkeypatch.setattr(
        hydration,
        "fetch_games",
        lambda *_args, **_kwargs: [_bdl_event(game_date="2023-04-02")],
    )
    monkeypatch.setattr(
        hydration,
        "fetch_espn_games",
        lambda sport, season: [_espn_event(event_id="new", game_date="2025-04-10")],
    )
    latest_calls = iter([date(2023, 4, 2), date(2025, 4, 10)])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda client, sport: next(latest_calls))
    persisted = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: persisted.extend(rows))

    result = hydration.hydrate("NBA", [2025], client=object())

    assert result["source_by_season"][2025] == "ESPN_SCOREBOARD"
    assert result["fallback_reasons"][2025] == "BALLDONTLIE_NO_NEWER_SETTLED_ROWS"
    assert result["latest_game_date_after"] == "2025-04-10"
    assert [row["game_id"] for row in persisted] == ["espn-new"]


def test_hydrate_fails_closed_when_primary_and_fallback_are_unavailable(monkeypatch):
    monkeypatch.setattr(
        hydration,
        "fetch_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        ),
    )
    monkeypatch.setattr(
        hydration,
        "fetch_espn_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("ESPN_SCOREBOARD_HTTP_503")
        ),
    )
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda client, sport: None)

    try:
        hydration.hydrate("NBA", [2026], client=object())
        raise AssertionError("expected BasketballHydrationError")
    except hydration.BasketballHydrationError as exc:
        text = str(exc)
        assert "BASKETBALL_FRESH_SOURCE_UNAVAILABLE" in text
        assert "BALLDONTLIE_AUTH_FAILED" in text
        assert "ESPN_SCOREBOARD_HTTP_503" in text
