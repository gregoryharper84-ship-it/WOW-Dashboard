from datetime import date, datetime, timezone
from types import SimpleNamespace

import basketball_event_hydration_runtime as hydration


def _espn_event(*, event_id="401", game_date="2026-09-20", home_score="88", away_score="81"):
    return {
        "id": event_id,
        "date": f"{game_date}T23:00Z",
        "status": {"type": {"completed": True, "description": "Final"}},
        "season": {"year": 2026, "type": 2, "slug": "regular-season"},
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


def test_hydrate_does_not_promote_unentitled_raw_espn_to_fitted_training(monkeypatch):
    monkeypatch.setattr(
        hydration, "fetch_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        ),
    )
    monkeypatch.setattr(
        hydration, "fetch_sportsdataverse_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("SPORTSDATAVERSE_ESPN_HTTP_503")
        ),
    )
    monkeypatch.setattr(
        hydration, "fetch_espn_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unclassified public source must never be queried for fitted training")
        ),
    )
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda source_id: SimpleNamespace(
            ready_for_candidate_training=False,
            blockers=("MODEL_SOURCE_UNCLASSIFIED",),
        ),
    )
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_args: None)
    try:
        hydration.hydrate("WNBA", [2026], client=object())
        raise AssertionError("unentitled raw scoreboard was used for fitted training")
    except hydration.BasketballHydrationError as exc:
        assert "ESPN_SCOREBOARD_TRAINING_NOT_ENTITLED" in str(exc)
        assert "MODEL_SOURCE_UNCLASSIFIED" in str(exc)

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
            AssertionError("unentitled public source must not be queried")
        ),
    )
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_args: SimpleNamespace(
            ready_for_candidate_training=False,
            blockers=("MODEL_SOURCE_UNCLASSIFIED",),
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
        assert "ESPN_SCOREBOARD_TRAINING_NOT_ENTITLED" in text
        assert "MODEL_SOURCE_UNCLASSIFIED" in text


def test_recent_espn_settlement_fetch_is_bounded_to_past_local_days():
    captured = []

    class Response:
        status_code = 200
        def __init__(self, events):
            self.events = events
        def json(self):
            return {"events": self.events}

    class Session:
        @staticmethod
        def get(url, *, params, headers, timeout):
            captured.append((url, params.copy(), timeout))
            day = params["dates"]
            # One completed event on the last of 14 prior local days.
            assert len(day) == 8 and day.isdecimal(), "ESPN does not support date ranges"
            return Response([_espn_event(event_id="401908001", game_date="2026-10-22")]
                            if day == "20261022" else [])

    result = hydration.fetch_recent_espn_settlements(
        "NBA", now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
        session=Session(),
    )
    assert [row["id"] for row in result] == ["401908001"]
    assert len(captured) == 14
    assert captured[0][1] == {"dates": "20261009", "limit": 100}
    assert captured[-1][1] == {"dates": "20261022", "limit": 100}
    assert len({x[1]["dates"] for x in captured}) == 14
    assert all(x[0].endswith("/basketball/nba/scoreboard") for x in captured)


def test_recent_espn_one_bad_day_refuses_partial_history_without_retry():
    calls = []
    class Response:
        def __init__(self, code, events=None):
            self.status_code = code
            self.events = events or []
        def json(self):
            return {"events": self.events}
    class Session:
        @staticmethod
        def get(_url, *, params, **kwargs):
            calls.append(params["dates"])
            return Response(403 if params["dates"] == "20261012" else 200)
    try:
        hydration.fetch_recent_espn_settlements(
            "NBA", now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
            session=Session(),
        )
        raise AssertionError("must reject incomplete bounded acquisition")
    except hydration.BasketballHydrationError as exc:
        assert str(exc) == "ESPN_RECENT_SOURCE_HTTP_403"
    assert calls == ["20261009", "20261010", "20261011", "20261012"]


def test_recent_espn_rejects_a_wrong_date_or_duplicate_provider_event_id():
    for fault, expected in (
        ("wrong_date", "ESPN_RECENT_SOURCE_EVENT_WRONG_DATE"),
        ("duplicate", "ESPN_RECENT_DUPLICATE_GAME_ID"),
    ):
        class Response:
            status_code = 200
            def __init__(self, events):
                self.events = events
            def json(self):
                return {"events": self.events}
        class Session:
            @staticmethod
            def get(_url, *, params, **kwargs):
                day = params["dates"]
                if fault == "wrong_date" and day == "20261009":
                    return Response([_espn_event(event_id="wrong", game_date="2026-10-22")])
                if fault == "duplicate":
                    if day == "20261009":
                        return Response([_espn_event(event_id="same", game_date="2026-10-09")])
                    if day == "20261010":
                        return Response([_espn_event(event_id="same", game_date="2026-10-10")])
                return Response([])
        try:
            hydration.fetch_recent_espn_settlements(
                "NBA", now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
                session=Session(),
            )
            raise AssertionError("must reject ambiguous dated provider evidence")
        except hydration.BasketballHydrationError as exc:
            assert str(exc) == expected


def test_recent_espn_budget_rejects_15th_call_before_network():
    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            raise AssertionError("provider must not be reached")
    try:
        hydration.fetch_recent_espn_settlements(
            "NBA", now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
            lookback_days=15, session=Session(),
        )
        raise AssertionError("must cap recent source lookback")
    except hydration.BasketballHydrationError as exc:
        assert str(exc) == "ESPN_RECENT_LOOKBACK_INVALID"

def test_espn_settled_calendar_day_matches_new_york_not_utc_and_rejects_postponed():
    raw = _espn_event(event_id="401908002", game_date="2026-10-24")
    raw["date"] = "2026-10-24T01:00:00Z"
    row = hydration.normalize_espn_game(raw, "NBA", "2026-10-24T03:00:00Z")
    assert row is not None and row["game_date"] == "2026-10-23"
    assert hydration.normalize_espn_game(raw, "NBA", "2026-10-24T00:00:00Z") is None
    raw["status"]["type"] = {"completed": False, "description": "Postponed"}
    assert hydration.normalize_espn_game(raw, "NBA", "2026-10-24T03:00:00Z") is None


def _mock_stale_sportsdataverse(monkeypatch):
    monkeypatch.setattr(
        hydration, "fetch_games",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        ),
    )
    monkeypatch.setattr(
        hydration, "fetch_sportsdataverse_games",
        lambda sport, season: [
            _sportsdataverse_row(event_id="401_earlier", game_date="2026-06-13")
        ],
    )
    persisted = []
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: persisted.extend(rows))
    latest = iter([date(2026, 6, 13), date(2026, 10, 22)])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_args: next(latest))
    return persisted


