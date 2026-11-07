import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
FORWARD = ROOT / "migrations" / "20261107_ncaaf_venues_snapshot_and_schedule_geometry.sql"
ROLLBACK = ROOT / "migrations" / "20261107_ncaaf_venues_snapshot_and_schedule_geometry_rollback.sql"
PROOF_DB = f"ncaaf_geometry_migration_proof_{uuid.uuid4().hex[:12]}"
PROVIDER = "CFBD_SCHEDULE_GEOMETRY_V1"


def _executable_sql(sql: str) -> str:
    return "\n".join(
        line for line in sql.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )


def test_forward_migration_is_one_transaction_and_insert_only():
    sql = FORWARD.read_text()
    executable = _executable_sql(sql)
    assert executable.startswith("\\set ON_ERROR_STOP on\nbegin;")
    assert "\ncommit;" in executable
    assert executable.index("begin;") < executable.index("drop constraint if exists wow_ncaaf_source_endpoint;")
    assert executable.index("drop constraint if exists wow_ncaaf_source_endpoint;") < executable.index("\ncommit;")
    assert "'/venues'" in sql
    assert "trg_wow_ncaaf_source_snapshot_immutable" in sql
    assert "trg_wow_ncaaf_source_snapshot_no_truncate" in sql
    assert "before truncate" in sql
    assert "for each statement" in sql
    assert "NCAAF_SOURCE_SNAPSHOT_IMMUTABLE" in sql
    assert PROVIDER in sql
    assert "array['REST_TRAVEL']" in sql
    assert "can_execute = false" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.wow_ncaaf_source_snapshots from public, anon, authenticated, service_role" in sql
    assert "grant select, insert on table public.wow_ncaaf_source_snapshots to service_role" in sql
    assert "grant all" not in sql.lower()
    assert "insert into public.wow_ncaaf_source_snapshots" not in sql.lower()
    assert "create unique index" not in sql.lower()
    assert "iczfhsmjrrafhvcpmqhr" in sql


def test_rollback_guards_manifest_citations_and_keeps_narrow_grants():
    sql = ROLLBACK.read_text()
    executable = _executable_sql(sql)
    assert executable.startswith("\\set ON_ERROR_STOP on\nbegin;")
    assert executable.rstrip().endswith("commit;")
    assert "NCAAF_VENUES_ROLLBACK_BLOCKED_BY_SNAPSHOTS" in sql
    assert "NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_EVIDENCE" in sql
    assert "NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_TRAINING_MANIFEST" in sql
    assert "NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_EVENT_MANIFEST" in sql
    assert "feature_source_manifest -> 'evidence'" in sql
    assert "source_manifest #> '{feature_source_manifest,evidence}'" in sql
    assert "evidence ->> 'source_provider'" in sql
    assert "'/venues'" not in sql.split("add constraint wow_ncaaf_source_endpoint", 1)[1]
    assert "drop trigger if exists trg_wow_ncaaf_source_snapshot_no_truncate" in sql
    assert "grant select, insert on table public.wow_ncaaf_source_snapshots to service_role" in sql
    assert "grant all" not in sql.lower()
    assert PROVIDER in sql


