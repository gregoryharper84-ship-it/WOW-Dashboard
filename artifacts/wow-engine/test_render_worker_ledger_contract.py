"""Contract tests for staged atomic Render deployment ledger."""
from pathlib import Path
import re

LEDGER = Path(__file__).parent / "v17/sql/render_worker_deploy_intent_staging.sql"

def test_ledger_is_private_and_row_locked():
    sql = LEDGER.read_text()
    assert "create schema if not exists wow_release_control" in sql
    assert "enable row level security" in sql
    assert "security definer" in sql
    assert "for update" in sql
    assert "on conflict do nothing" in sql
    assert "revoke all on schema wow_release_control from public, anon, authenticated" in sql

def test_lease_fencing_rejects_stale_writer_and_unsafe_retries():
    sql = LEDGER.read_text()
    assert "lease_token=d.lease_token+1" in sql
    assert "WORKER_DEPLOY_FENCING_REJECTED" in sql
    assert "v.lease_token is distinct from p_lease_token" in sql
    assert "v.owner_run_id is distinct from p_owner_run_id" in sql
    assert "v.state = 'PENDING'" in sql
    assert "INITIATING/ACK_AMBIGUOUS to revert to PENDING" in sql
    assert "p_state='LIVE_VERIFIED'" in sql
    assert "WORKER_DEPLOY_LIVE_RECEIPT_MISSING" in sql

def test_only_allowlisted_worker_service_is_claimable():
    assert "p_service_id <> 'srv-dapkbc0473hc73c1978g'" in LEDGER.read_text()



def test_single_complete_record_rpc_and_immutable_deploy_identity():
    """Do not let a conflict-resolution fragment corrupt the SQL migration."""
    sql = LEDGER.read_text()
    signature = "create or replace function wow_release_control.record_render_worker_deploy("
    grant = "revoke all on function wow_release_control.record_render_worker_deploy("
    assert sql.count(signature) == 1
    assert sql.count(grant) == 1
    assert "WORKER_DEPLOY_ACTIVE_RECEIPT_MISSING" in sql
    assert "WORKER_DEPLOY_LIVE_RECEIPT_MISSING" in sql
    assert "WORKER_DEPLOY_IDENTITY_CHANGED" in sql
    assert "v.render_deploy_id <> p_render_deploy_id" in sql
    assert "p_render_deploy_id !~ '^dep-[a-z0-9]+$'" in sql


def test_staging_ledger_migration_executes_against_disposable_ci_postgres():
    """Real Postgres DDL validation, always executed by the backend CI shard.

    The entire schema/function creation rolls back; no real deployment request
    or production Supabase migration is performed.
    """
    import os

    import pytest

    if os.environ.get("GITHUB_WORKFLOW") != "wow-engine-verify":
        pytest.skip("Dedicated backend CI workflow provides ephemeral Postgres")
    import psycopg

    dsn = "postgresql://wow_agent_ci:wow_agent_ci@localhost:5432/wow_agent_ci"
    with psycopg.connect(dsn, connect_timeout=5, autocommit=False) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DO $roles$ BEGIN "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') "
                    "THEN CREATE ROLE anon NOLOGIN; END IF; "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') "
                    "THEN CREATE ROLE authenticated NOLOGIN; END IF; "
                    "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wow_release_controller') "
                    "THEN CREATE ROLE wow_release_controller LOGIN NOSUPERUSER NOBYPASSRLS; END IF; "
                    "END $roles$;"
                )
                cursor.execute(LEDGER.read_text(), prepare=False)
                # A provisioned login alone cannot mint any deployment
                # intent without the independently approved DBA identity row.
                cursor.execute("set local role wow_release_controller")
                cursor.execute("SAVEPOINT no_dba_authorization")
                try:
                    cursor.execute(
                        "select * from wow_release_control.claim_render_worker_deploy("
                        "%s, %s, %s, %s)",
                        ("srv-dapkbc0473hc73c1978g", "a" * 40, "ci-before-dba", 300),
                    )
                except psycopg.errors.RaiseException as exc:
                    assert "WORKER_RELEASE_DB_AUTHORIZATION_MISSING" in str(exc)
                    cursor.execute("ROLLBACK TO SAVEPOINT no_dba_authorization")
                else:
                    raise AssertionError("Unapproved DBA control identity minted a lease")
                cursor.execute("RESET ROLE")
                cursor.execute(
                    "select count(*) from wow_release_control.render_worker_deploy_intents"
                )
                assert cursor.fetchone() == (0,)
                # DBA is the only actor that can approve the marker.
                cursor.execute(
                    "insert into wow_release_control.release_control_identity "
                    "(singleton, project_ref, environment, service_id, authorized, approved_by, approved_at) "
                    "values (true, %s, 'production', %s, true, 'CI-DBA', now())",
                    ("iczfhsmjrrafhvcpmqhr", "srv-dapkbc0473hc73c1978g"),
                )
                # Verify the actual non-superuser is subject to RLS while
                # invoking the same SECURITY INVOKER lease function.
                cursor.execute("set local role wow_release_controller")
                cursor.execute(
                    "select project_ref from wow_release_control.release_control_identity where singleton = true"
                )
                assert cursor.fetchone() == ("iczfhsmjrrafhvcpmqhr",)
                cursor.execute(
                    "select state, lease_token, can_initiate "
                    "from wow_release_control.claim_render_worker_deploy("
                    "%s, %s, %s, %s)",
                    ("srv-dapkbc0473hc73c1978g", "a" * 40, "ci-run-1", 300),
                )
                claim_rows = cursor.fetchall()
                assert len(claim_rows) == 1, "Claim must emit exactly one decision row"
                state, token, may_initiate = claim_rows[0]
                assert state == "INITIATING"
                assert token == 1
                assert may_initiate is True
                # RLS visibility alone is NOT a state-machine security boundary:
                # the release role must lack direct table UPDATE permission.
                cursor.execute("SAVEPOINT unauthorized_update")
                try:
                    cursor.execute(
                        "update wow_release_control.render_worker_deploy_intents "
                        "set state='PENDING', attempts=0 "
                        "where service_id=%s and target_sha=%s",
                        ("srv-dapkbc0473hc73c1978g", "a" * 40),
                    )
                except psycopg.errors.InsufficientPrivilege:
                    cursor.execute("ROLLBACK TO SAVEPOINT unauthorized_update")
                else:
                    raise AssertionError("Release login bypassed fenced worker RPC with UPDATE")
                # A newer SHA may not pass while the older SHA is INITIATING.
                cursor.execute("SAVEPOINT different_sha")
                try:
                    cursor.execute(
                        "select * from wow_release_control.claim_render_worker_deploy("
                        "%s, %s, %s, %s)",
                        ("srv-dapkbc0473hc73c1978g", "b" * 40, "ci-run-2", 300),
                    )
                except psycopg.errors.RaiseException as exc:
                    assert "WORKER_PREVIOUS_SHA_IN_FLIGHT_RECONCILE_ONLY" in str(exc)
                    cursor.execute("ROLLBACK TO SAVEPOINT different_sha")
                else:
                    raise AssertionError("Different SHA claim bypassed in-flight service HOLD")
        finally:
            # Staging-only proof; no schema/lease rows persist even on success.
            connection.rollback()