def test_stale_but_successful_csv_can_trigger_bounded_current_settled_fallback(monkeypatch):
    # Hypothetical independent authorization in a unit test only; production
    # ESPN_SCOREBOARD remains unclassified and must never reach this branch.
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda source_id: SimpleNamespace(
            ready_for_candidate_training=True, blockers=(),
        ),
    )
    persisted = _mock_stale_sportsdataverse(monkeypatch)
    called = []
    monkeypatch.setattr(
        hydration, "fetch_recent_espn_settlements",
        lambda sport, *, now: called.append((sport, now.isoformat())) or [
            _espn_event(event_id="401908002", game_date="2026-10-22"),
        ],
    )
    result = hydration.hydrate(
        "NBA", [2026], client=object(), now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
    )
    assert called and called[0][0] == "NBA"
    assert result["source_by_season"][2026] == "ESPN_SCOREBOARD"
    assert result["recent_supplement_status"][2026] == "SETTLED_SOURCE_UPDATED"
    assert result["fresh_acquisition_new_settled_games"] == 1
    assert result["settled_rows"] == 1
    assert result["latest_game_date_after"] == "2026-10-22"
    assert [row["game_id"] for row in persisted] == ["espn-401908002"]
    assert persisted[0]["source_provider"] == "ESPN_SCOREBOARD"
    assert result["can_execute"] is False


def test_recent_source_failure_never_advertises_updated_games(monkeypatch):
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_args: SimpleNamespace(ready_for_candidate_training=True, blockers=()),
    )
    persisted = _mock_stale_sportsdataverse(monkeypatch)
    monkeypatch.setattr(
        hydration, "fetch_recent_espn_settlements",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("ESPN_RECENT_SOURCE_HTTP_403")
        ),
    )
    result = hydration.hydrate(
        "NBA", [2026], client=object(), now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
    )
    assert result["source_by_season"][2026] == "SPORTSDATAVERSE_ESPN"
    assert result["recent_supplement_status"][2026] == "SOURCE_BLOCKED:ESPN_RECENT_SOURCE_HTTP_403"
    assert result["settled_rows"] == 0
    assert result["fresh_acquisition_new_settled_games"] == 0
    assert persisted == []
    assert result["can_execute"] is False


