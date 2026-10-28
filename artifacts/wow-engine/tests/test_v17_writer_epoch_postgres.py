"""Actual PostgreSQL protocol/race tests, restricted to an ephemeral local DB.

This module provisions ONLY the isolated writer fixture. It cannot accept a
remote host, arbitrary libpq options, production database or service credential.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

DSN = os.environ.get("WOW_WRITER_ISOLATED_TEST_DSN")
if not DSN or os.environ.get("WOW_WRITER_ISOLATED_TEST_ATTESTATION") != "NONPRODUCTION_EPHEMERAL_ONLY":
    pytest.skip("Isolated writer PostgreSQL not configured", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict

info = conninfo_to_dict(DSN)
if (set(info) - {"host", "port", "dbname", "user", "password"}
        or info.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or info.get("dbname") != "wow_release_fence_test"):
    raise RuntimeError("WRITER_ISOLATED_DB_IDENTITY_UNVERIFIED")

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/20261027_writer_mutation_fence_REVIEW_ONLY.sql"
OWNER = "github-actions:worker:123"


def connection():
    return psycopg.connect(DSN, autocommit=True, connect_timeout=10,
                           options="-c statement_timeout=5000 -c lock_timeout=2000")


@pytest.fixture(scope="module", autouse=True)
def install_isolated_writer_fixture():
    with connection() as db:
        # The legacy table shape was checked read-only against the designated
        # project; this fixture does not claim to attest its production grants.
        db.execute("""DO $$ BEGIN
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
          IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
        END $$;
        CREATE TABLE public.wow_engineering_incident_writer_leases (
          incident_id bigint PRIMARY KEY, owner_id text NOT NULL,
          epoch bigint NOT NULL, lease_group text NOT NULL,
          branch text NOT NULL DEFAULT '', pr_number bigint,
          state text NOT NULL, acquired_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
        );
        GRANT ALL ON public.wow_engineering_incident_writer_leases TO service_role;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO service_role;""")
        db.execute(MIGRATION.read_text())
        yield
        # No production cleanup path: all objects live in the service container.


@pytest.fixture(autouse=True)
def clean_isolated_rows():
    with connection() as db:
        db.execute("TRUNCATE public.wow_engineering_incident_writer_leases CASCADE")


def claim(db, incident=1739, epoch=1, owner=OWNER, group="GLOBAL"):
    db.execute("""INSERT INTO public.wow_engineering_incident_writer_leases
        (incident_id,owner_id,epoch,lease_group,state,acquired_at,expires_at,updated_at)
        VALUES (%s,%s,%s,%s,'HELD',clock_timestamp(),clock_timestamp()+interval '1 hour',clock_timestamp())""",
        (incident, owner, epoch, group))


def begin(db, *, incident=1739, epoch=1, owner=OWNER, group="GLOBAL", mutation=None, **overrides):
    values = dict(p_mutation_id=mutation or str(uuid4()), p_incident_id=incident,
        p_owner_id=owner, p_epoch=epoch, p_lease_group=group, p_operation="CREATE_BRANCH",
        p_branch=f"wow-engineering/fenced-{incident}-{epoch}-12345678",
        p_sha="a"*40, p_digest="b"*64, p_source_pr=None, p_source_sha="", p_draft=True)
    values.update(overrides)
    result = db.execute("SELECT public.wow_engineering_begin_writer_mutation(" +
        ",".join(f"{key} => %s" for key in values) + ")", list(values.values())).fetchone()[0]
    return result["status"]


def close(db, incident=1739, owner=OWNER, epoch=1, group="GLOBAL"):
    return db.execute("SELECT public.wow_engineering_close_writer_epoch(%s,%s,%s,%s)",
                      (incident, owner, epoch, group)).fetchone()[0]["status"]


def confirm(db, mutation):
    return db.execute("SELECT public.wow_engineering_confirm_writer_mutation(%s,1739,%s,1,%s)",
                      (mutation, OWNER, "b"*64)).fetchone()[0]


def release(db):
    # Exercise the exact UPDATE used by the live legacy release RPC.
    db.execute("UPDATE public.wow_engineering_incident_writer_leases SET state='RELEASED',expires_at=clock_timestamp() WHERE incident_id=1739")


def test_close_blocks_new_ids_but_allows_pending_readback_confirmation():
    with connection() as db:
        claim(db)
        mutation = str(uuid4())
        assert begin(db, mutation=mutation) == "STARTED"
        assert close(db) == "DRAINING"
        assert begin(db) == "DENIED"
        assert begin(db, mutation=mutation) == "PENDING"
        with pytest.raises(psycopg.Error, match="WRITER_MUTATION_IN_FLIGHT"):
            release(db)
        assert confirm(db, mutation) is True
        assert close(db) == "DRAINED"
        release(db)
        assert begin(db, mutation=mutation) == "CONFIRMED"
        assert begin(db) == "DENIED"


def test_legacy_release_requires_close_and_epoch_cannot_be_reopened():
    with connection() as db:
        claim(db)
        with pytest.raises(psycopg.Error, match="WRITER_EPOCH_NOT_CLOSED"):
            release(db)
        assert close(db) == "DRAINED"
        release(db)
        with pytest.raises(psycopg.Error, match="WRITER_EPOCH_NOT_MONOTONIC|WRITER_EPOCH_CLOSED"):
            db.execute("UPDATE public.wow_engineering_incident_writer_leases SET state='HELD' WHERE incident_id=1739")
        db.execute("UPDATE public.wow_engineering_incident_writer_leases SET state='HELD', epoch=2, owner_id='github-actions:new-worker',expires_at=clock_timestamp()+interval '1 hour' WHERE incident_id=1739")
        assert begin(db, epoch=2, owner="github-actions:new-worker") == "STARTED"
        assert begin(db) == "DENIED"


def test_delete_cannot_erase_epoch_history():
    with connection() as db:
        claim(db)
        with pytest.raises(psycopg.Error, match="WRITER_LEASE_DELETE_FORBIDDEN"):
            db.execute("DELETE FROM public.wow_engineering_incident_writer_leases WHERE incident_id=1739")


@pytest.mark.parametrize("column", ["p_operation", "p_branch", "p_sha", "p_digest", "p_source_sha", "p_draft", "p_epoch", "p_owner_id", "p_lease_group"])
def test_null_input_denies_without_inserting_intent(column):
    with connection() as db:
        claim(db)
        assert begin(db, **{column: None}) == "DENIED"
        assert db.execute("SELECT count(*) FROM public.wow_engineering_writer_mutation_intents").fetchone()[0] == 0


def test_closed_epoch_stays_closed_after_renewal():
    with connection() as db:
        claim(db)
        assert close(db) == "DRAINED"
        db.execute("UPDATE public.wow_engineering_incident_writer_leases SET expires_at=clock_timestamp()+interval '2 hours' WHERE incident_id=1739")
        assert begin(db) == "DENIED"


def test_expired_pending_intent_blocks_takeover_and_other_global_incident():
    with connection() as db:
        claim(db)
        assert begin(db) == "STARTED"
        assert close(db) == "DRAINING"
        db.execute("UPDATE public.wow_engineering_incident_writer_leases SET expires_at=clock_timestamp()-interval '1 hour' WHERE incident_id=1739")
        with pytest.raises(psycopg.Error, match="WRITER_MUTATION_IN_FLIGHT"):
            db.execute("UPDATE public.wow_engineering_incident_writer_leases SET epoch=2,owner_id='github-actions:new-worker' WHERE incident_id=1739")
        with pytest.raises(psycopg.Error, match="WRITER_LEASE_GROUP_CONFLICT"):
            claim(db, incident=1740)


def test_two_connections_close_and_begin_have_one_serial_order():
    with connection() as db:
        claim(db)
    barrier = Barrier(2)
    def race(operation):
        with connection() as db:
            barrier.wait(timeout=10)
            return operation(db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        closing = pool.submit(race, close)
        writing = pool.submit(race, begin)
        result = closing.result(timeout=15), writing.result(timeout=15)
    assert result in {("DRAINED", "DENIED"), ("DRAINING", "STARTED")}
    with connection() as db:
        assert begin(db) == "DENIED"


def test_wrong_owner_cannot_close_and_closed_identity_is_immutable():
    with connection() as db:
        claim(db)
        assert close(db, owner="github-actions:other-worker") == "DENIED"
        assert begin(db) == "STARTED"
        assert close(db) == "DRAINING"
        with pytest.raises(psycopg.Error, match="WRITER_EPOCH_CLOSURE_IMMUTABLE"):
            db.execute("DELETE FROM public.wow_engineering_closed_writer_epochs")
        with pytest.raises(psycopg.Error, match="WRITER_EPOCH_CLOSURE_IMMUTABLE"):
            db.execute("UPDATE public.wow_engineering_closed_writer_epochs SET owner_id='forged'")


def test_service_role_has_no_erase_or_trigger_privileges():
    with connection() as db:
        for table in ("wow_engineering_incident_writer_leases",
                      "wow_engineering_writer_mutation_intents",
                      "wow_engineering_closed_writer_epochs"):
            for privilege in ("DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
                assert db.execute("SELECT has_table_privilege('service_role',%s,%s)",
                                  ("public."+table, privilege)).fetchone()[0] is False
        assert db.execute("SELECT has_table_privilege('service_role','public.wow_engineering_closed_writer_epochs','UPDATE')").fetchone()[0] is False


@pytest.mark.parametrize("role", ["anon", "authenticated"])
def test_untrusted_roles_cannot_close_epoch(role):
    with connection() as db:
        claim(db)
        db.execute("SET ROLE " + role)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            close(db)


def test_trusted_gateway_role_can_close_without_delete_privilege():
    with connection() as db:
        claim(db)
        db.execute("SET ROLE service_role")
        assert close(db) == "DRAINED"