def test_release_identity_is_dba_provisioned_and_never_auto_approved():
    sql = LEDGER.read_text()
    assert "create table if not exists wow_release_control.release_control_identity" in sql
    assert "authorized boolean not null default false" in sql
    assert "WOW_RELEASE_CONTROLLER_ROLE_NOT_AUTHORIZED" in sql
    assert sql.count("WORKER_RELEASE_DB_AUTHORIZATION_MISSING") == 3
    assert "rolname='wow_release_controller'" in sql
    assert "not rolsuper and not rolbypassrls and rolcanlogin" in sql
    assert "create role wow_release_controller" not in sql.lower()
    assert "grant usage on schema wow_release_control to wow_release_controller" in sql
    assert "grant select on wow_release_control.release_control_identity to wow_release_controller" in sql
    assert "revoke insert, update, delete, truncate, references, trigger" in sql
    assert "grant select on wow_release_control.render_worker_deploy_intents" in sql
    assert "grant select, insert, update on wow_release_control.render_worker_deploy_intents" not in sql
    assert "grant delete" not in sql.lower()
    assert "to service_role" not in sql.lower()


def test_release_identity_role_cannot_seed_or_modify_dba_attestation():
    sql = LEDGER.read_text().lower()
    assert "revoke all on wow_release_control.release_control_identity from public, anon, authenticated" in sql
    assert "alter table wow_release_control.release_control_identity enable row level security" in sql
    assert "grant insert on wow_release_control.release_control_identity" not in sql
    assert "grant update on wow_release_control.release_control_identity" not in sql
    assert "security definer" in sql



def test_release_controller_cannot_bypass_fenced_worker_rpc_with_table_dml():
    sql = LEDGER.read_text()
    assert "security definer" in sql.lower()
    assert "WORKER_PREVIOUS_SHA_IN_FLIGHT_RECONCILE_ONLY" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "wow_release_intents_select" in sql
    assert "wow_release_intents_insert" not in sql
    assert "wow_release_intents_update" not in sql
    assert "revoke insert, update, delete, truncate, references, trigger" in sql
    assert "grant select on wow_release_control.render_worker_deploy_intents" in sql
    assert "WORKER_DEPLOY_RECORD_TARGET_INVALID" in sql
    assert "WORKER_DEPLOY_RENEW_TARGET_INVALID" in sql


def test_claim_rpc_disambiguates_return_table_state_from_ledger_columns():
    """Regression for psycopg.errors.AmbiguousColumn under real PostgreSQL.

    The claim function RETURNS TABLE(state,...), so unqualified state inside
    PL/pgSQL SQL statements resolves ambiguously and fails at runtime.
    The disposable-Postgres integration test also exercises the same RPC.
    """
    sql = LEDGER.read_text()
    start = sql.index(
        "create or replace function wow_release_control.claim_render_worker_deploy("
    )
    end = sql.index(
        "revoke all on function wow_release_control.claim_render_worker_deploy(",
        start,
    )
    claim_rpc = sql[start:end]
    assert "from wow_release_control.render_worker_deploy_intents intent" in claim_rpc
    assert "intent.target_sha into v_in_flight_sha" in claim_rpc
    assert "intent.state in ('INITIATING','ACK_AMBIGUOUS','DEPLOYING','HOLD')" in claim_rpc
    assert "limit 1 for update of intent" in claim_rpc
