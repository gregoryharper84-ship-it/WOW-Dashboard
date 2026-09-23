from datetime import date
from types import SimpleNamespace

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


def _sportsdataverse_row(*, event_id="401", game_date="2026-09-20", home_score="88", away_score="81"):
    return {
        "game_id": event_id,
        "game_date": game_date,
        "home_id": "10",
        "away_id": "20",
        "home_score": home_score,
        "away_score": away_score,
        "status_type_completed": "True",
        "status_type_name": "STATUS_FINAL",
        "status_type_state": "post",
        "status_type_description": "Final",
        "season": game_date[:4],
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


def test_normalize_sportsdataverse_game_creates_provenance_complete_settled_row():
    row = hydration.normalize_sportsdataverse_game(
        _sportsdataverse_row(),
        "WNBA",
        "2026-09-22T19:00:00+00:00",
    )
    assert row is not None
    assert row["game_id"] == "espn-401"
    assert row["game_date"] == "2026-09-20"
    assert row["home_team_id"] == "espn-10"
    assert row["away_team_id"] == "espn-20"
    assert row["home_win"] is True
    assert row["source_provider"] == "SPORTSDATAVERSE_ESPN"
    assert row["source_endpoint"].endswith("wnba_schedule_2026.csv")
    assert len(row["source_payload_sha256"]) == 64
    assert row["settled"] is True


def test_fetch_sportsdataverse_fails_closed_when_source_registry_blocks(monkeypatch):
    monkeypatch.setattr(
        hydration,
        "source_readiness",
        lambda source_id: SimpleNamespace(
            ready_for_candidate_training=False,
            blockers=("MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING",),
        ),
    )
    try:
        hydration.fetch_sportsdataverse_games("WNBA", 2026, session=object())
        raise AssertionError("expected BasketballHydrationError")
    except hydration.BasketballHydrationError as exc:
        assert "SPORTSDATAVERSE_ESPN_SOURCE_NOT_READY" in str(exc)
        assert "MODEL_SOURCE_NOT_AUTHORIZED_FOR_FITTED_TRAINING" in str(exc)


def test_fetch_sportsdataverse_rejects_schema_drift(monkeypatch):
    monkeypatch.setattr(
        hydration,
        "source_readiness",
        lambda source_id: SimpleNamespace(ready_for_candidate_training=True, blockers=()),
    )

    class Response:
        status_code = 200
        text = "game_id,game_date\n1,2026-09-20\n"

    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    try:
        hydration.fetch_sportsdataverse_games("WNBA", 2026, session=Session())
        raise AssertionError("expected BasketballHydrationError")
    except hydration.BasketballHydrationError as exc:
        assert "SPORTSDATAVERSE_ESPN_SCHEMA_DRIFT" in str(exc)
        assert "home_id" in str(exc)


def test_hydrate_prefers_sportsdataverse_after_primary_failure_and_only_appends_newer_rows(monkeypatch):
    def primary_fail(*_args, **_kwargs):
        raise hydration.BasketballHydrationError("BALLDONTLIE_API_KEY unavailable")

    monkeypatch.setattr(hydration, "fetch_games", primary_fail)
    monkeypatch.setattr(
        hydration,
        "fetch_sportsdataverse_games",
        lambda sport, season: [
            _sportsdataverse_row(event_id="old", game_date="2022-09-18"),
            _sportsdataverse_row(event_id="new", game_date="2026-09-20"),
        ],
    )
    monkeypatch.setattr(
        hydration,
        "fetch_espn_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ESPN fallback should not be used")),
    )

    latest_calls = iter([date(2022, 9, 18), date(2026, 9, 20)])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda client, sport: next(latest_calls))
    persisted = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: persisted.extend(rows))

    result = hydration.hydrate("WNBA", [2026], client=object())

    assert result["source_by_season"][2026] == "SPORTSDATAVERSE_ESPN"
    assert result["fallback_reasons"][2026] == "BALLDONTLIE_API_KEY unavailable"
    assert result["settled_rows"] == 1
    assert result["latest_game_date_before"] == "2022-09-18"
    assert result["latest_game_date_after"] == "2026-09-20"
    assert [row["game_id"] for row in persisted] == ["espn-new"]
    assert result["can_execute"] is False


def test_hydrate_uses_raw_espn_when_sportsdataverse_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        hydration,
        "fetch_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        ),
    )
    monkeypatch.setattr(
        hydration,
        "fetch_sportsdataverse_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("SPORTSDATAVERSE_ESPN_HTTP_503")
        ),
    )
    monkeypatch.setattr(
        hydration,
        "fetch_espn_games",
        lambda sport, season: [_espn_event(event_id="new", game_date="2026-09-20")],
    )

    latest_calls = iter([date(2022, 9, 18), date(2026, 9, 20)])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda client, sport: next(latest_calls))
    persisted = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: persisted.extend(rows))

    result = hydration.hydrate("WNBA", [2026], client=object())
    assert result["source_by_season"][2026] == "ESPN_SCOREBOARD"
    assert [row["game_id"] for row in persisted] == ["espn-new"]


def test_hydrate_fails_closed_when_all_sources_are_unavailable(monkeypatch):
    monkeypatch.setattr(
        hydration,
        "fetch_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        ),
    )
    monkeypatch.setattr(
        hydration,
        "fetch_sportsdataverse_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("SPORTSDATAVERSE_ESPN_HTTP_503")
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
        assert "SPORTSDATAVERSE_ESPN_HTTP_503" in text
        assert "ESPN_SCOREBOARD_HTTP_503" in text
