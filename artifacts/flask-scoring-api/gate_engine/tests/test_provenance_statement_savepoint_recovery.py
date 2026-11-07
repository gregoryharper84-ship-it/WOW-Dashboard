"""Reproduce PostgreSQL aborted-transaction behavior in provenance DDL."""

from gate_engine.source_provenance.schema_migration import (
    _PROVENANCE_COLUMNS,
    _PROVENANCE_INDEXES,
    run_provenance_migration,
)


class Cursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql):
        c = self.conn
        if sql.startswith("ROLLBACK TO SAVEPOINT"):
            if c.fail_rollback:
                raise RuntimeError("SAVEPOINT_ROLLBACK_FAILED")
            c.pending = c.pending[:c.savepoint]
            c.aborted = False
            c.rollback_to_savepoint_count += 1
            return
        if c.aborted:
            raise RuntimeError("IN_FAILED_SQL_TRANSACTION")
        if sql.startswith("SAVEPOINT"):
            c.savepoint = len(c.pending)
            c.savepoint_count += 1
            return
        if sql.startswith("RELEASE SAVEPOINT"):
            c.release_count += 1
            return
        if c.fail_once and c.fail_once in sql:
            c.fail_once = None
            c.aborted = True
            raise RuntimeError("SIMULATED_LOCK_TIMEOUT")
        c.pending.append(sql)

    def close(self):
        pass


class Connection:
    def __init__(self, fail_once=None, fail_rollback=False):
        self.fail_once = fail_once
        self.fail_rollback = fail_rollback
        self.pending, self.committed = [], []
        self.savepoint = None
        self.aborted = False
        self.savepoint_count = 0
        self.release_count = 0
        self.rollback_to_savepoint_count = 0
        self.rollback_count = 0

    def cursor(self):
        return Cursor(self)

    def commit(self):
        # PostgreSQL treats COMMIT on an aborted transaction as ROLLBACK.
        if not self.aborted:
            self.committed.extend(self.pending)
        self.pending.clear()
        self.aborted = False

    def rollback(self):
        self.pending.clear()
        self.aborted = False
        self.rollback_count += 1


def test_lock_timeout_preserves_prior_columns_and_allows_later_ddl():
    # RED on old source: no savepoint, so first failed ALTER poisons COMMIT.
    db = Connection(fail_once="uac_evidence_packets ADD COLUMN IF NOT EXISTS observed_at")
    out = run_provenance_migration(db)
    assert out["success"] is False
    assert len(out["errors"]) == 1
    assert "uac_evidence_packets.observed_at" in out["errors"][0]
    assert "uac_evidence_packets.observed_at" not in out["columns_added"]
    assert any("ALTER TABLE llp_source_snapshots" in q for q in db.committed)
    assert any("uac_evidence_packets ADD COLUMN IF NOT EXISTS effective_at" in q
               for q in db.committed)
    assert out["fk_applied"] is True
    assert db.rollback_to_savepoint_count == 1
    assert db.rollback_count == 0


def test_each_statement_is_isolated_without_loss_on_clean_run():
    db = Connection()
    out = run_provenance_migration(db)
    count = 2 * len(_PROVENANCE_COLUMNS) + len(_PROVENANCE_INDEXES) + 1
    assert out["success"] is True
    assert not out["errors"]
    assert len(out["columns_added"]) == 2 * len(_PROVENANCE_COLUMNS)
    assert out["indexes_added"] == len(_PROVENANCE_INDEXES)
    assert out["fk_applied"] is True
    assert len(db.committed) == count
    assert db.savepoint_count == db.release_count == count
    assert db.rollback_to_savepoint_count == 0


def test_savepoint_rollback_failure_fails_closed_without_commit():
    db = Connection(fail_once="uac_evidence_packets ADD COLUMN IF NOT EXISTS observed_at",
                    fail_rollback=True)
    out = run_provenance_migration(db)
    assert out["success"] is False
    assert any("fatal: PROVENANCE_SAVEPOINT_ROLLBACK_FAILED" == e for e in out["errors"])
    assert db.rollback_count == 1
    assert not db.committed
