"""Offline behavioral contract only; actual Postgres concurrency remains mandatory."""
import importlib.util
import sys
from pathlib import Path
import threading
from datetime import datetime, timezone

import pytest

ENGINE = Path(__file__).resolve().parents[1]
SRC = ENGINE / "v17" / "render_release_service_fence_candidate.py"
MIGRATION = ENGINE / "migrations" / "20261018_release_service_state_fence_REVIEW_ONLY.sql"
spec = importlib.util.spec_from_file_location("render_release_service_fence_candidate", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
SHA = "a" * 40
SERVICE = "srv-da7sa9gu01pc73brt80g"
LEASE = m.Lease(SERVICE, SHA, "3865:1", "worker-A", 17, "RESERVED", None)


class FakePost:
    def __init__(self):
        self.lock = threading.Lock()
        self.reserved = False

    def reserve_single_post(self, lease):
        with self.lock:
            if self.reserved:
                raise m.ReleaseHold("RELEASE_POST_ALREADY_RESERVED_OR_FENCED")
            self.reserved = True


class Cursor:
    """Intentionally simplistic protocol fake; no PostgreSQL lock simulation."""
    def __init__(self, db):
        self.db = db
        self.row = None
        self.rows = []

    def __enter__(self): return self
    def __exit__(self, *args): return False

    def execute(self, sql, params):
        self.db.statements.append(sql)
        if "INSERT INTO wow_release_control.service_fence" in sql:
            self.row = None
        elif "SELECT fence, lease_expires_at > clock_timestamp()" in sql:
            self.row = (self.db.fence, self.db.lease_active)
        elif "SELECT target_sha, state" in sql:
            self.rows = [(s, state) for s, state in self.db.intents.items()
                         if state in {"RESERVED","POST_UNCERTAIN","PROVIDER_ACK","LIVE_OBSERVED"}]
        elif "SELECT 1 FROM wow_release_control.release_intent" in sql:
            self.row = (1,) if params[1] in self.db.intents else None
        elif "UPDATE wow_release_control.service_fence" in sql:
            self.db.fence += 1
            self.row = (self.db.fence,)
        elif "INSERT INTO wow_release_control.release_intent" in sql:
            sha = params[1]
            self.row = None if sha in self.db.intents else ("RESERVED",)
            if self.row: self.db.intents[sha] = "RESERVED"
        else:
            raise AssertionError("Unexpected SQL: " + sql[:100])

    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class DB:
    def __init__(self, *, intents=None, lease_active=False):
        self.fence = 4
        self.intents = dict(intents or {})
        self.lease_active = lease_active
        self.statements = []

    def cursor(self): return Cursor(self)
    def transaction(self):
        db = self
        class Tx:
            def __enter__(self):
                self.prior = (db.fence, db.intents.copy())
            def __exit__(self, t, e, tb):
                if t: db.fence, db.intents = self.prior
                return False
        return Tx()


def test_rejects_non_exact_sha():
    with pytest.raises(m.ReleaseHold, match="RELEASE_TARGET_SHA_INVALID"):
        m._validate(SERVICE, "123", "run1", "engineer")


def test_rejects_incorrect_service():
    with pytest.raises(m.ReleaseHold, match="RELEASE_SERVICE_ID_INVALID"):
        m._validate("bad", SHA, "run1", "engineer")


@pytest.mark.parametrize("state", ["RESERVED","POST_UNCERTAIN","PROVIDER_ACK","LIVE_OBSERVED"])
@pytest.mark.parametrize("same_sha", [True, False])
def test_old_unresolved_intent_survives_expired_lease(state, same_sha):
    db = DB(intents={SHA if same_sha else "b"*40: state})
    with pytest.raises(m.ReleaseHold, match="RELEASE_PREVIOUS_INTENT_UNRESOLVED_NO_REPOST"):
        m.ReleaseFence(db).claim(SERVICE, SHA, "newrun", "engineer")
    assert db.fence == 4


def test_verified_sha_cannot_repost():
    with pytest.raises(m.ReleaseHold, match="RELEASE_EXISTING_INTENT_NO_REPOST"):
        m.ReleaseFence(DB(intents={SHA:"VERIFIED"})).claim(SERVICE, SHA, "run2", "engineer")


def test_different_sha_requires_prior_explicit_verification():
    db = DB(intents={SHA:"VERIFIED"})
    lease = m.ReleaseFence(db).claim(SERVICE, "c"*40, "run2", "engineer")
    assert lease.fence == 5
    assert db.intents["c"*40] == "RESERVED"


def test_same_owner_new_run_never_steals_active_lease():
    with pytest.raises(m.ReleaseHold, match="RELEASE_SERVICE_LEASE_BUSY"):
        m.ReleaseFence(DB(lease_active=True)).claim(SERVICE, SHA, "newrun", "engineer")


def test_service_lock_precedes_intent_scan_and_insert():
    db = DB()
    m.ReleaseFence(db).claim(SERVICE, SHA, "run1", "engineer")
    s = db.statements
    assert next(i for i,x in enumerate(s) if "service_fence" in x and "FOR UPDATE" in x) < next(i for i,x in enumerate(s) if "SELECT target_sha, state" in x) < next(i for i,x in enumerate(s) if "INSERT INTO wow_release_control.release_intent" in x)


def test_exact_sha_post_once():
    fence = FakePost()
    sent = []
    m.invoke_single_render_post(LEASE, fence, lambda p: sent.append(p))
    with pytest.raises(m.ReleaseHold, match="RELEASE_POST_ALREADY_RESERVED_OR_FENCED"):
        m.invoke_single_render_post(LEASE, fence, lambda p: sent.append(p))
    assert sent == [{"clearCache":"do_not_clear", "commitId":SHA}]


def test_lost_ack_never_reposts():
    fence = FakePost()
    sent = []
    def lost(payload):
        sent.append(payload)
        raise TimeoutError("ACK lost")
    with pytest.raises(m.ReleaseHold, match="RELEASE_PROVIDER_POST_ACK_UNKNOWN_RECONCILE_ONLY"):
        m.invoke_single_render_post(LEASE, fence, lost)
    with pytest.raises(m.ReleaseHold, match="RELEASE_POST_ALREADY_RESERVED_OR_FENCED"):
        m.invoke_single_render_post(LEASE, fence, lost)
    assert len(sent) == 1


def test_concurrent_attempts_only_one_fake_post():
    fence = FakePost()
    called = []
    result = []
    def worker():
        try:
            m.invoke_single_render_post(LEASE, fence, lambda p: called.append(p))
            result.append("SENT")
        except m.ReleaseHold:
            result.append("HOLD")
    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert result.count("SENT") == 1
    assert len(called) == 1


def test_private_migration_has_per_service_guard_and_no_api_grants():
    sql = MIGRATION.read_text()
    assert "release_one_unresolved_per_service" in sql
    assert "release_intent(service_id)" in sql
    assert "RELEASE_INTENT_IDENTITY_IMMUTABLE" in sql
    assert "RELEASE_INTENT_STATE_NOT_MONOTONIC" in sql
    assert "RELEASE_INTENT_DELETE_FORBIDDEN" in sql
    assert "REVOKE ALL ON FUNCTION wow_release_control.protect_intent()" in sql
    assert "GRANT EXECUTE" not in sql


def test_invalid_provider_identity_refuses_reconciliation_before_db():
    fence = m.ReleaseFence(None)
    with pytest.raises(m.ReleaseHold, match="RELEASE_PROVIDER_IDENTITY_MISMATCH"):
        fence.reconcile_provider(LEASE, deploy_id="oops", deployed_sha=SHA,
                                 provider_created_at=datetime.now(timezone.utc), candidate_count=1)


def test_implicit_transaction_connection_cannot_reserve_render_post():
    class UncommittedConnection:
        autocommit = False
    with pytest.raises(m.ReleaseHold, match="RELEASE_DB_AUTOCOMMIT_REQUIRED_BEFORE_RENDER_POST"):
        m.ReleaseFence(UncommittedConnection())
