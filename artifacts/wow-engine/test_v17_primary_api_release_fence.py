"""P0 primary API Release fence: contract, crash and provider fault injection.

A source-only or mock pass does not authorize production migration, release,
or a real Render API call.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from v17.primary_api_release_fence import (
    PRIMARY_SERVICE,
    PrimaryReleaseHold,
    post_render_exactly_once,
    record_authenticated_readback,
    reserve_single_post,
)


class FakeDb:
    autocommit = True

    def __init__(self):
        self.used = False
        self.committed = False
        self.pending = False
        self.result = ()
        self.io_authorized = True

    @contextmanager
    def transaction(self):
        self.pending = True
        try:
            yield
        except BaseException:
            self.pending = False
            raise
        else:
            self.committed = True
            self.pending = False

    def execute(self, sql, args):
        assert self.pending
        if "reserve_primary_api_post" in sql:
            if self.used:
                raise RuntimeError("PRIMARY_RELEASE_POST_ALREADY_RESERVED_RECONCILE_ONLY")
            self.used = True
            self.result = ("PRIMARY_RELEASE_POST_AUTHORIZED_ONCE",)
        elif "authorize_primary_api_post_io" in sql:
            if not self.io_authorized:
                raise RuntimeError("PRIMARY_RELEASE_DBA_AUTHORIZATION_MISSING")
            self.result = ("PRIMARY_RELEASE_IO_WINDOW_ATTESTED",)
        elif "observe_primary_api_deploy" in sql:
            self.result = ("PRIMARY_RELEASE_PROVIDER_READBACK_ONLY",)
        else:
            raise AssertionError("Wrong SQL RPC")
        return self

    def fetchone(self):
        return self.result


def test_exact_sha_post_only_after_durable_commit():
    db = FakeDb()
    events = []

    def render(body):
        assert db.committed and db.pending
        events.append(body)
        return {"id": "dep-abc123", "commit": {"id": "a" * 40}}

    receipt = post_render_exactly_once(
        db, render, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-release"
    )
    assert events == [{"commitId": "a" * 40, "clearCache": "do_not_clear"}]
    assert receipt["status"] == "POST_ACK_ONLY_REQUIRES_LIVE_READBACK"
    assert receipt["can_execute"] is False
    with pytest.raises(RuntimeError, match="RECONCILE_ONLY"):
        post_render_exactly_once(
            db, render, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-release"
        )
    assert len(events) == 1, "same SHA cannot issue second POST"


def test_lost_ack_burns_token_and_never_retries():
    db = FakeDb()
    events = []

    def lost_ack(body):
        assert db.committed
        events.append(body)
        raise TimeoutError("Render already received POST but response is lost")

    with pytest.raises(PrimaryReleaseHold, match="ACK_UNKNOWN_RECONCILE_ONLY"):
        post_render_exactly_once(
            db, lost_ack, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-ack"
        )
    with pytest.raises(RuntimeError, match="RECONCILE_ONLY"):
        post_render_exactly_once(
            db, lost_ack, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-ack"
        )
    assert len(events) == 1


@pytest.mark.parametrize("response", [
    None, [], {}, {"id": "dep-abc123"},
    {"id": "dep-abc123", "commit": {"id": "b" * 40}},
    {"id": "wrong", "commit": {"id": "a" * 40}},
])
def test_bad_or_missing_provider_ack_does_not_retry(response):
    db = FakeDb()
    called = []
    with pytest.raises(PrimaryReleaseHold, match="PROVIDER_"):
        post_render_exactly_once(
            db, lambda body: called.append(body) or response,
            service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-ack"
        )
    assert len(called) == 1 and db.used


@pytest.mark.parametrize("service,sha,owner", [
    ("srv-different", "a" * 40, "ci"),
    (PRIMARY_SERVICE, "a" * 39, "ci"),
    (PRIMARY_SERVICE, "A" * 40, "ci"),
    (PRIMARY_SERVICE, "a" * 40, "owner with spaces"),
    (PRIMARY_SERVICE, "a" * 40, ""),
])
def test_invalid_release_identity_never_mutates(service, sha, owner):
    db = FakeDb()
    with pytest.raises(PrimaryReleaseHold):
        reserve_single_post(db, service_id=service, sha=sha, run_id=owner)
    assert not db.used


def test_no_autocommit_prevents_hidden_outer_transaction_rollback():
    db = FakeDb()
    db.autocommit = False
    with pytest.raises(PrimaryReleaseHold, match="AUTOCOMMIT_REQUIRED"):
        reserve_single_post(db, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci")
    assert not db.used


def test_live_receipt_is_not_product_or_QA_pass():
    db = FakeDb()
    receipt = record_authenticated_readback(
        db, service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci",
        deploy_id="dep-test123", observed_sha="a" * 40, live=True,
    )
    assert receipt["status"] == "LIVE_OBSERVED_NOT_QA_VERIFIED"
    assert receipt["can_execute"] is False


def test_private_primary_api_migration_has_single_burn_and_no_seeded_approval():
    sql = (
        Path(__file__).resolve().parent /
        "v17/sql/primary_api_single_post_release_fence.sql"
    ).read_text()
    assert sql.count("create or replace function wow_release_control.reserve_primary_api_post(") == 1
    assert sql.count("create or replace function wow_release_control.assert_primary_api_authority()") == 1
    assert sql.count("'POST_UNCERTAIN'") >= 2
    assert "PRIMARY_RELEASE_POST_ALREADY_RESERVED_RECONCILE_ONLY" in sql
    assert "for share of ident,anchor" in sql
    assert "PRIMARY_RELEASE_CONTROLLER_PRIVILEGE_LEAK" in sql
    assert "commitId" not in sql  # provider interaction belongs outside PostgreSQL
    assert "insert into wow_release_control.primary_api_release_identity" not in sql



def test_primary_api_fence_disposable_postgres_two_connections_and_db_rights():
    """CI-only PostgreSQL lease, project proof and interrupted POST tests.

    The entire fixture uses the temporary wow-engine-verify database. It
    never reads or modifies a production Supabase project.
    """
    import os

    if os.environ.get("GITHUB_WORKFLOW") != "wow-engine-verify":
        pytest.skip("Requires disposable wow-engine-verify PostgreSQL")
    import psycopg

    dsn = "postgresql://wow_agent_ci:wow_agent_ci@localhost:5432/wow_agent_ci"
    ddl = (
        Path(__file__).resolve().parent /
        "v17/sql/primary_api_single_post_release_fence.sql"
    ).read_text()
    conn1 = psycopg.connect(dsn, connect_timeout=5, autocommit=False)
    conn2 = psycopg.connect(dsn, connect_timeout=5, autocommit=False)
    try:
        with conn1.cursor() as cur:
            cur.execute(
                "DO $roles$ BEGIN "
                "IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='anon') "
                "THEN CREATE ROLE anon NOLOGIN; END IF; "
                "IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='authenticated') "
                "THEN CREATE ROLE authenticated NOLOGIN; END IF; "
                "IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='wow_primary_release_controller') "
                "THEN CREATE ROLE wow_primary_release_controller LOGIN NOSUPERUSER NOBYPASSRLS; END IF; "
                "END $roles$;"
            )
            cur.execute("CREATE SCHEMA IF NOT EXISTS wow_release_control")
            cur.execute(
                "CREATE TABLE wow_release_control.release_control_project_anchor "
                "(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),"
                "expected_project_ref text NOT NULL,certified_by text NOT NULL,"
                "certified_at timestamptz NOT NULL)"
            )
            cur.execute(ddl, prepare=False)
            # Missing DBA approval MUST fail before any POST reservation is made.
            cur.execute("SET LOCAL ROLE wow_primary_release_controller")
            cur.execute("SAVEPOINT missing_approval")
            try:
                cur.execute(
                    "SELECT wow_release_control.reserve_primary_api_post(%s,%s,%s)",
                    (PRIMARY_SERVICE, "a" * 40, "ci-before-dba"),
                )
            except psycopg.errors.RaiseException as exc:
                assert "PRIMARY_RELEASE_DBA_AUTHORIZATION_MISSING" in str(exc)
                cur.execute("ROLLBACK TO SAVEPOINT missing_approval")
            else:
                raise AssertionError("DBA-unapproved primary deployment authorized")
            cur.execute("RESET ROLE")
            cur.execute(
                "INSERT INTO wow_release_control.release_control_project_anchor "
                "(singleton,expected_project_ref,certified_by,certified_at) "
                "VALUES(true,%s,'CI-DBA',now())",
                ("a" * 20,),
            )
            cur.execute(
                "INSERT INTO wow_release_control.primary_api_release_identity "
                "(singleton,project_ref,service_id,authorized,approved_by,approved_at) "
                "VALUES(true,%s,%s,true,'CI-DBA',now())",
                ("b" * 20, PRIMARY_SERVICE),
            )
            cur.execute("SET LOCAL ROLE wow_primary_release_controller")
            cur.execute("SAVEPOINT wrong_project")
            try:
                cur.execute(
                    "SELECT wow_release_control.reserve_primary_api_post(%s,%s,%s)",
                    (PRIMARY_SERVICE, "a" * 40, "ci-wrong-project"),
                )
            except psycopg.errors.RaiseException as exc:
                assert "PRIMARY_RELEASE_DBA_AUTHORIZATION_MISSING" in str(exc)
                cur.execute("ROLLBACK TO SAVEPOINT wrong_project")
            else:
                raise AssertionError("Wrong-project anchor authorized deployment")
            for name in ("primary_api_release_identity", "release_control_project_anchor"):
                cur.execute("SAVEPOINT privileged_update")
                try:
                    cur.execute(
                        f"UPDATE wow_release_control.{name} SET singleton=true"
                    )
                except psycopg.errors.InsufficientPrivilege:
                    cur.execute("ROLLBACK TO SAVEPOINT privileged_update")
                else:
                    raise AssertionError(f"Controller may UPDATE {name}")
            cur.execute("RESET ROLE")
            cur.execute(
                "UPDATE wow_release_control.primary_api_release_identity "
                "SET project_ref=%s WHERE singleton=true",
                ("a" * 20,),
            )
        conn1.commit()

        # Start a reservation but retain the transaction's per-service lock.
        with conn1.cursor() as cur:
            cur.execute("SET LOCAL ROLE wow_primary_release_controller")
            cur.execute(
                "SELECT wow_release_control.reserve_primary_api_post(%s,%s,%s)",
                (PRIMARY_SERVICE, "a" * 40, "ci-once"),
            )
            assert cur.fetchone() == ("PRIMARY_RELEASE_POST_AUTHORIZED_ONCE",)

        # An independent DBA revocation cannot commit while a one-shot
        # claim is being durably reserved. The SELECT FOR SHARE roots lock
        # the approval row until that reservation transaction commits.
        with conn2.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='350ms'")
            try:
                cur.execute(
                    "UPDATE wow_release_control.primary_api_release_identity "
                    "SET authorized=false WHERE singleton=true"
                )
            except psycopg.errors.LockNotAvailable:
                pass
            else:
                raise AssertionError("DBA revocation raced past active reservation")
        conn2.rollback()

        with conn2.cursor() as cur:
            cur.execute("SET LOCAL ROLE wow_primary_release_controller")
            cur.execute("SET LOCAL lock_timeout='350ms'")
            try:
                cur.execute(
                    "SELECT wow_release_control.reserve_primary_api_post(%s,%s,%s)",
                    (PRIMARY_SERVICE, "b" * 40, "ci-contender"),
                )
            except psycopg.errors.LockNotAvailable:
                pass
            else:
                raise AssertionError("Competing SHA bypassed transaction lock")
        conn2.rollback()
        conn1.commit()

        # The second authorization gate stays locked over network I/O:
        # a DBA revocation cannot slip between check and the provider POST.
        with conn1.cursor() as cur:
            cur.execute("SET LOCAL ROLE wow_primary_release_controller")
            cur.execute(
                "SELECT wow_release_control.authorize_primary_api_post_io(%s,%s,%s)",
                (PRIMARY_SERVICE, "a" * 40, "ci-once"),
            )
            assert cur.fetchone() == ("PRIMARY_RELEASE_IO_WINDOW_ATTESTED",)
        with conn2.cursor() as cur:
            cur.execute("SET LOCAL lock_timeout='350ms'")
            try:
                cur.execute(
                    "UPDATE wow_release_control.primary_api_release_identity "
                    "SET authorized=false WHERE singleton=true"
                )
            except psycopg.errors.LockNotAvailable:
                pass
            else:
                raise AssertionError("DBA revoked during an active post window")
        conn2.rollback()
        conn1.commit()

        # Durable burnt token rejects both same-SHA replay and another SHA.
        with conn2.cursor() as cur:
            cur.execute("SET LOCAL ROLE wow_primary_release_controller")
            for commit, owner in (("a" * 40, "ci-once"), ("b" * 40, "ci-contender")):
                cur.execute("SAVEPOINT duplicate_post")
                try:
                    cur.execute(
                        "SELECT wow_release_control.reserve_primary_api_post(%s,%s,%s)",
                        (PRIMARY_SERVICE, commit, owner),
                    )
                except psycopg.errors.RaiseException as exc:
                    assert "PRIMARY_RELEASE_POST_ALREADY_RESERVED_RECONCILE_ONLY" in str(exc)
                    cur.execute("ROLLBACK TO SAVEPOINT duplicate_post")
                else:
                    raise AssertionError("Second Render POST was authorized")
        conn2.rollback()

        with conn1.cursor() as cur:
            cur.execute(
                "SELECT target_sha,state,can_execute FROM "
                "wow_release_control.primary_api_deploy_intents "
                "WHERE service_id=%s",
                (PRIMARY_SERVICE,),
            )
            assert cur.fetchone() == ("a" * 40, "POST_UNCERTAIN", False)
            cur.execute(
                "UPDATE wow_release_control.primary_api_release_identity "
                "SET authorized=false WHERE singleton=true"
            )
        conn1.commit()
        with conn2.cursor() as cur:
            cur.execute("SET LOCAL ROLE wow_primary_release_controller")
            cur.execute("SAVEPOINT revoked_io")
            try:
                cur.execute(
                    "SELECT wow_release_control.authorize_primary_api_post_io(%s,%s,%s)",
                    (PRIMARY_SERVICE, "a" * 40, "ci-once"),
                )
            except psycopg.errors.RaiseException as exc:
                assert "PRIMARY_RELEASE_DBA_AUTHORIZATION_MISSING" in str(exc)
                cur.execute("ROLLBACK TO SAVEPOINT revoked_io")
            else:
                raise AssertionError("Revoked DBA authority still permitted a POST")
            cur.execute("SAVEPOINT revoked")
            try:
                cur.execute(
                    "SELECT wow_release_control.observe_primary_api_deploy("
                    "%s,%s,%s,%s,%s,%s)",
                    (PRIMARY_SERVICE,"a" * 40,"ci-once","dep-ci123","a" * 40,True),
                )
            except psycopg.errors.RaiseException as exc:
                assert "PRIMARY_RELEASE_DBA_AUTHORIZATION_MISSING" in str(exc)
                cur.execute("ROLLBACK TO SAVEPOINT revoked")
            else:
                raise AssertionError("Revoked DBA authorization approved readback")
        conn2.rollback()
    finally:
        conn1.rollback()
        conn2.rollback()
        conn1.close()
        conn2.close()


def test_dba_revoked_after_reservation_prevents_network_post():
    db = FakeDb()
    calls = []
    db.io_authorized = False
    with pytest.raises(RuntimeError, match="PRIMARY_RELEASE_DBA_AUTHORIZATION_MISSING"):
        post_render_exactly_once(
            db, lambda body: calls.append(body),
            service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-revoked",
        )
    assert db.used and db.committed and not calls
    with pytest.raises(RuntimeError, match="RECONCILE_ONLY"):
        post_render_exactly_once(
            db, lambda body: calls.append(body),
            service_id=PRIMARY_SERVICE, sha="a" * 40, run_id="ci-revoked",
        )
    assert not calls, "Revocation after reserve must never allow a second attempt"
