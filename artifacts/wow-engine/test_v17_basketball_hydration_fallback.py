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


def test_sdv_completed_non_regular_season_games_never_enter_fitted_training():
    # SportsDataverse season_type=2 identifies the regular season. Missing
    # season_type on older snapshots retains the legacy normalization path.
    for sport in ("NBA", "WNBA"):
        for value in (1, "1", 3, "3", 4, True, "postseason", "preseason", "", None, {}):
            row = _sportsdataverse_row()
            row["season_type"] = value
            assert hydration.normalize_sportsdataverse_game(
                row, sport, "2026-09-22T19:00:00+00:00"
            ) is None, (sport, repr(value))
        for value in (2, "2", 2.0, "2.0"):
            row = _sportsdataverse_row()
            row["season_type"] = value
            normalized = hydration.normalize_sportsdataverse_game(
                row, sport, "2026-09-22T19:00:00+00:00"
            )
            assert normalized is not None, (sport, repr(value))
            assert normalized["settled"] is True
        legacy = _sportsdataverse_row()
        assert "season_type" not in legacy
        assert hydration.normalize_sportsdataverse_game(
            legacy, sport, "2026-09-22T19:00:00+00:00"
        ) is not None


def test_nba_hydration_does_not_persist_completed_postseason_source_rows(monkeypatch):
    # Exercise the actual batch ingestion path, not just the normalizer.
    playoff = _sportsdataverse_row(
        event_id="nba-postseason", game_date="2026-11-02"
    )
    playoff["season"] = "2027"
    playoff["season_type"] = 3
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [playoff])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_: None)
    # hydrate calls _persist_rows with an empty list; its actual implementation
    # returns zero without any database IO. Check the normalized batch rather
    # than misclassifying a no-op helper invocation as a persisted row.
    persisted_batches = []

    def record_batch(_client, _sport, rows):
        persisted_batches.append(list(rows))
        assert rows == []
        return 0

    monkeypatch.setattr(hydration, "_persist_rows", record_batch)

    result = hydration.hydrate(
        "NBA", [2027], client=object(),
        now=datetime(2026, 11, 5, 17, tzinfo=timezone.utc),
    )
    assert persisted_batches == [[]]
    assert result["raw_rows"] == 1
    assert result["per_season"][2027] == 0
    assert result["settled_rows"] == 0
    assert result["fresh_acquisition_new_settled_games"] == 0
    assert result["source_by_season"][2027] == "SPORTSDATAVERSE_ESPN"
    assert result["can_execute"] is False


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


def test_repeat_hydration_upserts_are_not_miscounted_as_new_games(monkeypatch):
    """Same BDL season on the second pass is refreshed, not newly acquired."""
    saved_ids = {"bdl-old"}

    class FakeQuery:
        def __init__(self):
            self.lookup = []
            self.writes = None

        def select(self, *_args):
            return self

        def in_(self, field, values):
            assert field == "game_id"
            self.lookup = list(values)
            return self

        def upsert(self, rows, on_conflict):
            assert on_conflict == "game_id"
            self.writes = list(rows)
            return self

        def execute(self):
            if self.writes is not None:
                saved_ids.update(row["game_id"] for row in self.writes)
                return SimpleNamespace(data=self.writes)
            return SimpleNamespace(
                data=[{"game_id": value} for value in self.lookup if value in saved_ids]
            )

    class FakeClient:
        def table(self, _name):
            return FakeQuery()

    monkeypatch.setattr(hydration, "fetch_games", lambda *_: [
        {"game_id": "bdl-old"}, {"game_id": "bdl-new"},
    ])
    monkeypatch.setattr(
        hydration, "normalize_game",
        lambda row, _sport, _retrieved_at: {
            "game_id": row["game_id"], "game_date": "2026-10-20", "settled": True
        },
    )
    monkeypatch.setattr(
        hydration, "_latest_persisted_game_date",
        lambda _client, _sport: date(2026, 10, 20),
    )
    db = FakeClient()
    first = hydration.hydrate("WNBA", [2026], client=db)
    second = hydration.hydrate("WNBA", [2026], client=db)
    assert first["settled_rows"] == second["settled_rows"] == 2
    assert first["fresh_acquisition_new_settled_games"] == 1
    assert second["fresh_acquisition_new_settled_games"] == 0
    assert saved_ids == {"bdl-old", "bdl-new"}


