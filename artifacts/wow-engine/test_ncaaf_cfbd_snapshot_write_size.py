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
    def __init__(self, *, fail_week=None, invalid_ack_week=None):
        self.calls = []
        self.fail_week = fail_week
        self.invalid_ack_week = invalid_ack_week
        self.pending = None

    def table(self, name):
        assert name == "wow_ncaaf_source_snapshots"
        return self

    def upsert(self, row, *, on_conflict):
        assert isinstance(row, dict), "never use a bulk season upsert"
        assert on_conflict == "provider,endpoint,season,week,payload_sha256"
        self.pending = row
        self.calls.append(row)
        return self

    def execute(self):
        if self.pending["week"] == self.fail_week:
            raise RuntimeError("TRANSIENT_SUPABASE_520")
        if self.pending["week"] == self.invalid_ack_week:
            return SimpleNamespace(data=[])
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
