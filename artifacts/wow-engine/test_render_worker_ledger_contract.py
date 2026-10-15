"""Contract tests for staged atomic Render deployment ledger."""
from pathlib import Path
import re

LEDGER = Path(__file__).parent / "v17/sql/render_worker_deploy_intent_staging.sql"

def test_ledger_is_private_and_row_locked():
    sql = LEDGER.read_text()
    assert "create schema if not exists wow_release_control" in sql
    assert "enable row level security" in sql
    assert "security invoker" in sql
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
        finally:
            # Staging-only proof; no schema/lease rows persist even on success.
            connection.rollback()



def test_release_identity_is_dba_provisioned_and_never_auto_approved():
    sql = LEDGER.read_text()
    assert "create table if not exists wow_release_control.release_control_identity" in sql
    assert "authorized boolean not null default false" in sql
    assert "WOW_RELEASE_CONTROLLER_ROLE_NOT_AUTHORIZED" in sql
    assert "rolname='wow_release_controller'" in sql
    assert "not rolsuper and not rolbypassrls and rolcanlogin" in sql
    assert "create role wow_release_controller" not in sql.lower()
    assert "grant usage on schema wow_release_control to wow_release_controller" in sql
    assert "grant select on wow_release_control.release_control_identity to wow_release_controller" in sql
    assert "grant select, insert, update on wow_release_control.render_worker_deploy_intents" in sql
    assert "grant delete" not in sql.lower()
    assert "to service_role" not in sql.lower()


def test_release_identity_role_cannot_seed_or_modify_dba_attestation():
    sql = LEDGER.read_text().lower()
    assert "revoke all on wow_release_control.release_control_identity from public, anon, authenticated" in sql
    assert "alter table wow_release_control.release_control_identity enable row level security" in sql
    assert "grant insert on wow_release_control.release_control_identity" not in sql
    assert "grant update on wow_release_control.release_control_identity" not in sql
    assert "security invoker" in sql



def test_private_release_ledger_enforces_restricted_worker_row_policies():
    sql = LEDGER.read_text()
    assert "wow_release_identity_select" in sql
    assert "wow_release_intents_select" in sql
    assert "wow_release_intents_insert" in sql
    assert "wow_release_intents_update" in sql
    assert "for select to wow_release_controller" in sql.lower()
    assert "for update to wow_release_controller" in sql.lower()
    assert "for insert to wow_release_controller" in sql.lower()
    assert "with check (service_id" in sql.lower()
    assert "using (service_id" in sql.lower()