def test_duplicate_game_ids_across_write_batches_fail_before_any_io():
    rows = [{"game_id": f"bdl-{i}"} for i in range(100)]
    rows.append({"game_id": "bdl-0"})

    class DenyDb:
        def table(self, *_args):
            raise AssertionError("No read or upsert is allowed before validation")

    try:
        hydration._persist_rows(DenyDb(), "NBA", rows)
        raise AssertionError("expected duplicate game ID rejection")
    except hydration.BasketballHydrationError as exc:
        assert str(exc) == "BASKETBALL_SETTLED_GAME_ID_INVALID"


def test_partial_upsert_failure_is_not_replay_safe():
    class Query:
        def __init__(self, db):
            self.db = db
            self.writing = False

        def select(self, *_args):
            return self

        def in_(self, *_args):
            return self

        def upsert(self, *_args, **_kwargs):
            self.writing = True
            return self

        def execute(self):
            if self.writing:
                self.db.writes += 1
                if self.db.writes == 2:
                    raise RuntimeError("provider response lost after write")
            return SimpleNamespace(data=[])

    class Db:
        writes = 0

        def table(self, *_args):
            return Query(self)

    db = Db()
    rows = [{"game_id": f"bdl-{i}"} for i in range(101)]
    try:
        hydration._persist_rows(db, "NBA", rows)
        raise AssertionError("expected partial write HOLD")
    except hydration.BasketballHydrationPartialWriteError as exc:
        assert str(exc) == "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"
    assert db.writes == 2


def test_second_season_failure_after_persisted_first_season_blocks_replay(monkeypatch):
    def source(_sport, year):
        if year == 2025:
            return [{"game_id": "bdl-2025-1"}]
        raise RuntimeError("unavailable second season")

    monkeypatch.setattr(hydration, "fetch_games", source)
    monkeypatch.setattr(hydration, "normalize_game", lambda game, *_: {
        "game_id": game["game_id"], "game_date": "2025-10-25", "settled": True
    })
    monkeypatch.setattr(hydration, "_persist_rows", lambda *_args: 1)
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_args: date(2025, 10, 25))
    try:
        hydration.hydrate("WNBA", [2025, 2026], client=object())
        raise AssertionError("expected partial-season HOLD")
    except hydration.BasketballHydrationPartialWriteError as exc:
        assert str(exc) == "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"


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
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: (persisted.extend(rows), len(rows))[1])

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
        assert text == "NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE:SPORTSDATAVERSE_ESPN_HTTP_503"


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
    monkeypatch.setattr(hydration, "_persist_rows", lambda client, sport, rows: (persisted.extend(rows), len(rows))[1])
    # These in-season fixtures test the recent supplement: Nov 3 ET =>
    # last completed Nov 2 and the previous Nov 1 row is inside coverage.
    # October before opening is deliberately covered by a separate regression.
    latest = iter([date(2026, 11, 1), date(2026, 11, 2)])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_args: next(latest))
    return persisted


