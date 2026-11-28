from __future__ import annotations

from types import SimpleNamespace

import v17.spread_margin_replay as replay
from v17.spread_margin_challenger import SpreadChallengerUnavailable


class _FakeQuery:
    def __init__(self, table: str, rows: list[dict], order_log: list[tuple[str, str]], request_log: list[dict] | None = None):
        self._table = table
        self._rows = list(rows)
        self._order_log = order_log
        self._request_log = request_log
        self._order_column = None
        self._start = 0
        self._end = None
        self._gte: tuple[str, object] | None = None
        self._limit: int | None = None

    def select(self, _fields: str):
        return self

    def eq(self, _column: str, _value):
        return self

    def order(self, column: str):
        self._order_column = column
        self._order_log.append((self._table, column))
        return self

    def gte(self, column: str, value):
        self._gte = (column, value)
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def range(self, start: int, end: int):
        self._start = start
        self._end = end
        return self

    def execute(self):
        assert self._order_column is not None
        ordered = sorted(self._rows, key=lambda row: str(row.get(self._order_column) or ""))
        if self._request_log is not None:
            self._request_log.append({
                "table": self._table,
                "gte": self._gte,
                "limit": self._limit,
                "range": None if self._end is None else (self._start, self._end),
            })
        if self._gte is not None:
            column, value = self._gte
            ordered = [row for row in ordered if str(row.get(column) or "") >= str(value)]
        if self._limit is not None:
            return SimpleNamespace(data=ordered[self._start : self._start + self._limit])
        end = len(ordered) - 1 if self._end is None else self._end
        return SimpleNamespace(data=ordered[self._start : end + 1])


class _FakeClient:
    def __init__(self, rows_by_table: dict[str, list[dict]]):
        self._rows_by_table = rows_by_table
        self.order_log: list[tuple[str, str]] = []
        self.request_log: list[dict] = []

    def table(self, name: str):
        return _FakeQuery(name, self._rows_by_table[name], self.order_log, self.request_log)


def test_persisted_ncaaf_loaders_page_by_unique_official_event_id(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_PAGE_SIZE", 2)
    same_kickoff = "2023-09-09T16:00:00+00:00"
    games = [
        {"official_event_id": event_id, "event_start_time": same_kickoff}
        for event_id in ["401523999", "401523995", "401523998", "401523996", "401523997"]
    ]
    features = [
        {"training_row_id": f"row-{event_id}", "official_event_id": f"NCAAF:{event_id}", "event_start_time": same_kickoff}
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
    def __init__(self, table, rows, order_log, attempts, request_log=None):
        super().__init__(table, rows, order_log, request_log)
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
        return _FlakyQuery(name, self._rows_by_table[name], self.order_log, self.attempts, self.request_log)


def test_persisted_ncaaf_loaders_retry_one_transient_read_timeout(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_READ_TIMEOUT_BACKOFF_SECONDS", 0.0)
    games = [{"official_event_id": "401", "event_start_time": "2026-09-01T00:00:00+00:00"}]
    features = [{"training_row_id": "row-401", "official_event_id": "NCAAF:401", "event_start_time": "2026-09-01T00:00:00+00:00"}]
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


def _feature(event_id: str, row_id: str) -> dict:
    return {"training_row_id": row_id, "official_event_id": event_id}


def test_persisted_ncaaf_feature_loader_pages_with_forward_keyset_not_offset(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_PAGE_SIZE", 2)
    features = [_feature(f"NCAAF:{1000 + index}", f"row-{index}") for index in range(5)]
    client = _FakeClient({"wow_d1_training_rows": features})

    rows = replay._load_ncaaf_persisted_feature_rows(client)

    assert [row["official_event_id"] for row in rows] == [f"NCAAF:{1000 + index}" for index in range(5)]
    requests = client.request_log
    # No deep OFFSET is ever issued for the feature ledger.
    assert all(request["range"] is None for request in requests)
    assert all(request["limit"] == 2 for request in requests)
    assert requests[0]["gte"] is None
    assert [request["gte"][1] for request in requests[1:]] == [
        "NCAAF:1001",
        "NCAAF:1002",
        "NCAAF:1003",
        "NCAAF:1004",
    ]


def test_persisted_ncaaf_feature_loader_reads_a_duplicate_event_run_exactly_once(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_PAGE_SIZE", 2)
    features = [
        _feature("NCAAF:1", "row-1"),
        _feature("NCAAF:1", "row-2"),
        _feature("NCAAF:1", "row-3"),
        _feature("NCAAF:2", "row-4"),
        _feature("NCAAF:2", "row-5"),
    ]
    client = _FakeClient({"wow_d1_training_rows": features})

    rows = replay._load_ncaaf_persisted_feature_rows(client)

    # A page of 2 cannot clear the three-row NCAAF:1 run; that cursor widens
    # instead of dropping rows or looping.
    assert [row["training_row_id"] for row in rows] == ["row-1", "row-2", "row-3", "row-4", "row-5"]
    assert any(request["limit"] == 4 for request in client.request_log)


def test_persisted_ncaaf_feature_loader_fails_closed_when_a_cursor_run_exceeds_the_cap(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_PAGE_SIZE", 2)
    monkeypatch.setattr(replay, "KEYSET_PAGE_ESCALATION_MAX", 2)
    features = [_feature("NCAAF:1", f"row-{index}") for index in range(5)]
    client = _FakeClient({"wow_d1_training_rows": features})

    try:
        replay._load_ncaaf_persisted_feature_rows(client)
    except SpreadChallengerUnavailable as exc:
        assert exc.code == "SPREAD_REPLAY_PAGE_CURSOR_STALLED"
    else:
        raise AssertionError("a cursor run wider than the cap must fail closed")


def test_persisted_ncaaf_game_rows_keep_offset_pagination(monkeypatch):
    monkeypatch.setattr(replay, "NCAAF_PERSISTED_PAGE_SIZE", 2)
    games = [
        {"training_game_id": f"g-{index}", "official_event_id": f"{1000 + index}"}
        for index in range(3)
    ]
    client = _FakeClient({"wow_ncaaf_training_games": games})

    rows = replay._load_ncaaf_persisted_game_rows(client)

    assert [row["official_event_id"] for row in rows] == ["1000", "1001", "1002"]
    assert [request["range"] for request in client.request_log] == [(0, 1), (2, 3)]
    assert all(request["gte"] is None for request in client.request_log)