def test_duplicated_recent_event_ids_do_not_create_duplicate_training_games(monkeypatch):
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_args: SimpleNamespace(ready_for_candidate_training=True, blockers=()),
    )
    persisted = _mock_stale_sportsdataverse(monkeypatch)
    repeated = _espn_event(event_id="401908002", game_date="2026-10-22")
    monkeypatch.setattr(
        hydration, "fetch_recent_espn_settlements",
        lambda *_args, **_kwargs: [repeated, repeated],
    )
    result = hydration.hydrate(
        "NBA", [2026], client=object(), now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
    )
    assert result["recent_supplement_status"][2026] == "SOURCE_BLOCKED:ESPN_RECENT_DUPLICATE_GAME_ID"
    assert result["settled_rows"] == 0
    assert persisted == []


def test_recent_source_rejects_malformed_and_unavailable_reply():
    class Malformed:
        status_code = 200
        def json(self):
            return {"events": "invalid"}

    class Unavailable:
        status_code = 403

    for response, code in ((Malformed(), "ESPN_RECENT_SOURCE_INVALID_RESPONSE"),
                           (Unavailable(), "ESPN_RECENT_SOURCE_HTTP_403")):
        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return response
        try:
            hydration.fetch_recent_espn_settlements(
                "NBA", now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc), session=Session(),
            )
            raise AssertionError("expected fail-closed provider result")
        except hydration.BasketballHydrationError as exc:
            assert str(exc) == code


def test_espn_fallback_rejects_preseason_playoff_or_unknown_season_type():
    raw = _espn_event(event_id="401908003", game_date="2026-10-22")
    retrieved = "2026-10-24T03:00:00Z"
    assert hydration.normalize_espn_game(raw, "NBA", retrieved) is not None
    for season in (
        {"year": 2027, "type": 1, "slug": "preseason"},
        {"year": 2027, "type": 3, "slug": "post-season"},
        {"year": 2027, "type": 2, "slug": "preseason"},
        {"year": 2027, "type": 1, "slug": "regular-season"},
        {},
    ):
        changed = dict(raw, season=season)
        assert hydration.normalize_espn_game(changed, "NBA", retrieved) is None


def test_licensed_2027_nba_release_provenance_is_not_calendar_year():
    raw = _sportsdataverse_row(event_id="401909001", game_date="2026-10-20")
    raw["season"] = "2027"
    value = hydration.normalize_sportsdataverse_game(
        raw, "NBA", "2026-10-23T17:00:00Z", source_release_year=2027,
    )
    assert value is not None
    assert value["game_date"] == "2026-10-20"
    assert value["season"] == 2027
    assert value["source_endpoint"].endswith("/nba_schedule_2027.csv")
    assert value["source_provider"] == "SPORTSDATAVERSE_ESPN"
    assert value["settled"] is True


def test_unlicensed_recent_espn_is_not_fetched_or_persisted_during_shadow_refresh(monkeypatch):
    persisted = _mock_stale_sportsdataverse(monkeypatch)
    monkeypatch.setattr(
        hydration, "fetch_recent_espn_settlements",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("research-only ESPN must not feed fitted training")
        ),
    )
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_args: SimpleNamespace(
            ready_for_candidate_training=False,
            blockers=("MODEL_SOURCE_UNCLASSIFIED",),
        ),
    )
    result = hydration.hydrate(
        "NBA", [2026], client=object(), now=datetime(2026, 10, 23, 17, tzinfo=timezone.utc),
    )
    assert result["source_by_season"][2026] == "SPORTSDATAVERSE_ESPN"
    assert result["settled_rows"] == 0
    assert result["recent_supplement_status"][2026] == (
        "SOURCE_BLOCKED:ESPN_SCOREBOARD_TRAINING_NOT_ENTITLED:MODEL_SOURCE_UNCLASSIFIED"
    )
    assert persisted == []
    assert result["can_execute"] is False