def test_stale_espn_supplement_cannot_contaminate_nba_fitted_history(monkeypatch):
    # Even a future ESPN training entitlement cannot establish an ESPN-to-BDL
    # or ESPN-to-SportsDataverse *provider* contract. Fail before persistence.
    monkeypatch.setattr(
        hydration,
        "source_readiness",
        lambda source_id: SimpleNamespace(
            ready_for_candidate_training=True, blockers=(),
        ),
    )
    persisted = _mock_stale_sportsdataverse(monkeypatch)
    called = []
    monkeypatch.setattr(
        hydration,
        "fetch_recent_espn_settlements",
        lambda sport, *, now: called.append((sport, now.isoformat())) or [
            _espn_event(event_id="401908002", game_date="2026-11-02"),
        ],
    )
    try:
        hydration.hydrate(
            "NBA", [2027], client=object(),
            now=datetime(2026, 11, 3, 17, tzinfo=timezone.utc),
        )
        raise AssertionError("raw ESPN data must never be inserted into the fitted NBA ledger")
    except hydration.BasketballHydrationError as exc:
        assert str(exc) == "NBA_TEAM_PROVIDER_CROSSWALK_UNVERIFIED"
    assert called and called[0][0] == "NBA"
    assert persisted == []


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
        "NBA", [2027], client=object(), now=datetime(2026, 11, 3, 17, tzinfo=timezone.utc),
    )
    assert result["source_by_season"][2027] == "SPORTSDATAVERSE_ESPN"
    assert result["recent_supplement_status"][2027] == "SOURCE_BLOCKED:ESPN_RECENT_SOURCE_HTTP_403"
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
    repeated = _espn_event(event_id="401908002", game_date="2026-11-02")
    monkeypatch.setattr(
        hydration, "fetch_recent_espn_settlements",
        lambda *_args, **_kwargs: [repeated, repeated],
    )
    result = hydration.hydrate(
        "NBA", [2027], client=object(), now=datetime(2026, 11, 3, 17, tzinfo=timezone.utc),
    )
    assert result["recent_supplement_status"][2027] == "SOURCE_BLOCKED:ESPN_RECENT_DUPLICATE_GAME_ID"
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
        "NBA", [2027], client=object(), now=datetime(2026, 11, 3, 17, tzinfo=timezone.utc),
    )
    assert result["source_by_season"][2027] == "SPORTSDATAVERSE_ESPN"
    assert result["settled_rows"] == 0
    assert result["recent_supplement_status"][2027] == (
        "SOURCE_BLOCKED:ESPN_SCOREBOARD_TRAINING_NOT_ENTITLED:MODEL_SOURCE_UNCLASSIFIED"
    )
    assert persisted == []
    assert result["can_execute"] is False


def test_nba_2027_asset_keeps_canonical_sdv_identity_namespace(monkeypatch):
    """BDL 200/empty cannot hide 2026-27 settled games in SDV 2027 asset."""
    attempted = []

    def deny_bdl(sport, season):
        raise AssertionError("NBA cannot mix BallDontLie IDs with SDV history")

    def live_sdv(sport, season):
        attempted.append(("SPORTSDATAVERSE_ESPN", sport, season))
        row = _sportsdataverse_row(event_id="401909001", game_date="2026-10-26")
        row["season"] = "2027"
        return [row]

    stored = []
    dates = iter([date(2026, 6, 13), date(2026, 10, 26)])
    monkeypatch.setattr(hydration, "fetch_games", deny_bdl)
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", live_sdv)
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_args: next(dates))
    monkeypatch.setattr(
        hydration, "_persist_rows",
        lambda client, sport, rows: (stored.extend(rows), len(rows))[1],
    )
    result = hydration.hydrate(
        "NBA", [2027], client=object(),
        now=datetime(2026, 11, 2, 17, tzinfo=timezone.utc),
    )
    assert attempted == [("SPORTSDATAVERSE_ESPN", "NBA", 2027)]
    assert result["source_by_season"][2027] == "SPORTSDATAVERSE_ESPN"
    assert 2027 not in result["fallback_reasons"]
    assert result["fresh_acquisition_new_settled_games"] == 1
    assert len(stored) == 1
    assert stored[0]["game_date"] == "2026-10-26"
    assert stored[0]["source_endpoint"].endswith("/nba_schedule_2027.csv")
    assert stored[0]["source_provider"] == "SPORTSDATAVERSE_ESPN"
    assert result["can_execute"] is False


