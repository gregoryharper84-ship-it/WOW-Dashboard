"""Regression for HTTP 520 on oversized NCAAF CFBD player-history bulk upsert."""
from types import SimpleNamespace

import pytest

from ncaaf_cfbd_hydrator import SourceSnapshot, persist_source_snapshots


def snap(week):
    return SourceSnapshot(
        provider="CFBD", endpoint="/games/players", season=2023, week=week,
        requested_at="2026-10-29T15:00:00Z", retrieved_at="2026-10-29T15:00:05Z",
        request_params={"year": 2023, "week": week}, response_rows=[{"id": week}],
        response_row_count=1, payload_sha256=f"{week:064x}",
        acquisition_status="AVAILABLE", blocker_codes=[],
    )


class FakeStorage:
    def __init__(self, *, fail_week=None, invalid_ack_week=None, stored=None):
        self.calls = []
        self.fail_week = fail_week
        self.invalid_ack_week = invalid_ack_week
        self.stored = [dict(row) for row in (stored or [])]
        self.pending = None
        self.mode = None
        self.filters = []

    def table(self, name):
        assert name == "wow_ncaaf_source_snapshots"
        self.mode = None
        self.filters = []
        self.pending = None
        return self

    def select(self, _columns):
        self.mode = "select"
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def is_(self, field, value):
        self.filters.append((field, None if value == "null" else value))
        return self

    def insert(self, row):
        assert isinstance(row, dict), "never use a bulk season insert"
        self.mode = "insert"
        self.pending = row
        self.calls.append(row)
        return self

    def _matches(self, row):
        return all(row.get(field) == value for field, value in self.filters)

    def execute(self):
        if self.mode == "select":
            return SimpleNamespace(data=[row for row in self.stored if self._matches(row)])
        if self.pending["week"] == self.fail_week:
            raise RuntimeError("TRANSIENT_SUPABASE_520")
        if self.pending["week"] == self.invalid_ack_week:
            return SimpleNamespace(data=[])
        self.stored.append(dict(self.pending))
        return SimpleNamespace(data=[self.pending])


def test_isolated_week_writes_and_exact_source_hashes():
    storage = FakeStorage()
    count = persist_source_snapshots(storage, [snap(i) for i in range(1, 21)])
    assert count == 20
    assert [r["week"] for r in storage.calls] == list(range(1, 21))
    assert all(r["can_execute"] is False for r in storage.calls)
    assert [r["payload_sha256"] for r in storage.calls] == [
        f"{week:064x}" for week in range(1, 21)
    ]


def test_partial_success_persists_first_week_and_fails_closed_on_second():
    store = FakeStorage(fail_week=2)
    with pytest.raises(RuntimeError, match="TRANSIENT_SUPABASE_520"):
        persist_source_snapshots(store, [snap(1), snap(2), snap(3)])
    assert [x["week"] for x in store.calls] == [1, 2]


def test_missing_api_ack_cannot_be_misreported_as_success():
    store = FakeStorage(invalid_ack_week=2)
    with pytest.raises(RuntimeError, match="NCAAF_SOURCE_SNAPSHOT_WRITE_ACK_INVALID"):
        persist_source_snapshots(store, [snap(1), snap(2), snap(3)])
    assert [x["week"] for x in store.calls] == [1, 2]


def test_empty_snapshots_do_not_call_storage():
    storage = FakeStorage()
    assert persist_source_snapshots(storage, []) == 0
    assert storage.calls == []


def test_identical_snapshot_is_acknowledged_without_a_second_insert():
    original = snap(4)
    storage = FakeStorage(stored=[{
        "provider": "CFBD",
        "endpoint": "/games/players",
        "season": 2023,
        "week": 4,
        "payload_sha256": f"{4:064x}",
        "response_row_count": 1,
        "acquisition_status": "AVAILABLE",
        "blocker_codes": [],
        "can_execute": False,
        "retrieved_at": "2026-10-01T00:00:00+00:00",
    }])
    assert persist_source_snapshots(storage, [original]) == 1
    assert storage.calls == []
    assert storage.stored[0]["retrieved_at"] == "2026-10-01T00:00:00+00:00"


def test_same_hash_with_different_blockers_is_not_rewritten():
    row = snap(4)
    storage = FakeStorage(stored=[{
        "provider": "CFBD",
        "endpoint": "/games/players",
        "season": 2023,
        "week": 4,
        "payload_sha256": f"{4:064x}",
        "response_row_count": 1,
        "acquisition_status": "AVAILABLE",
        "blocker_codes": ["DIFFERENT"],
        "can_execute": False,
    }])
    with pytest.raises(RuntimeError, match="NCAAF_SOURCE_SNAPSHOT_IMMUTABLE_CONFLICT"):
        persist_source_snapshots(storage, [row])
    assert storage.calls == []


def test_null_week_replay_does_not_insert_a_second_season_receipt():
    row = SourceSnapshot(
        provider="CFBD", endpoint="/games", season=2026, week=None,
        requested_at="2026-11-07T00:00:00+00:00",
        retrieved_at="2026-11-07T00:00:01+00:00",
        request_params={"year": 2026}, response_rows=[{"id": 1}],
        response_row_count=1, payload_sha256="b" * 64,
        acquisition_status="AVAILABLE", blocker_codes=[],
    )
    storage = FakeStorage(stored=[{
        "provider": "CFBD", "endpoint": "/games", "season": 2026, "week": None,
        "payload_sha256": "b" * 64, "response_row_count": 1,
        "acquisition_status": "AVAILABLE", "blocker_codes": [], "can_execute": False,
    }])
    assert persist_source_snapshots(storage, [row]) == 1
    assert storage.calls == []
