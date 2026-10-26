"""Research-only NBA POINTS snapshot persistence negative and repeat-safety tests."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from v17.nba_points_snapshot_persistence import (
    NBAPointsSnapshotPersistenceError, persist_validated_nba_points_snapshot,
)

NOW = datetime(2026, 10, 26, 17, tzinfo=timezone.utc)


class MockQuery:
    def __init__(self, db):
        self.db = db
        self.item = None
        self.key = None

    def select(self, columns):
        assert columns == "*"
        return self

    def eq(self, key, value):
        assert key == "source_snapshot_id"
        self.key = value
        return self

    def limit(self, n):
        assert n == 1
        return self

    def insert(self, item):
        self.item = deepcopy(item)
        return self

    def execute(self):
        class Result:
            def __init__(self, data):
                self.data = data
        if self.item is not None:
            self.db.write_count += 1
            if self.db.write_error:
                raise RuntimeError("DB insert refused")
            if self.item["source_snapshot_id"] in self.db.rows:
                raise RuntimeError("unique violation")
            self.db.rows[self.item["source_snapshot_id"]] = deepcopy(self.item)
            return Result([self.item])
        return Result([deepcopy(self.db.rows[self.key])] if self.key in self.db.rows else [])


class MockDB:
    def __init__(self):
        self.rows = {}
        self.write_count = 0
        self.write_error = False

    def table(self, table):
        assert table == "wow_prop_evidence_snapshots"
        return MockQuery(self)


def sample():
    sid = str(uuid4())
    snapshot = {
        "source_snapshot_id": sid, "captured_at": "2026-10-26T16:40:00Z",
        "event_id": "espn-987654", "event_start_time": "2026-10-26T20:00:00Z",
        "sport": "NBA", "player": "Example Player", "stat_type": "POINTS",
        "line": 20.5, "game_log": [10.0] * 10,
        "box_score_log": [{"event_id": f"prior-{i}", "points": 10} for i in range(10)],
        "role_status": {"status": "ACTIVE"}, "role_timestamp": "2026-10-26T16:20:00Z",
        "opportunity_ledger": {"status": "PASS"},
        "source_timestamps": {"NBA_LOG": "2026-10-26T16:30:00Z"},
        "hydration_status": "PASS", "blockers": [],
        "evidence_version": "PROP_EVIDENCE_V1", "can_execute": False,
    }
    return {
        "status": "SOURCE_VALIDATED_SNAPSHOT_NOT_PERSISTED",
        "write_performed": False, "can_execute": False, "probability_publishable": False,
        "rank_eligible": False, "source_snapshot_id": sid, "snapshot": snapshot,
        "source_identity": {"canonical_event_id": "espn-987654", "player_id": "nba-12",
            "board_line_provider": "EXACT_BOARD", "board_captured_at": "2026-10-26T16:35:00Z",
            "roster_captured_at": "2026-10-26T16:20:00Z"},
    }


def fail(code, receipt, *, db=None, now=NOW):
    with pytest.raises(NBAPointsSnapshotPersistenceError) as err:
        persist_validated_nba_points_snapshot(db or MockDB(), receipt, now=now)
    assert err.value.code == code


def test_first_persist_and_identical_retry_are_immutable():
    db, receipt = MockDB(), sample()
    first = persist_validated_nba_points_snapshot(db, receipt, now=NOW)
    assert first == {"status": "PERSISTED_RESEARCH_ONLY", "source_snapshot_id": receipt["source_snapshot_id"],
                     "write_performed": True, "probability_publishable": False, "can_execute": False}
    second = persist_validated_nba_points_snapshot(db, receipt, now=NOW)
    assert second["status"] == "ALREADY_PERSISTED_RESEARCH_ONLY"
    assert db.write_count == 1
    assert "calibrated_lower_bound" not in db.rows[receipt["source_snapshot_id"]]


def test_same_id_changed_exact_line_is_not_updated():
    db, receipt = MockDB(), sample()
    persist_validated_nba_points_snapshot(db, receipt, now=NOW)
    changed = deepcopy(receipt)
    changed["snapshot"]["line"] = 21.5
    fail("NBA_POINTS_IMMUTABLE_SNAPSHOT_CONFLICT", changed, db=db)
    assert db.write_count == 1


def test_started_event_must_not_be_stored():
    receipt = sample()
    fail("NBA_POINTS_SNAPSHOT_NO_LONGER_PREGAME", receipt,
         now=datetime(2026, 10, 26, 21, tzinfo=timezone.utc))


def test_unvalidated_receipt_fails_and_never_writes():
    receipt, db = sample(), MockDB()
    receipt["rank_eligible"] = True
    fail("NBA_POINTS_VALIDATED_RECEIPT_REQUIRED", receipt, db=db)
    assert db.write_count == 0


def test_mismatched_source_identity_rejected():
    receipt = sample()
    receipt["source_identity"]["canonical_event_id"] = "espn-999"
    fail("NBA_POINTS_SOURCE_IDENTITY_MISMATCH", receipt)


def test_invalid_uuid_rejected():
    receipt = sample()
    receipt["source_snapshot_id"] = receipt["snapshot"]["source_snapshot_id"] = "not-a-uuid"
    fail("NBA_POINTS_SNAPSHOT_ID_INVALID", receipt)


def test_db_failure_not_success():
    receipt, db = sample(), MockDB()
    db.write_error = True
    fail("NBA_POINTS_SNAPSHOT_INSERT_FAILED", receipt, db=db)
    assert db.write_count == 1


def test_missing_evidence_never_persists():
    receipt = sample()
    del receipt["snapshot"]["opportunity_ledger"]
    fail("NBA_POINTS_SNAPSHOT_FIELDS_MISSING", receipt)


def test_late_source_timestamp_rejected():
    receipt = sample()
    receipt["snapshot"]["source_timestamps"]["NBA_LOG"] = "2026-10-26T16:55:00Z"
    fail("NBA_POINTS_SOURCE_TIMESTAMP_INVALID", receipt)
