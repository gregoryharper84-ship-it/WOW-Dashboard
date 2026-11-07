"""Current-season/source integrity regressions for the bounded #2472 intake.

The in-memory store implements the database boundary; fitting and acquisition
are replaced only when the test concerns maintenance rather than those stages.
No fixture supplies certification or publication authority.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import basketball_event_hydration_runtime as hydration
import v17.basketball_model_maintenance as maintenance


class Store:
    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]

    def table(self, _name):
        return Query(self)


class Query:
    def __init__(self, store):
        self.store, self.filters, self.orders = store, [], []
        self.maximum = self.window = self.incoming = None
        self.ignore_duplicates = False

    def select(self, _fields):
        return self

    def eq(self, key, value):
        self.filters.append(lambda row: row.get(key) == value)
        return self

    def gte(self, key, value):
        self.filters.append(lambda row: row.get(key, "") >= value)
        return self

    def lte(self, key, value):
        self.filters.append(lambda row: row.get(key, "") <= value)
        return self

    def in_(self, key, values):
        self.filters.append(lambda row: row.get(key) in values)
        return self

    def order(self, key, desc=False):
        self.orders.append((key, desc))
        return self

    def limit(self, count):
        self.maximum = count
        return self

    def range(self, start, end):
        self.window = (start, end)
        return self

    def upsert(self, rows, on_conflict, ignore_duplicates=False):
        assert on_conflict == "game_id"
        self.incoming = rows
        self.ignore_duplicates = ignore_duplicates
        return self

    def execute(self):
        if self.incoming is not None:
            written = []
            for row in self.incoming:
                if self.ignore_duplicates and any(old["game_id"] == row["game_id"] for old in self.store.rows):
                    continue
                self.store.rows = [old for old in self.store.rows if old["game_id"] != row["game_id"]]
                self.store.rows.append(dict(row))
                written.append(row)
            return SimpleNamespace(data=written)
        rows = [row for row in self.store.rows if all(check(row) for check in self.filters)]
        for key, desc in reversed(self.orders):
            rows.sort(key=lambda row: row[key], reverse=desc)
        if self.window is not None:
            rows = rows[self.window[0]:self.window[1] + 1]
        if self.maximum is not None:
            rows = rows[:self.maximum]
        return SimpleNamespace(data=rows)


def source_row(**overrides):
    return {
        "game_id": "401000001", "game_date": "2026-11-05",
        "game_date_time": "2026-11-05T19:00:00-05:00",
        "home_id": "7", "away_id": "8", "home_score": "111", "away_score": "109",
        "status_type_completed": "true", "status_type_name": "STATUS_FINAL",
        "status_type_description": "Final", "status_type_state": "post",
        "season_type": "2", "_wow_source_observed_at": "2026-11-06T03:00:00Z",
        **overrides,
    }


def settled_row(**overrides):
    return {
        "game_id": "espn-401000001", "game_date": "2026-11-05", "season": 2027,
        "settled": True, "source_provider": "SPORTSDATAVERSE_ESPN", **overrides,
    }


def freeze_live_clock(monkeypatch, at="2026-11-07T12:00:00+00:00"):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromisoformat(at).astimezone(tz or timezone.utc)
    monkeypatch.setattr(maintenance, "datetime", Clock)


def shadow_replay(*_args, **_kwargs):
    return {"persisted_status": "SHADOW", "promotion_attempted": False, "can_execute": False}


def forbidden_replay(*_args, **_kwargs):
    pytest.fail("Unverified current-season evidence must not reach fitting")


@pytest.mark.parametrize("sport,at,want", [
    ("NBA", "2026-09-30T12:00:00+00:00", (2023, 2024, 2025, 2026)),
    ("NBA", "2026-10-01T12:00:00+00:00", (2024, 2025, 2026, 2027)),
    ("NBA", "2027-01-02T12:00:00+00:00", (2024, 2025, 2026, 2027)),
    ("WNBA", "2026-11-07T12:00:00+00:00", (2023, 2024, 2025, 2026)),
])
def test_provider_season_windows_follow_league_ending_year(sport, at, want):
    assert maintenance.default_seasons(datetime.fromisoformat(at), sport=sport) == want


def test_default_nba_refresh_cannot_refit_only_the_june_corpus(monkeypatch):
    freeze_live_clock(monkeypatch)
    monkeypatch.setattr(maintenance, "hydrate", lambda *_a, **_k: {
        "settled_rows": 100, "latest_game_date_after": "2026-06-13",
    })
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    result = maintenance.run_basketball_model_maintenance(Store(), sports=("NBA",))
    row = result["rows"][0]
    assert row["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"
    assert row["hydration_blocker"]["required_season_label"] == 2027
    assert result["seasons_by_sport"] == {
        "NBA": [2024, 2025, 2026, 2027],
    }
    assert row["probability_publishable"] is False


def test_missing_acquisition_is_not_current_season_proof(monkeypatch):
    freeze_live_clock(monkeypatch)
    def fail(*_a, **_k):
        raise hydration.BasketballHydrationError("NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE:SPORTSDATAVERSE_ESPN_HTTP_503")
    monkeypatch.setattr(maintenance, "hydrate", fail)
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    result = maintenance.run_basketball_model_maintenance(Store(), sports=("NBA",))
    row = result["rows"][0]
    assert result["status"] == "BLOCKED"
    assert row["hydration_blocker"]["upstream_hydration_blocker"]["code"] == "NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("corrupt", [
    {"season": 2026}, {"source_provider": "BALLDONTLIE"}, {"game_id": "401000002"},
])
def test_latest_day_and_full_season_readback_reject_invalid_identity(monkeypatch, corrupt):
    freeze_live_clock(monkeypatch)
    monkeypatch.setattr(maintenance, "hydrate", lambda *_a, **_k: {
        "settled_rows": 1, "latest_game_date_after": "2026-11-05",
        "latest_current_season_game_date_after": "2026-11-05",
    })
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    invalid = {"game_id": "espn-401000002", "game_date": "2026-10-25", **corrupt}
    rows = [settled_row(), settled_row(**invalid)]
    result = maintenance.run_basketball_model_maintenance(Store(rows), sports=("NBA",))
    assert result["status"] == "BLOCKED"
    assert result["rows"][0]["hydration_blocker"]["current_season_readback_code"] == "NBA_CURRENT_SEASON_PERSISTED_PROVENANCE_INVALID"


def test_readback_inspects_history_beyond_first_page(monkeypatch):
    freeze_live_clock(monkeypatch)
    monkeypatch.setattr(maintenance, "hydrate", lambda *_a, **_k: {
        "settled_rows": 1, "latest_game_date_after": "2026-11-05",
    })
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    rows = [settled_row()] + [settled_row(game_id=f"espn-{n}", game_date="2026-11-04") for n in range(1000, 1101)]
    rows.append(settled_row(game_id="espn-999", game_date="2026-10-25", season=2026))
    result = maintenance.run_basketball_model_maintenance(Store(rows), sports=("NBA",))
    assert result["rows"][0]["hydration_blocker"]["current_season_readback_code"] == "NBA_CURRENT_SEASON_PERSISTED_PROVENANCE_INVALID"


def test_verified_current_season_replay_is_shadow_and_not_new_acquisition(monkeypatch):
    freeze_live_clock(monkeypatch)
    monkeypatch.setattr(maintenance, "hydrate", lambda *_a, **_k: {
        "settled_rows": 100, "fresh_acquisition_new_settled_games": 0,
        "latest_game_date_after": "2026-11-05",
    })
    monkeypatch.setattr(maintenance, "run_training_replay", shadow_replay)
    result = maintenance.run_basketball_model_maintenance(Store([settled_row()]), sports=("NBA",))
    assert result["status"] == "COMPLETE"
    assert result["rows_no_new_settlements"] == 1
    row = result["rows"][0]
    assert row["hydration_status"] == "FRESH_ACQUISITION_NO_NEW_SETTLEMENTS_USING_PERSISTED_CORPUS"
    assert row["training_replay"]["persisted_status"] == "SHADOW"
    assert row["can_execute"] is row["probability_publishable"] is False
    assert result["automatic_certification"] is result["automatic_promotion"] is False


@pytest.mark.parametrize("at,want", [
    ("2026-10-20T18:59:59+00:00", "COMPLETE"),
    ("2026-10-20T19:00:00+00:00", "BLOCKED"),
])
def test_october_exception_requires_verified_canonical_schedule_before_opening(monkeypatch, at, want):
    freeze_live_clock(monkeypatch, at)
    monkeypatch.setattr(maintenance, "hydrate", lambda *_a, **_k: {
        "settled_rows": 0, "latest_game_date_after": "2026-06-13",
        "source_by_season": {2027: "SPORTSDATAVERSE_ESPN"},
        "regular_season_start_by_season": {2027: "2026-10-20T19:00:00Z"},
    })
    monkeypatch.setattr(maintenance, "run_training_replay", shadow_replay if want == "COMPLETE" else forbidden_replay)
    assert maintenance.run_basketball_model_maintenance(Store(), sports=("NBA",))["status"] == want


def test_explicit_clock_cannot_fit_unbounded_persisted_history(monkeypatch):
    monkeypatch.setattr(maintenance, "hydrate", lambda *_a, **_k: {"settled_rows": 0})
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    result = maintenance.run_basketball_model_maintenance(
        Store(), sports=("NBA",), seasons=(2026,), now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert result["rows"][0]["code"] == "BASKETBALL_HISTORICAL_REPLAY_CUTOFF_UNSUPPORTED"
    assert result["rows"][0]["replay_attempted"] is False


def test_missing_csv_season_inherits_acquired_release_and_endpoint():
    row = hydration.normalize_sportsdataverse_game(
        source_row(), "NBA", "2026-11-07T12:00:00Z", source_release_year=2027,
    )
    assert row["season"] == 2027
    assert row["source_endpoint"].endswith("nba_schedule_2027.csv")
    assert row["source_retrieved_at"] == "2026-11-06T03:00:00Z"


@pytest.mark.parametrize("overrides", [
    {"season": "2026"}, {"season": "2027.25"}, {"home_score": "110.5"},
    {"status_type_description": "Postponed"}, {"status_type_state": "in"},
    {"season_type": "1"}, {"_wow_source_observed_at": "2026-11-08T03:00:00Z"},
    {"game_date_time": "2026-11-08T19:00:00-05:00"},
])
def test_inconsistent_or_future_source_observations_never_become_settled(overrides):
    assert hydration.normalize_sportsdataverse_game(
        source_row(**overrides), "NBA", "2026-11-07T12:00:00Z", source_release_year=2027,
    ) is None


def test_nba_acquisition_is_canonical_and_counts_only_new_persisted_ids(monkeypatch):
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [source_row()])
    def unverified_bdl(*_a, **_k):
        pytest.fail("An unverified NBA provider namespace must not enter training")
    monkeypatch.setattr(hydration, "fetch_games", unverified_bdl)
    store = Store()
    first = hydration.hydrate("NBA", (2027,), client=store)
    second = hydration.hydrate("NBA", (2027,), client=store)
    assert first["fresh_acquisition_new_settled_games"] == 1
    assert second["fresh_acquisition_new_settled_games"] == 0
    assert store.rows[0]["game_id"] == "espn-401000001"
    assert store.rows[0]["season"] == 2027
    assert first["source_by_season"] == {2027: "SPORTSDATAVERSE_ESPN"}


def test_current_nba_source_failure_never_switches_to_unverified_provider(monkeypatch):
    def unavailable(*_):
        raise hydration.BasketballHydrationError("SPORTSDATAVERSE_ESPN_HTTP_503")
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", unavailable)
    monkeypatch.setattr(hydration, "fetch_games", lambda *_: [])
    with pytest.raises(hydration.BasketballHydrationError, match="NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE"):
        hydration.hydrate("NBA", (2027,), client=Store())


def test_partially_committed_refresh_is_not_replayed(monkeypatch):
    freeze_live_clock(monkeypatch)
    def source(_sport, year):
        if year == 2027:
            raise hydration.BasketballHydrationError("SPORTSDATAVERSE_ESPN_HTTP_503")
        return [source_row(game_date="2026-06-01", game_date_time="2026-06-01T19:00:00-04:00", season="2026")]
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", source)
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    store = Store()
    result = maintenance.run_basketball_model_maintenance(store, sports=("NBA",), seasons=(2026, 2027))
    assert len(store.rows) == 1
    assert result["rows"][0]["code"] == "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"
    assert result["rows"][0]["hydration_blocker"]["requires_corpus_reconciliation"] is True


def bdl_row(**overrides):
    return {
        "id": 123, "season": 2026, "date": "2026-09-20", "status": "Final",
        "home_team_score": 88, "visitor_team_score": 80,
        "home_team": {"id": 1}, "visitor_team": {"id": 2},
        "_wow_source_observed_at": "2026-09-21T12:00:00Z", **overrides,
    }


def test_historical_bdl_hydration_cannot_backdate_a_later_final(monkeypatch):
    monkeypatch.setattr(hydration, "fetch_games", lambda *_: [bdl_row()])
    store = Store()
    result = hydration.hydrate(
        "WNBA", (2026,), client=store, now=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert result["settled_rows"] == 0
    assert store.rows == []


@pytest.mark.parametrize("overrides", [
    {"date": "2026-12-20"}, {"date": "invalid-date"},
    {"datetime": "2026-09-21T13:00:00Z"},
    {"_wow_source_observed_at": None},
    {"_wow_source_observed_at": "2026-09-21T12:00:00"},
    {"_wow_source_observed_at": "2026-09-23T12:00:00Z"},
])
def test_bdl_final_requires_valid_event_and_acquisition_chronology(overrides):
    assert hydration.normalize_game(
        bdl_row(**overrides), "WNBA", "2026-09-22T12:00:00Z",
    ) is None


def test_bdl_source_time_is_actual_acquisition_not_the_replay_cutoff():
    row = hydration.normalize_game(bdl_row(), "WNBA", "2026-09-22T12:00:00Z")
    assert row["source_retrieved_at"] == "2026-09-21T12:00:00Z"
    assert row["updated_at"] == "2026-09-21T12:00:00Z"


def test_bdl_fetch_overwrites_provider_claimed_acquisition_time(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 21, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(hydration, "datetime", Clock)
    monkeypatch.setattr(hydration, "_key", lambda: "test-only")
    raw = bdl_row(_wow_source_observed_at="2000-01-01T00:00:00Z")
    monkeypatch.setattr(hydration.requests, "get", lambda *_a, **_k: SimpleNamespace(
        status_code=200, json=lambda: {"data": [raw], "meta": {}},
    ))
    rows = hydration.fetch_games("WNBA", 2026)
    assert rows[0]["_wow_source_observed_at"] == "2026-09-21T12:00:00+00:00"
    assert raw["_wow_source_observed_at"] == "2000-01-01T00:00:00Z"


def test_canonical_outage_can_replay_independently_verified_current_corpus(monkeypatch):
    freeze_live_clock(monkeypatch)
    def unavailable(*_a, **_k):
        raise hydration.BasketballHydrationError("NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE:SPORTSDATAVERSE_ESPN_HTTP_503")
    monkeypatch.setattr(maintenance, "hydrate", unavailable)
    monkeypatch.setattr(maintenance, "run_training_replay", shadow_replay)
    result = maintenance.run_basketball_model_maintenance(Store([settled_row()]), sports=("NBA",))
    assert result["status"] == "COMPLETE"
    assert result["rows_fresh_acquisition_degraded"] == 1
    row = result["rows"][0]
    assert row["hydration"] is None
    assert row["hydration_status"] == "FRESH_ACQUISITION_BLOCKED_USING_PERSISTED_CORPUS"
    assert row["hydration_blocker"]["code"] == "NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE"
    assert row["training_replay"]["persisted_status"] == "SHADOW"
    assert row["probability_publishable"] is row["can_execute"] is False


@pytest.mark.parametrize("rows", [[], [settled_row(season=2026)], [settled_row(source_provider="BALLDONTLIE")]])
def test_canonical_outage_cannot_waive_current_corpus_identity(monkeypatch, rows):
    freeze_live_clock(monkeypatch)
    def unavailable(*_a, **_k):
        raise hydration.BasketballHydrationError("NBA_CANONICAL_SDV_SOURCE_UNAVAILABLE")
    monkeypatch.setattr(maintenance, "hydrate", unavailable)
    monkeypatch.setattr(maintenance, "run_training_replay", forbidden_replay)
    result = maintenance.run_basketball_model_maintenance(Store(rows), sports=("NBA",))
    assert result["status"] == "BLOCKED"
    assert result["rows"][0]["code"] == "NBA_CURRENT_SEASON_SETTLED_DATA_MISSING"


@pytest.mark.parametrize("latest_day", ["2026-11-05", "2026-11-06"])
def test_later_canonical_snapshot_fills_partial_days_without_rewriting_evidence(monkeypatch, latest_day):
    first_rows = [source_row()]
    if latest_day != "2026-11-05":
        first_rows.append(source_row(
            game_id="401000003", game_date=latest_day,
            game_date_time=f"{latest_day}T19:00:00-05:00",
            _wow_source_observed_at="2026-11-07T03:00:00Z",
        ))
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: first_rows)
    store = Store()
    hydration.hydrate("NBA", (2027,), client=store)
    original = dict(next(row for row in store.rows if row["game_id"] == "espn-401000001"))
    later_rows = [source_row(home_score="112", _wow_source_observed_at="2026-11-07T04:00:00Z")]
    later_rows.append(source_row(game_id="401000002", home_id="9", away_id="10"))
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: later_rows)
    result = hydration.hydrate("NBA", (2027,), client=store)
    assert result["fresh_acquisition_new_settled_games"] == 1
    assert any(row["game_id"] == "espn-401000002" for row in store.rows)
    assert next(row for row in store.rows if row["game_id"] == "espn-401000001") == original


def test_canonical_reconciliation_keeps_the_cross_provider_date_fence(monkeypatch):
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [source_row()])
    store = Store([settled_row(game_id="123", source_provider="BALLDONTLIE")])
    with pytest.raises(hydration.BasketballHydrationError, match="NBA_CANONICAL_DAY_PROVIDER_UNVERIFIED"):
        hydration.hydrate("NBA", (2027,), client=store)
    assert [row["game_id"] for row in store.rows] == ["123"]


def test_completed_canonical_season_reconciles_without_false_recent_source_target(monkeypatch):
    old = source_row(
        game_date="2026-06-13", game_date_time="2026-06-13T19:00:00-04:00",
        season="2026", _wow_source_observed_at="2026-06-14T03:00:00Z",
    )
    def source(_sport, season):
        return [old] if season == 2026 else [source_row(game_id="401000002")]
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", source)
    store = Store()
    hydration.hydrate("NBA", (2026,), client=store)
    original = dict(store.rows[0])
    result = hydration.hydrate(
        "NBA", (2026, 2027), client=store, now=datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
    )
    assert result["fresh_acquisition_new_settled_games"] == 1
    assert 2026 not in result["recent_supplement_status"]
    assert result["source_by_season"] == {2026: "SPORTSDATAVERSE_ESPN", 2027: "SPORTSDATAVERSE_ESPN"}
    assert next(row for row in store.rows if row["game_id"] == "espn-401000001") == original


def test_canonical_insert_race_cannot_rewrite_settled_observation_or_count_as_new():
    original = settled_row(home_score=111, source_retrieved_at="2026-11-06T03:00:00Z")
    class RacingStore(Store):
        def table(self, _name):
            return RacingQuery(self)
    class RacingQuery(Query):
        def upsert(self, rows, on_conflict, ignore_duplicates=False):
            self.incoming = rows
            self.ignore_duplicates = ignore_duplicates
            return self
        def execute(self):
            if self.incoming is not None:
                self.store.rows = [dict(original)]  # Another writer won after our ID readback.
                if self.ignore_duplicates:
                    return SimpleNamespace(data=[])
            return super().execute()
    store = RacingStore()
    count = hydration._persist_rows(store, "NBA", [dict(original, home_score=112)])
    assert store.rows == [original]
    assert count == 0


def test_canonical_confirmation_cannot_count_a_preexisting_id_as_new():
    """Echoing an already-present ID back as confirmation must fail closed."""
    original = settled_row()
    new_row = settled_row(game_id="espn-401000099")

    class EchoingQuery(Query):
        def execute(self):
            if self.incoming is not None:
                for row in self.incoming:
                    self.store.rows.append(dict(row))
                return SimpleNamespace(data=[dict(original)])
            return super().execute()

    class EchoingStore(Store):
        def table(self, _name):
            return EchoingQuery(self)

    store = EchoingStore([original])
    with pytest.raises(hydration.BasketballHydrationPartialWriteError) as caught:
        hydration._persist_rows(
            store, "NBA", [dict(original, home_score=112), new_row],
        )
    # A write was attempted, so the boundary must surface an ambiguous/partial
    # write and never report the pre-existing ID as a newly persisted game.
    assert str(caught.value) == "BASKETBALL_HYDRATION_PARTIAL_WRITE_REPLAY_FORBIDDEN"
    assert isinstance(caught.value.__cause__, hydration.BasketballHydrationError)
    assert str(caught.value.__cause__) == "BASKETBALL_INSERTED_GAME_IDS_UNVERIFIED"
    assert len(store.rows) == 2


def test_canonical_confirmation_rejects_a_duplicated_new_id():
    original = settled_row()
    new_row = settled_row(game_id="espn-401000098")

    class DuplicatingQuery(Query):
        def execute(self):
            if self.incoming is not None:
                self.store.rows.append(dict(self.incoming[0]))
                return SimpleNamespace(data=[dict(self.incoming[0]), dict(self.incoming[0])])
            return super().execute()

    class DuplicatingStore(Store):
        def table(self, _name):
            return DuplicatingQuery(self)

    with pytest.raises(hydration.BasketballHydrationPartialWriteError) as caught:
        hydration._persist_rows(DuplicatingStore([original]), "NBA", [new_row])
    assert isinstance(caught.value.__cause__, hydration.BasketballHydrationError)
    assert str(caught.value.__cause__) == "BASKETBALL_INSERTED_GAME_IDS_UNVERIFIED"


def test_canonical_empty_day_readback_fills_a_whole_older_missing_day(monkeypatch):
    newer = source_row(
        game_id="401000002", game_date="2026-11-06",
        game_date_time="2026-11-06T19:00:00-05:00",
        _wow_source_observed_at="2026-11-07T03:00:00Z",
    )
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [newer])
    store = Store()
    hydration.hydrate("NBA", (2027,), client=store)
    original = dict(store.rows[0])
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [source_row(), newer])
    result = hydration.hydrate("NBA", (2027,), client=store)
    assert result["fresh_acquisition_new_settled_games"] == 1
    assert {row["game_date"] for row in store.rows} == {"2026-11-05", "2026-11-06"}
    assert next(row for row in store.rows if row["game_id"] == "espn-401000002") == original


@pytest.mark.parametrize("bad_rows,code", [
    (None, "NBA_CANONICAL_DAY_READBACK_INCOMPLETE"),
    ([settled_row()] * 101, "NBA_CANONICAL_DAY_READBACK_INCOMPLETE"),
    ([settled_row(), settled_row(game_id="123", source_provider="BALLDONTLIE")], "NBA_CANONICAL_DAY_PROVIDER_UNVERIFIED"),
    ([settled_row(season=2026)], "NBA_CANONICAL_DAY_PROVIDER_UNVERIFIED"),
    ([settled_row(game_id="espn-malformed")], "NBA_CANONICAL_DAY_PROVIDER_UNVERIFIED"),
])
def test_canonical_day_reconciliation_rejects_incomplete_or_unverified_storage(monkeypatch, bad_rows, code):
    class BadDayQuery(Query):
        day_read = False
        def eq(self, key, value):
            self.day_read |= key == "game_date"
            return super().eq(key, value)
        def execute(self):
            if self.day_read:
                return SimpleNamespace(data=bad_rows)
            return super().execute()
    class BadDayStore(Store):
        def table(self, _name):
            return BadDayQuery(self)
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [source_row(game_id="401000002")])
    store = BadDayStore([settled_row()])
    original = [dict(row) for row in store.rows]
    with pytest.raises(hydration.BasketballHydrationError, match=code):
        hydration.hydrate("NBA", (2027,), client=store)
    assert store.rows == original


def test_canonical_day_readback_failure_never_writes_or_exposes_transport_detail(monkeypatch):
    class BrokenDayQuery(Query):
        day_read = False
        def eq(self, key, value):
            self.day_read |= key == "game_date"
            return super().eq(key, value)
        def execute(self):
            if self.day_read:
                raise RuntimeError("database detail must remain private")
            return super().execute()
    class BrokenDayStore(Store):
        def table(self, _name):
            return BrokenDayQuery(self)
    monkeypatch.setattr(hydration, "fetch_sportsdataverse_games", lambda *_: [source_row(game_id="401000002")])
    store = BrokenDayStore([settled_row()])
    original = [dict(row) for row in store.rows]
    with pytest.raises(hydration.BasketballHydrationError, match="^NBA_CANONICAL_DAY_READBACK_FAILED$"):
        hydration.hydrate("NBA", (2027,), client=store)
    assert store.rows == original