def _psql_available() -> bool:
    try:
        probe = subprocess.run(
            ["sudo", "-n", "-u", "postgres", "psql", "-d", "postgres", "-c", "select 1"],
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return probe.returncode == 0


def _psql(database: str, sql: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sudo", "-n", "-u", "postgres", "psql", "-d", database, "-v", "ON_ERROR_STOP=1", "-f", "-"],
        input=sql,
        capture_output=True,
        text=True,
    )


def _scalar(database: str, sql: str) -> str:
    result = subprocess.run(
        ["sudo", "-n", "-u", "postgres", "psql", "-d", database, "-v", "ON_ERROR_STOP=1", "-tA", "-c", sql],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture(scope="module")
def proof_database():
    """Isolated proof database; never drop or replace existing global roles."""
    if not _psql_available():
        pytest.skip("local postgres superuser is not available for the isolated migration proof")
    roles = ("anon", "authenticated", "service_role")
    existing = set(_scalar(
        "postgres",
        "select rolname from pg_roles where rolname in ('anon','authenticated','service_role');",
    ).splitlines())
    created_roles = []
    database_created = False
    try:
        for role in roles:
            if role not in existing:
                role_sql = (
                    "create role service_role nologin bypassrls;"
                    if role == "service_role" else f"create role {role} nologin;"
                )
                creation = _psql("postgres", role_sql)
                assert creation.returncode == 0, creation.stderr
                created_roles.append(role)
        created = _psql("postgres", f"create database {PROOF_DB};")
        assert created.returncode == 0, created.stderr
        database_created = True
        yield PROOF_DB
    finally:
        if database_created:
            dropped = _psql("postgres", f"drop database {PROOF_DB} with (force);")
            assert dropped.returncode == 0, dropped.stderr
        for role in reversed(created_roles):
            dropped_role = _psql("postgres", f"drop role {role};")
            assert dropped_role.returncode == 0, dropped_role.stderr


def _install_pre_migration_schema(database: str) -> None:
    installed = _psql(
        database,
        """
        drop table if exists public.wow_ncaaf_event_feature_snapshots;
        drop table if exists public.wow_ncaaf_training_features;
        drop table if exists public.wow_ncaaf_pregame_evidence;
        drop table if exists public.wow_ncaaf_evidence_sources;
        drop table if exists public.wow_ncaaf_source_snapshots;
        drop function if exists public.wow_ncaaf_reject_source_snapshot_mutation();
        create table public.wow_ncaaf_source_snapshots (
            source_snapshot_id uuid primary key default gen_random_uuid(),
            created_at timestamptz not null default now(),
            provider text not null,
            endpoint text not null,
            season integer not null,
            week integer,
            requested_at timestamptz not null,
            retrieved_at timestamptz not null,
            request_params jsonb not null,
            response_rows jsonb not null,
            response_row_count integer not null,
            payload_sha256 text not null,
            acquisition_status text not null,
            blocker_codes jsonb not null default '[]'::jsonb,
            can_execute boolean not null default false,
            constraint wow_ncaaf_source_endpoint check (endpoint in (
                '/games','/games/players','/ratings/core','/ratings/sp',
                '/ratings/srs','/ratings/elo','/ratings/fpi'
            )),
            constraint wow_ncaaf_source_never_execute check (can_execute = false)
        );
        grant all on table public.wow_ncaaf_source_snapshots to service_role;
        create table public.wow_ncaaf_evidence_sources (
            provider_key text primary key,
            provider_class text not null,
            max_provenance_grade text not null,
            allowed_evidence_kinds text[] not null,
            active boolean not null,
            approval_reference text not null,
            notes text,
            can_execute boolean not null
        );
        create table public.wow_ncaaf_pregame_evidence (
            evidence_id uuid primary key default gen_random_uuid(),
            source_provider text not null
        );
        create table public.wow_ncaaf_training_features (
            feature_row_id uuid primary key default gen_random_uuid(),
            feature_source_manifest jsonb not null
        );
        create table public.wow_ncaaf_event_feature_snapshots (
            feature_snapshot_id uuid primary key default gen_random_uuid(),
            source_manifest jsonb not null
        );
        """,
    )
    assert installed.returncode == 0, installed.stderr


def _constraint(database: str) -> str:
    return _scalar(
        database,
        "select pg_get_constraintdef(oid) from pg_constraint where conname = 'wow_ncaaf_source_endpoint';",
    )


def test_failed_forward_statement_restores_the_endpoint_constraint(proof_database):
    _install_pre_migration_schema(proof_database)
    before = _constraint(proof_database)
    assert "/venues" not in before
    broken = FORWARD.read_text().replace(
        "drop constraint if exists wow_ncaaf_source_endpoint;",
        "drop constraint if exists wow_ncaaf_source_endpoint;\n"
        "do $$ begin raise exception 'INDUCED_MIGRATION_FAILURE'; end $$;\n",
        1,
    )
    failed = _psql(proof_database, broken)
    assert failed.returncode != 0
    assert "INDUCED_MIGRATION_FAILURE" in failed.stderr
    assert _constraint(proof_database) == before


def _snapshot_insert(endpoint: str) -> str:
    return f"""
    insert into public.wow_ncaaf_source_snapshots (
        provider, endpoint, season, requested_at, retrieved_at, request_params,
        response_rows, response_row_count, payload_sha256, acquisition_status, can_execute
    ) values (
        'CFBD', '{endpoint}', 2026, now(), now(), '{{}}'::jsonb,
        '[]'::jsonb, 0, repeat('a', 64), 'AVAILABLE', false
    );
    """


def test_forward_and_rollback_honor_grants_and_manifest_dependencies(proof_database):
    _install_pre_migration_schema(proof_database)
    applied = _psql(proof_database, FORWARD.read_text())
    assert applied.returncode == 0, applied.stderr
    assert "/venues" in _constraint(proof_database)
    privileges = _scalar(
        proof_database,
        """
        select string_agg(privilege_type, ',' order by privilege_type)
          from information_schema.role_table_grants
         where table_name = 'wow_ncaaf_source_snapshots'
           and grantee = 'service_role';
        """,
    )
    assert privileges == "INSERT,SELECT"
    triggers = _scalar(
        proof_database,
        """
        select string_agg(tgname, ',' order by tgname)
          from pg_trigger
         where tgname in (
             'trg_wow_ncaaf_source_snapshot_immutable',
             'trg_wow_ncaaf_source_snapshot_no_truncate'
         )
           and not tgisinternal;
        """,
    )
    assert "trg_wow_ncaaf_source_snapshot_immutable" in triggers
    assert "trg_wow_ncaaf_source_snapshot_no_truncate" in triggers

    inserted = _psql(
        proof_database,
        "set role service_role;\n" + _snapshot_insert("/games"),
    )
    assert inserted.returncode == 0, inserted.stderr
    denied_update = _psql(
        proof_database,
        """
        set role service_role;
        update public.wow_ncaaf_source_snapshots set season = 2025;
        """,
    )
    assert denied_update.returncode != 0
    denied_truncate = _psql(
        proof_database,
        "set role service_role;\ntruncate public.wow_ncaaf_source_snapshots;",
    )
    assert denied_truncate.returncode != 0
    assert "permission denied" in denied_truncate.stderr
    owner_truncate = _psql(proof_database, "truncate public.wow_ncaaf_source_snapshots;")
    assert owner_truncate.returncode != 0
    assert "NCAAF_SOURCE_SNAPSHOT_IMMUTABLE" in owner_truncate.stderr
    owner_update = _psql(
        proof_database,
        "update public.wow_ncaaf_source_snapshots set season = 2025;",
    )
    assert owner_update.returncode != 0
    assert "NCAAF_SOURCE_SNAPSHOT_IMMUTABLE" in owner_update.stderr

    cited = _psql(
        proof_database,
        f"""
        insert into public.wow_ncaaf_training_features (feature_source_manifest)
        values (jsonb_build_object(
            'notes', '{PROVIDER} mentioned in text only',
            'evidence', jsonb_build_array(jsonb_build_object('source_provider', '{PROVIDER}'))
        ));
        insert into public.wow_ncaaf_event_feature_snapshots (source_manifest)
        values (jsonb_build_object('notes', '{PROVIDER}'));
        """,
    )
    assert cited.returncode == 0, cited.stderr
    blocked = _psql(proof_database, ROLLBACK.read_text())
    assert blocked.returncode != 0
    assert "NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_TRAINING_MANIFEST" in blocked.stderr
    assert "/venues" in _constraint(proof_database)
    assert _scalar(
        proof_database,
        f"select provider_key from public.wow_ncaaf_evidence_sources where provider_key = '{PROVIDER}';",
    ) == PROVIDER

    cleared = _psql(proof_database, "delete from public.wow_ncaaf_training_features;")
    assert cleared.returncode == 0, cleared.stderr
    nested = _psql(
        proof_database,
        f"""
        update public.wow_ncaaf_event_feature_snapshots
           set source_manifest = jsonb_build_object(
               'feature_source_manifest',
               jsonb_build_object('evidence', jsonb_build_array(
                   jsonb_build_object('source_provider', '{PROVIDER}', 'evidence_kind', 'REST_TRAVEL')
               ))
           );
        """,
    )
    assert nested.returncode == 0, nested.stderr
    blocked_snapshot = _psql(proof_database, ROLLBACK.read_text())
    assert blocked_snapshot.returncode != 0
    assert "NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_EVENT_MANIFEST" in blocked_snapshot.stderr
    assert "/venues" in _constraint(proof_database)

    text_only = _psql(
        proof_database,
        f"""
        update public.wow_ncaaf_event_feature_snapshots
           set source_manifest = jsonb_build_object(
               'notes', '{PROVIDER}',
               'feature_source_manifest',
               jsonb_build_object('evidence', jsonb_build_array(
                   jsonb_build_object('source_provider', 'OTHER_PROVIDER')
               ))
           );
        """,
    )
    assert text_only.returncode == 0, text_only.stderr
    rolled = _psql(proof_database, ROLLBACK.read_text())
    assert rolled.returncode == 0, rolled.stderr
    after = _constraint(proof_database)
    assert "/venues" not in after
    assert "/games" in after
    assert _scalar(
        proof_database,
        f"select count(*) from public.wow_ncaaf_evidence_sources where provider_key = '{PROVIDER}';",
    ) == "0"
    remaining = _scalar(
        proof_database,
        """
        select coalesce(string_agg(privilege_type, ',' order by privilege_type), '')
          from information_schema.role_table_grants
         where table_name = 'wow_ncaaf_source_snapshots'
           and grantee = 'service_role';
        """,
    )
    assert remaining == "INSERT,SELECT"
    assert "TRUNCATE" not in remaining
    assert "UPDATE" not in remaining
    assert "DELETE" not in remaining
