from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import v17.spread_margin_replay as replay


class _FakeQuery:
    def __init__(self, table: str, rows: list[dict], order_log: list[tuple[str, str]]):
        self._table = table
        self._rows = list(rows)
        self._order_log = order_log
        self._order_column = None
        self._start = 0
        self._end = 0

    def select(self, _fields: str):
        return self

    def eq(self, _column: str, _value):
        return self

    def order(self, column: str):
        self._order_column = column
        self._order_log.append((self._table, column))
        return self

    def range(self, start: int, end: int):
        self._start = start
        self._end = end
        return self

    def execute(self):
        assert self._order_column is not None
        ordered = sorted(self._rows, key=lambda row: str(row.get(self._order_column) or ""))
        return SimpleNamespace(data=ordered[self._start : self._end + 1])


class _FakeClient:
    def __init__(self, rows_by_table: dict[str, list[dict]]):
        self._rows_by_table = rows_by_table
        self.order_log: list[tuple[str, str]] = []

    def table(self, name: str):
        return _FakeQuery(name, self._rows_by_table[name], self.order_log)


def test_persisted_ncaaf_loaders_page_by_unique_official_event_id(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_PAGE_SIZE", 2)
    same_kickoff = "2023-09-09T16:00:00+00:00"
    games = [
        {"official_event_id": event_id, "event_start_time": same_kickoff}
        for event_id in ["401523999", "401523995", "401523998", "401523996", "401523997"]
    ]
    features = [
        {"official_event_id": f"NCAAF:{event_id}", "event_start_time": same_kickoff}
        for event_id in ["401523999", "401523995", "401523998", "401523996", "401523997"]
    ]
    client = _FakeClient({
        "wow_ncaaf_training_games": games,
        "wow_d1_training_rows": features,
    })

    loaded_games = replay._load_ncaaf_persisted_game_rows(client)
    loaded_features = replay._load_ncaaf_persisted_feature_rows(client)

    assert [row["official_event_id"] for row in loaded_games] == sorted(row["official_event_id"] for row in games)
    assert [row["official_event_id"] for row in loaded_features] == sorted(row["official_event_id"] for row in features)
    assert len(loaded_games) == len(games)
    assert len(loaded_features) == len(features)
    assert set(client.order_log) == {
        ("wow_ncaaf_training_games", "official_event_id"),
        ("wow_d1_training_rows", "official_event_id"),
    }


def test_reference_ncaaf_replay_keeps_chronological_loader_order(monkeypatch):
    monkeypatch.setattr(replay, "PAGE_SIZE", 2)
    client = _FakeClient({
        "wow_ncaaf_training_games": [
            {"official_event_id": "2", "event_start_time": "2024-09-07T18:00:00+00:00"},
            {"official_event_id": "1", "event_start_time": "2024-08-31T18:00:00+00:00"},
        ]
    })

    rows = replay._load_ncaaf_game_rows(client)

    assert [row["official_event_id"] for row in rows] == ["1", "2"]
    assert set(client.order_log) == {("wow_ncaaf_training_games", "event_start_time")}



class ReadTimeout(Exception):
    pass


class _FlakyQuery(_FakeQuery):
    def __init__(self, table, rows, order_log, attempts):
        super().__init__(table, rows, order_log)
        self._attempts = attempts

    def execute(self):
        self._attempts[self._table] = self._attempts.get(self._table, 0) + 1
        if self._attempts[self._table] == 1:
            raise ReadTimeout("transient")
        return super().execute()


class _FlakyClient(_FakeClient):
    def __init__(self, rows_by_table):
        super().__init__(rows_by_table)
        self.attempts = {}

    def table(self, name: str):
        return _FlakyQuery(name, self._rows_by_table[name], self.order_log, self.attempts)


def test_persisted_ncaaf_loaders_retry_one_transient_read_timeout(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_READ_TIMEOUT_BACKOFF_SECONDS", 0.0)
    games = [{"official_event_id": "401", "event_start_time": "2026-09-01T00:00:00+00:00"}]
    features = [{"official_event_id": "NCAAF:401", "event_start_time": "2026-09-01T00:00:00+00:00"}]
    client = _FlakyClient({
        "wow_ncaaf_training_games": games,
        "wow_d1_training_rows": features,
    })

    assert replay._load_ncaaf_persisted_game_rows(client) == games
    assert replay._load_ncaaf_persisted_feature_rows(client) == features
    assert client.attempts == {
        "wow_ncaaf_training_games": 2,
        "wow_d1_training_rows": 2,
    }


def test_read_retry_preserves_non_timeout_failures(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_READ_TIMEOUT_BACKOFF_SECONDS", 0.0)

    class OtherFailure(Exception):
        pass

    class BrokenQuery:
        def execute(self):
            raise OtherFailure("no retry")

    try:
        replay._execute_read_with_retry(lambda: BrokenQuery(), retries=2)
    except OtherFailure:
        pass
    else:
        raise AssertionError("non-ReadTimeout failure must propagate unchanged")



def test_persisted_ncaaf_loaders_use_smaller_page_budget_than_shared_replay_default():
    assert replay.NCAAF_PERSISTED_PAGE_SIZE < replay.PAGE_SIZE
    assert replay.NCAAF_PERSISTED_PAGE_SIZE <= 200


def test_postgrest_statement_timeout_retried_on_read_only_query(monkeypatch):
    # SQLSTATE 57014 is delivered as PostgREST APIError/HTTP 500, not
    # httpx.ReadTimeout; this exact mismatch caused Oct 27 #1059.
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_READ_TIMEOUT_BACKOFF_SECONDS", 0.0)

    class APIError(Exception):
        def __init__(self, code):
            self.code = code
            super().__init__("database service URL and credential must remain private")

    calls = {"count": 0}

    class Query:
        def execute(self):
            calls["count"] += 1
            if calls["count"] == 1:
                raise APIError("57014")
            return SimpleNamespace(data=[{"official_event_id": "401871071"}])

    result = replay._execute_read_with_retry(lambda: Query(), retries=2)
    assert result.data == [{"official_event_id": "401871071"}]
    assert calls["count"] == 2


def test_postgrest_statement_timeout_retry_is_bounded_and_preserves_failure(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_READ_TIMEOUT_BACKOFF_SECONDS", 0.0)

    class APIError(Exception):
        def __init__(self, code):
            self.code = code
            super().__init__("do not expose transport payload")

    calls = {"count": 0}
    persistent = APIError("57014")

    class Query:
        def execute(self):
            calls["count"] += 1
            raise persistent

    try:
        replay._execute_read_with_retry(lambda: Query(), retries=2)
    except APIError as exc:
        assert exc is persistent
    else:
        raise AssertionError("persistent 57014 must propagate after bounded retries")
    assert calls["count"] == 3


def test_postgrest_non_timeout_api_error_never_retried(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_READ_TIMEOUT_BACKOFF_SECONDS", 0.0)

    class APIError(Exception):
        def __init__(self, code):
            self.code = code
            super().__init__("permissions failure")

    calls = {"count": 0}

    class Query:
        def execute(self):
            calls["count"] += 1
            raise APIError("42501")

    try:
        replay._execute_read_with_retry(lambda: Query(), retries=2)
    except APIError as exc:
        assert exc.code == "42501"
    else:
        raise AssertionError("non-timeout API errors must fail closed")
    assert calls["count"] == 1


def test_ncaaf_forward_fingerprint_index_matches_exact_latest_row_sort():
    # 2026-10-27 production validation: 16,533 qualifying rows were scanned
    # to return 1 newest event because official_event_id-only index could not
    # satisfy event_start_time DESC + official_event_id DESC ordering.
    engine_dir = Path(__file__).resolve().parents[1]
    ddl = (engine_dir / "migrations" / "20261027_ncaaf_spread_forward_freshness_order_index.sql").read_text()
    normalized = " ".join(ddl.lower().split())
    assert "create index if not exists wow_d1_training_rows_ncaaf_spread_freshness_order_idx" in normalized
    assert "on public.wow_d1_training_rows using btree" in normalized
    assert "sport, model_family, feature_schema_version, event_start_time desc, official_event_id desc" in normalized
    assert "drop index" not in normalized
    assert "drop table" not in normalized
    assert "update public.wow_d1_training_rows" not in normalized
    # Preserve previous official_event_id-ordered pagination index.
    previous = (engine_dir / "migrations" / "20260927_ncaaf_spread_forward_lookup_index.sql").read_text()
    assert "wow_d1_training_rows_ncaaf_spread_lookup_idx" in previous