def test_wnba_year_is_not_shifted_by_nba_provider_mapping(monkeypatch):
    years = []
    monkeypatch.setattr(
        hydration, "fetch_games",
        lambda sport, season: (
            years.append(("BDL", season)) or
            (_ for _ in ()).throw(hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED"))
        ),
    )
    monkeypatch.setattr(
        hydration, "fetch_sportsdataverse_games",
        lambda sport, season: years.append(("SDV", season)) or [],
    )
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_: None)
    monkeypatch.setattr(hydration, "_persist_rows", lambda *_: 0)
    result = hydration.hydrate(
        "WNBA", [2026], client=object(),
        now=datetime(2026, 11, 2, tzinfo=timezone.utc),
    )
    assert years == [("BDL", 2026), ("SDV", 2026)]
    assert result["source_by_season"][2026] == "SPORTSDATAVERSE_ESPN"
    assert result["fresh_acquisition_new_settled_games"] == 0
    assert result["can_execute"] is False


def test_entitled_espn_nba_fallback_uses_calendar_year_but_blocks_write(monkeypatch):
    requested = []
    monkeypatch.setattr(
        hydration,
        "source_readiness",
        lambda *_: SimpleNamespace(ready_for_candidate_training=True, blockers=()),
    )
    monkeypatch.setattr(
        hydration,
        "fetch_games",
        lambda sport, season: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        ),
    )
    monkeypatch.setattr(
        hydration,
        "fetch_sportsdataverse_games",
        lambda sport, season: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("SPORTSDATAVERSE_ESPN_HTTP_404")
        ),
    )
    monkeypatch.setattr(
        hydration,
        "fetch_espn_games",
        lambda sport, year: requested.append(year) or [
            _espn_event(event_id="401909002", game_date="2026-10-27")
        ],
    )
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_: None)
    stored = []
    monkeypatch.setattr(
        hydration,
        "_persist_rows",
        lambda db, sport, rows: (stored.extend(rows), len(rows))[1],
    )
    try:
        hydration.hydrate(
            "NBA", [2027], client=object(),
            now=datetime(2026, 11, 2, 17, tzinfo=timezone.utc),
        )
        raise AssertionError("NBA scoreboard history must fail without approved crosswalk")
    except hydration.BasketballHydrationError as exc:
        assert str(exc) == "NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE:SPORTSDATAVERSE_ESPN_HTTP_404"
    # A public scoreboard entitlement is not a canonical fitted-team crosswalk.
    assert requested == []
    assert stored == []


def test_recent_espn_supplement_cannot_jump_over_months_of_missing_results(monkeypatch):
    """Never move the corpus fence to yesterday while June-Oct is unacquired."""
    monkeypatch.setattr(
        hydration, "fetch_games",
        lambda *_: (_ for _ in ()).throw(
            hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")
        ),
    )
    monkeypatch.setattr(
        hydration, "fetch_sportsdataverse_games",
        lambda *_: [
            _sportsdataverse_row(event_id="already-persisted", game_date="2026-06-13")
        ],
    )
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_: SimpleNamespace(ready_for_candidate_training=True, blockers=()),
    )
    monkeypatch.setattr(
        hydration, "_latest_persisted_game_date", lambda *_: date(2026, 6, 13)
    )
    monkeypatch.setattr(
        hydration, "_persist_rows",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("No partial backfill may advance persisted date")
        ),
    )
    monkeypatch.setattr(
        hydration, "fetch_recent_espn_settlements",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Cannot use 14-day fetch to bridge June-October")
        ),
    )
    try:
        hydration.hydrate(
            "NBA", [2027], client=object(),
            now=datetime(2026, 11, 2, 17, tzinfo=timezone.utc),
        )
        raise AssertionError("expected a typed backfill failure")
    except hydration.BasketballHydrationError as exc:
        assert str(exc) == "ESPN_RECENT_WINDOW_GAP_NOT_COVERED"


