from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from v17 import daily_snapshot_runtime as subject


class ReadTimeout(Exception):
    pass


class _Query:
    def __init__(self, db):
        self.db = db

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        self.db.attempts += 1
        if self.db.attempts <= self.db.failures:
            raise self.db.error_type("transient")
        return SimpleNamespace(data=self.db.rows)


class _DB:
    def __init__(self, *, failures, error_type=ReadTimeout):
        self.failures = failures
        self.error_type = error_type
        self.attempts = 0
        self.rows = [{
            "official_event_id": "future-1",
            "event_start_time": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
            "home_team": "Home",
            "away_team": "Away",
            "snapshot_id": "snap-1",
        }]

    def table(self, _name):
        return _Query(self)


def test_team_rows_retries_transient_read_timeout_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(subject.time, "sleep", lambda seconds: sleeps.append(seconds))
    db = _DB(failures=2)

    rows = subject._team_rows(db, "2026-10-02", 24)

    assert db.attempts == 3
    assert sleeps == [0.5, 1.0]
    assert [row["official_event_id"] for row in rows] == ["future-1"]


def test_team_rows_preserves_read_timeout_after_bounded_retries(monkeypatch):
    monkeypatch.setattr(subject.time, "sleep", lambda _seconds: None)
    db = _DB(failures=3)

    with pytest.raises(ReadTimeout):
        subject._team_rows(db, "2026-10-02", 24)

    assert db.attempts == 3


def test_team_rows_does_not_retry_non_transient_query_failure(monkeypatch):
    class PermissionErrorLike(Exception):
        pass

    sleeps = []
    monkeypatch.setattr(subject.time, "sleep", lambda seconds: sleeps.append(seconds))
    db = _DB(failures=1, error_type=PermissionErrorLike)

    with pytest.raises(PermissionErrorLike):
        subject._team_rows(db, "2026-10-02", 24)

    assert db.attempts == 1
    assert sleeps == []
