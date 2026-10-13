"""DB-backed contract for sanitized Action 422 receipts.

Only runs against the ephemeral localhost CI Postgres. Never targets a remote DB.
The entire DDL/data exercise rolls back, including synthetic roles.
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest


def test_safe_422_validation_migration_on_ephemeral_postgres():
    # The guarded wow-engine-verify workflow provisions this public CI-only
    # localhost Postgres service, but does not define WOW_CI/DATABASE_URL.
    if (
        os.getenv("GITHUB_ACTIONS") != "true"
        or os.getenv("GITHUB_WORKFLOW") != "wow-engine-verify"
        or os.getenv("GITHUB_REPOSITORY") != "gregoryharper84-ship-it/WOW-Dashboard"
    ):
        pytest.skip("Run only in the designated localhost Postgres CI workflow")
    dsn = os.getenv("DATABASE_URL") or (
        "postgresql://wow_agent_ci:wow_agent_ci@localhost:5432/wow_agent_ci"
    )
    assert urlparse(dsn).hostname in {"localhost", "127.0.0.1"}, (
        "Refuse DB migration contract on any nonlocal host"
    )
    import psycopg
    from psycopg.types.json import Jsonb

    sql = (
        Path(__file__).parent
        / "migrations/20261012_action_invocation_safe_validation_code.sql"
    ).read_text()
    assert sql.count("create or replace function") == 1
    assert sql.count("on conflict (invocation_id) do nothing") == 1
    assert "^[A-Z][A-Z0-9_]{1,79}$" in sql

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("begin")
        try:
            for role in ("anon", "authenticated", "service_role"):
                exists = conn.execute(
                    "select exists(select 1 from pg_roles where rolname = %s)",
                    (role,),
                ).fetchone()[0]
                if not exists:
                    conn.execute(f"create role {role}")
            conn.execute("""
                create table if not exists public.wow_action_invocation_receipts (
                    invocation_id uuid primary key,
                    occurred_at timestamptz not null default now(),
                    route text not null,
                    action_operation_id text not null,
                    http_method text not null,
                    http_status integer not null,
                    auth_scheme text not null,
                    caller_class text not null,
                    caller_user_agent text,
                    request_id text,
                    rows_in integer,
                    duration_ms numeric,
                    can_execute boolean not null default false check(can_execute = false)
                )
            """)
            conn.execute("""
                create table if not exists public.wow_action_invocation_receipt_recovery (
                    invocation_id uuid primary key
                )
            """)
            conn.execute("alter table public.wow_action_invocation_receipts enable row level security")

            # Migration is additive and replay-idempotent.
            conn.execute(sql)
            conn.execute(sql)
            assert conn.execute("""
                select c.relrowsecurity
                from pg_class c where c.oid = 'public.wow_action_invocation_receipts'::regclass
            """).fetchone()[0] is True
            assert conn.execute("""
                select has_function_privilege(
                    'service_role',
                    'public.wow_record_action_invocation_receipt(jsonb)',
                    'EXECUTE'
                )
            """).fetchone()[0] is True
            assert conn.execute("""
                select has_function_privilege(
                    'anon',
                    'public.wow_record_action_invocation_receipt(jsonb)',
                    'EXECUTE'
                )
            """).fetchone()[0] is False

            def receipt(code):
                return dict(
                    invocation_id=str(uuid4()),
                    route="/score-prop",
                    action_operation_id="scoreWowProp",
                    http_method="POST",
                    http_status=422,
                    auth_scheme="BEARER",
                    caller_class="CHATGPT_ACTION",
                    request_id="synthetic-no-secret",
                    duration_ms=1.0,
                    validation_code=code,
                    can_execute=True,  # must be forced false by the RPC
                )

            valid = receipt("PROP_EVIDENCE_SNAPSHOT_NOT_FOUND")
            conn.execute("""
                insert into public.wow_action_invocation_receipt_recovery(invocation_id)
                values (%s::uuid)
            """, (valid["invocation_id"],))
            conn.execute(
                "select public.wow_record_action_invocation_receipt(%s)",
                (Jsonb(valid),),
            )
            first = conn.execute("""
                select validation_code, can_execute, request_id
                from public.wow_action_invocation_receipts
                where invocation_id = %s::uuid
            """, (valid["invocation_id"],)).fetchone()
            assert first == (
                "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND", False, "synthetic-no-secret"
            )
            assert conn.execute("""
                select count(*) from public.wow_action_invocation_receipt_recovery
                where invocation_id = %s::uuid
            """, (valid["invocation_id"],)).fetchone()[0] == 0

            duplicate = dict(valid, validation_code="REQUEST_SCHEMA_VALIDATION_FAILED")
            conn.execute(
                "select public.wow_record_action_invocation_receipt(%s)",
                (Jsonb(duplicate),),
            )
            assert conn.execute("""
                select validation_code from public.wow_action_invocation_receipts
                where invocation_id = %s::uuid
            """, (valid["invocation_id"],)).fetchone()[0] == "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"

            for unsafe in ("lower_case", "A" * 81, "PRIVATE\\nDATA"):
                row = receipt(unsafe)
                conn.execute(
                    "select public.wow_record_action_invocation_receipt(%s)",
                    (Jsonb(row),),
                )
                assert conn.execute("""
                    select validation_code from public.wow_action_invocation_receipts
                    where invocation_id = %s::uuid
                """, (row["invocation_id"],)).fetchone()[0] is None

            conn.execute("savepoint invalid_code")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("""
                    insert into public.wow_action_invocation_receipts(
                        invocation_id, route, action_operation_id, http_method,
                        http_status, auth_scheme, caller_class, validation_code
                    ) values (
                        %s::uuid, '/score-prop', 'scoreWowProp', 'POST',
                        422, 'BEARER', 'CHATGPT_ACTION', 'unsafe code'
                    )
                """, (str(uuid4()),))
            conn.execute("rollback to savepoint invalid_code")
            conn.execute("release savepoint invalid_code")
        finally:
            conn.execute("rollback")
