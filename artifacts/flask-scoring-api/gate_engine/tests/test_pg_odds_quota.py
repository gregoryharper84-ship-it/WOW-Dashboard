"""
Tests for gate_engine/pg_odds_quota.py — the cross-worker Odds API quota
state module.

These tests do NOT require a live DATABASE_URL: every public function is
fail-open by contract (returns False / {} rather than raising), so we
verify that contract directly rather than standing up a real Postgres
instance in CI. If DATABASE_URL happens to be set in the environment
these tests run in, ensure_table_exists()/persist/fetch will exercise the
real DB path too — either way the assertions below hold.
"""
import pytest

from gate_engine import pg_odds_quota as oq


def test_advisory_lock_key_distinct_from_other_workers():
    # 778597299 = settlement worker, 778597203 = LLP cron (see settlement_worker.py)
    assert oq.ADVISORY_LOCK_KEY not in (778597299, 778597203)


def test_get_conn_raises_cleanly_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        oq._get_conn()


def test_persist_quota_update_fails_open_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = oq.persist_quota_update("paid", 30, 970, True)
    assert result is False  # never raises, returns False on failure


def test_fetch_quota_snapshot_fails_open_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    snap = oq.fetch_quota_snapshot()
    assert snap == {}  # never raises, returns {} on failure


def test_persist_quota_update_fails_open_on_bad_url():
    # Malformed DSN should be swallowed, not raised.
    result = oq.persist_quota_update(
        "paid", 30, 970, True, conn_string="postgresql://bad:bad@127.0.0.1:1/nope"
    )
    assert result is False


def test_fetch_quota_snapshot_fails_open_on_bad_url():
    snap = oq.fetch_quota_snapshot(conn_string="postgresql://bad:bad@127.0.0.1:1/nope")
    assert snap == {}
