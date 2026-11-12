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


def test_service_role_cannot_bypass_writer_rpc_with_direct_dml():
    # BYPASSRLS does not authorize bypassing protected writer RPCs.
    with connection() as db:
        claim(db)
        for table, privilege in (
            ("wow_engineering_writer_mutation_intents", "INSERT"),
            ("wow_engineering_writer_mutation_intents", "UPDATE"),
            ("wow_engineering_closed_writer_epochs", "INSERT"),
        ):
            assert db.execute(
                "SELECT has_table_privilege('service_role',%s,%s)",
                ("public." + table, privilege),
            ).fetchone()[0] is False
        db.execute("SET ROLE service_role")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("""INSERT INTO public.wow_engineering_closed_writer_epochs
                (incident_id,epoch,owner_id,lease_group)
                VALUES (1739,1,%s,'GLOBAL')""", (OWNER,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("""UPDATE public.wow_engineering_writer_mutation_intents
                SET state='CONFIRMED' WHERE incident_id=1739""")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("""INSERT INTO public.wow_engineering_writer_mutation_intents
                (mutation_id,incident_id,owner_id,epoch,lease_group,operation,branch,sha,
                 payload_digest,state)
                VALUES (%s,1739,%s,1,'GLOBAL','CREATE_BRANCH',%s,%s,%s,'PENDING')""",
                (str(uuid4()), OWNER, 'wow-engineering/fenced-1739-1-12345678', 'a'*40, 'b'*64))
        mutation = str(uuid4())
        assert begin(db, mutation=mutation) == 'STARTED'
        assert confirm(db, mutation) is True
        assert close(db) == 'DRAINED'


def test_writer_rpc_definers_are_attested_and_search_path_pinned():
    with connection() as db:
        rows = db.execute("""SELECT p.proname, p.prosecdef,
                pg_catalog.pg_get_userbyid(p.proowner), p.proconfig
            FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname IN
              ('wow_engineering_begin_writer_mutation','wow_engineering_close_writer_epoch',
               'wow_engineering_confirm_writer_mutation')""").fetchall()
        assert len(rows) == 3
        for name, is_definer, owner, settings in rows:
            assert is_definer is True, name
            assert owner == 'postgres', name
            assert 'search_path=pg_catalog' in settings, name


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


def test_legacy_claim_upsert_checks_actual_incremented_epoch():
    with connection() as db:
        claim(db)
        assert close(db) == "DRAINED"
        release(db)
        # The real legacy RPC proposes epoch=1 in BEFORE INSERT, then increments
        # the conflicting existing row. The UPDATE trigger must assess epoch=2.
        db.execute("""INSERT INTO public.wow_engineering_incident_writer_leases AS l
          (incident_id,owner_id,epoch,lease_group,state,acquired_at,expires_at,updated_at)
          VALUES (1739,'github-actions:new-worker',1,'GLOBAL','HELD',now(),now()+interval '1 hour',now())
          ON CONFLICT (incident_id) DO UPDATE SET
          owner_id=excluded.owner_id,epoch=l.epoch+1,state='HELD',
          expires_at=excluded.expires_at WHERE l.state='RELEASED' OR l.expires_at<=clock_timestamp()""")
        assert begin(db, epoch=2, owner="github-actions:new-worker") == "STARTED"