def test_nba_completed_season_is_not_recent_espn_supplement_target(monkeypatch):
    """In November 2026, NBA ending-year 2026 is completed; 2027 is current."""
    calls = []
    stored = []

    def canonical_games(sport, season):
        calls.append((sport, season))
        if season == 2026:
            return [_sportsdataverse_row(
                event_id="nba-june-2026", game_date="2026-06-13",
            )]
        if season == 2027:
            return [_sportsdataverse_row(
                event_id="nba-oct-2027", game_date="2026-10-26",
            )]
        raise AssertionError("unexpected season")

    dates = iter([date(2026, 6, 13), date(2026, 10, 26)])
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", canonical_games)
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_: SimpleNamespace(ready_for_candidate_training=True, blockers=()),
    )
    monkeypatch.setattr(
        hydration, "_latest_persisted_game_date", lambda *_: next(dates),
    )
    monkeypatch.setattr(
        hydration, "fetch_recent_espn_settlements",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("completed 2026 NBA season must not use current ESPN supplement")
        ),
    )
    monkeypatch.setattr(
        hydration, "_persist_rows",
        lambda _client, _sport, rows: (stored.extend(rows), len(rows))[1],
    )

    result = hydration.hydrate(
        "NBA", [2026, 2027], client=object(),
        now=datetime(2026, 11, 2, 17, tzinfo=timezone.utc),
    )
    assert calls == [("NBA", 2026), ("NBA", 2027)]
    assert 2026 not in result["recent_supplement_status"]
    assert result["source_by_season"][2027] == "SPORTSDATAVERSE_ESPN"
    assert result["per_season"] == {2026: 0, 2027: 1}
    assert [r["game_date"] for r in stored] == ["2026-10-26"]
    assert result["can_execute"] is False


def test_nba_offseason_sdv_has_no_false_recent_espn_gap(monkeypatch):
    """July 2027 is offseason: an ended 2027 NBA season is not missing games."""
    prior = date(2027, 6, 18)
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_args: [])
    monkeypatch.setattr(hydration, "_latest_persisted_game_date", lambda *_args: prior)
    monkeypatch.setattr(hydration, "_persist_rows", lambda *_args: 0)
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("No recent-ESPN source needed during offseason")
        ),
    )
    result = hydration.hydrate(
        "NBA", [2027], client=object(),
        now=datetime(2027, 7, 23, 17, tzinfo=timezone.utc),
    )
    assert result["per_season"] == {2027: 0}
    assert result["recent_supplement_status"] == {}
    assert result["can_execute"] is False


def test_wnba_offseason_does_not_infer_source_gap(monkeypatch):
    """WNBA's October season end cannot imply missing November results."""
    def missing_bdl(*_args):
        raise hydration.BasketballHydrationError("BALLDONTLIE_AUTH_FAILED")

    monkeypatch.setattr(hydration, "fetch_games", missing_bdl)
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_args: [])
    monkeypatch.setattr(
        hydration, "_latest_persisted_game_date", lambda *_args: date(2026, 10, 6),
    )
    monkeypatch.setattr(hydration, "_persist_rows", lambda *_args: 0)
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("No recent-ESPN source needed after WNBA season")
        ),
    )
    result = hydration.hydrate(
        "WNBA", [2026], client=object(),
        now=datetime(2026, 11, 12, 17, tzinfo=timezone.utc),
    )
    assert result["per_season"] == {2026: 0}
    assert result["recent_supplement_status"] == {}
    assert result["can_execute"] is False


def test_nba_october_preopening_gap_does_not_trigger_recent_scoreboard(monkeypatch):
    """Preopening October requires no artificial settlement backfill."""
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_args: [])
    monkeypatch.setattr(
        hydration, "_latest_persisted_game_date", lambda *_args: date(2026, 6, 13),
    )
    monkeypatch.setattr(hydration, "_persist_rows", lambda *_args: 0)
    monkeypatch.setattr(
        hydration, "source_readiness",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("No incomplete 14-day scoreboard backfill")
        ),
    )
    result = hydration.hydrate(
        "NBA", [2027], client=object(),
        now=datetime(2026, 10, 7, 17, tzinfo=timezone.utc),
    )
    assert result["recent_supplement_status"] == {}
    assert result["can_execute"] is False
