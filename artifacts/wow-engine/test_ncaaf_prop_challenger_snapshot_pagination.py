"""Research-only CFBD historical pagination: exact-once, no silent row loss."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17.ncaaf_prop_challenger_runner import (
    CAN_EXECUTE,
    NCAAFPropChallengerRunnerError,
    load_source_snapshots,
)


def _row(number: int) -> dict:
    return {
        "source_snapshot_id": f"00000000-0000-0000-0000-{number:012d}",
        "provider": "CFBD",
        "endpoint": "/games/players",
        "season": 2025,
        "created_at": f"2026-10-29T15:00:{number:02d}+00:00",
        "week": number,
        "requested_at": "2026-10-29T15:00:00+00:00",
        "retrieved_at": "2026-10-29T15:01:00+00:00",
        "request_params": {},
        "response_rows": [],
        "response_row_count": 0,
        "payload_sha256": "0" * 64,
        "acquisition_status": "EMPTY",
        "blocker_codes": ["NCAAF_SOURCE_EMPTY"],
        "can_execute": False,
    }


class _Query:
    def __init__(self, db):
        self.db = db
        self.after = None
        self.limit = None
        self.fence = False
        self.cutoff = None

    def select(self, fields):
        assert fields == "created_at" or "source_snapshot_id" in fields
        self.fence = fields == "created_at"
        return self

    def eq(self, field, value):
        assert (field, value) in (("provider", "CFBD"), ("endpoint", "/games/players"))
        return self

    def in_(self, field, values):
        assert field == "season"
        assert values == [2025]
        return self

    def order(self, field, desc=False):
        assert (field, desc) in (("created_at", True), ("source_snapshot_id", False))
        return self

    def limit(self, count):
        assert self.fence and count == 1
        return self

    def lte(self, field, value):
        assert field == "created_at"
        self.cutoff = value
        return self

    def gt(self, field, value):
        assert field == "source_snapshot_id"
        self.after = value
        return self

    def range(self, start, stop):
        assert start == 0
        self.limit = stop + 1
        return self

    def execute(self):
        if self.fence:
            ordered = sorted(
                (r for r in self.db.rows if isinstance(r, dict) and "created_at" in r),
                key=lambda r: r["created_at"],
                reverse=True,
            )
            fence_rows = [{"created_at": ordered[0]["created_at"]}] if ordered else []
            if self.db.new_row_after_fence is not None:
                self.db.rows.append(self.db.new_row_after_fence)
                self.db.new_row_after_fence = None
            return SimpleNamespace(data=fence_rows)
        self.db.calls.append((self.after, self.limit))
        rows = self.db.rows
        if self.cutoff is not None:
            rows = [r for r in rows if not isinstance(r, dict)
                    or r.get("created_at", "") <= self.cutoff]
        if self.after is not None:
            rows = [r for r in rows if r["source_snapshot_id"] > self.after]
        return SimpleNamespace(data=rows[: self.limit])


class _Db:
    def __init__(self, rows, new_row_after_fence=None):
        self.rows = rows
        self.calls = []
        self.new_row_after_fence = new_row_after_fence

    def table(self, name):
        assert name == "wow_ncaaf_source_snapshots"
        return _Query(self)


def test_keyset_pagination_is_exact_once_across_three_pages():
    db = _Db([_row(n) for n in range(1, 6)])
    output = load_source_snapshots(db, endpoint="/games/players", seasons=[2025], page_size=2)
    assert len(output) == 5
    assert [r.week for r in output] == [1, 2, 3, 4, 5]
    assert db.calls == [
        (None, 2),
        (_row(2)["source_snapshot_id"], 2),
        (_row(4)["source_snapshot_id"], 2),
    ]
    assert CAN_EXECUTE is False
    assert all(x.can_execute is False for x in output)


def test_new_source_snapshot_during_paging_is_outside_frozen_corpus():
    old = [_row(n) for n in range(1, 6)]
    newly_committed = _row(30)
    newly_committed["created_at"] = "2026-10-30T10:00:00+00:00"
    db = _Db(old, new_row_after_fence=newly_committed)
    output = load_source_snapshots(db, endpoint="/games/players", seasons=[2025], page_size=2)
    assert [r.week for r in output] == [1, 2, 3, 4, 5]
    assert len(db.rows) == 6  # a concurrent insert was simulated
    assert len(output) == 5    # frozen source corpus is unchanged


def test_repeated_page_fails_closed_instead_of_double_counting():
    # The adapter must reject a provider that repeats previously returned IDs.
    db = _Db([_row(2), _row(1)])
    with pytest.raises(NCAAFPropChallengerRunnerError) as err:
        load_source_snapshots(db, endpoint="/games/players", seasons=[2025], page_size=2)
    assert (err.value.code, err.value.detail) == ("NCAAF_PROP_SOURCE_READ_INVALID", "PAGE_ORDER_INVALID")


def test_missing_snapshot_id_fails_closed():
    record = _row(1)
    record.pop("source_snapshot_id")
    db = _Db([record])
    with pytest.raises(NCAAFPropChallengerRunnerError) as err:
        load_source_snapshots(db, endpoint="/games/players", seasons=[2025], page_size=2)
    assert (err.value.code, err.value.detail) == ("NCAAF_PROP_SOURCE_READ_INVALID", "SNAPSHOT_ID_INVALID")


def test_nonmapping_row_fails_closed_instead_of_being_dropped():
    db = _Db(["not-a-snapshot"])
    with pytest.raises(NCAAFPropChallengerRunnerError) as err:
        load_source_snapshots(db, endpoint="/games/players", seasons=[2025], page_size=2)
    assert (err.value.code, err.value.detail) == ("NCAAF_PROP_SOURCE_READ_INVALID", "ROW_INVALID")


@pytest.mark.parametrize("page_size", [0, -1, 1001, True, 1.5])
def test_invalid_page_size_never_touches_database(page_size):
    db = _Db([_row(1)])
    with pytest.raises(NCAAFPropChallengerRunnerError) as err:
        load_source_snapshots(db, endpoint="/games/players", seasons=[2025], page_size=page_size)
    assert (err.value.code, err.value.detail) == ("NCAAF_PROP_SOURCE_READ_INVALID", "PAGE_SIZE_INVALID")
    assert not db.calls


def test_empty_seasons_fail_closed_without_fallback():
    with pytest.raises(NCAAFPropChallengerRunnerError) as err:
        load_source_snapshots(_Db([]), endpoint="/games/players", seasons=[], page_size=2)
    assert (err.value.code, err.value.detail) == ("NCAAF_PROP_SOURCE_READ_INVALID", "SEASONS_EMPTY")
