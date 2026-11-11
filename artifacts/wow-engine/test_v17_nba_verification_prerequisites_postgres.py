"""Integration: the prerequisite probe against real PostgreSQL, in both states.

The migration is exercised for real, in three states, against a throwaway
database:

1. base schema only            -> NBA_VERIFICATION_LEDGER_MISSING
2. ledger present, calibrator
   evidence columns absent     -> NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING
3. ledger migration applied    -> ready, and a real receipt archives

State 2 is the genuine partial-rollout shape: the ledger exists but the
calibrator evidence columns do not.

The Supabase/PostgREST transport is replaced by a thin adapter over psycopg that
raises the same SQLSTATE codes PostgREST forwards (`42P01` missing relation,
`42703` missing column). That keeps the probe's classification logic under test
unchanged; this is not an HTTP transport test, and it does not contact Supabase.

Requires a PostgreSQL superuser DSN in `WOW_TEST_POSTGRES_DSN`, for example
`postgresql:///postgres` on a local peer-auth socket. Skips when it is absent.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Mapping

import pytest

from v17 import nba_verification_wiring as wiring

DSN = os.environ.get("WOW_TEST_POSTGRES_DSN", "").strip()
pytestmark = pytest.mark.skipif(
    not DSN, reason="WOW_TEST_POSTGRES_DSN is not configured"
)

psycopg = pytest.importorskip("psycopg")
from psycopg import pq  # noqa: E402  (import after importorskip)
from psycopg.types.json import Jsonb  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
GUARD_MIGRATION = MIGRATIONS / "20260915_harden_immutable_mutation_trigger_search_path.sql"
BASE_MIGRATION = MIGRATIONS / "20260913_basketball_team_event_specialists.sql"
LEDGER_MIGRATION = MIGRATIONS / "20261110_v17_independent_verification_receipts.sql"
PREDEPLOY_MIGRATION = MIGRATIONS / "20261110_nba_team_event_verification_receipts.sql"

_OK_STATUSES = (pq.ExecStatus.COMMAND_OK, pq.ExecStatus.TUPLES_OK)

# psycopg exposes the SQLSTATE diagnostic as SQLSTATE; some builds named it CODE.
_SQLSTATE_FIELD = getattr(pq.DiagnosticField, "SQLSTATE", None) or getattr(
    pq.DiagnosticField, "CODE", None
)


def _sqlstate(result: Any) -> str:
    if _SQLSTATE_FIELD is None:
        return ""
    value = result.error_field(_SQLSTATE_FIELD)
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")
    return value or ""
_MUTATION_REJECTED = "P0001"
_CHECK_VIOLATION = "23514"


class _ProviderError(RuntimeError):
    """Carries the SQLSTATE a PostgREST client would have surfaced."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _apply(conn: Any, sql_text: str) -> None:
    result = conn.pgconn.exec_(sql_text.encode("utf-8"))
    if result.status not in _OK_STATUSES:
        raise AssertionError(
            "statement failed: " + result.error_message.decode("utf-8", "replace")
        )


def _expect_rejected(conn: Any, sql_text: str, *, sqlstate: str | None = None) -> str:
    result = conn.pgconn.exec_(sql_text.encode("utf-8"))
    assert result.status not in _OK_STATUSES, f"statement was accepted: {sql_text}"
    code = _sqlstate(result)
    if sqlstate is not None:
        assert code == sqlstate, result.error_message.decode("utf-8", "replace")
    return code


def _ensure_roles(conn: Any) -> None:
    for role in ("anon", "authenticated", "service_role"):
        present = conn.execute(
            "select 1 from pg_roles where rolname = %s", (role,)
        ).fetchone()
        if not present:
            _apply(conn, f"create role {role} nologin")


@contextmanager
def _database(
    *, apply_ledger: bool, drop_calibrator_evidence: bool = False
) -> Iterator[Any]:
    admin = psycopg.connect(DSN, autocommit=True)
    name = f"wow_probe_{uuid.uuid4().hex[:12]}"
    admin.execute(f'create database "{name}"')
    try:
        with psycopg.connect(DSN, dbname=name, autocommit=True) as conn:
            _ensure_roles(conn)
            _apply(conn, GUARD_MIGRATION.read_text(encoding="utf-8"))
            _apply(conn, BASE_MIGRATION.read_text(encoding="utf-8"))
            if apply_ledger:
                _apply(conn, LEDGER_MIGRATION.read_text(encoding="utf-8"))
            if drop_calibrator_evidence:
                _apply(
                    conn,
                    "alter table public.wow_basketball_team_event_calibrators "
                    "drop column calibration_sha256, "
                    "drop column calibration_fit_end;",
                )
            yield conn
    finally:
        try:
            admin.execute(f'drop database if exists "{name}" with (force)')
        except Exception:  # noqa: BLE001 - pre-13 servers lack WITH (FORCE)
            admin.execute(f'drop database if exists "{name}"')
        admin.close()


class _ProbeAdapter:
    """PostgREST-shaped client backed by real SQL."""

    def __init__(self, conn: Any):
        self.conn = conn

    def table(self, name: str) -> "_ProbeQuery":
        return _ProbeQuery(self.conn, name)


class _ProbeQuery:
    def __init__(self, conn: Any, table: str):
        self.conn = conn
        self.table_name = table
        self.columns = "*"
        self.limit_value: int | None = None
        self.payload: Mapping[str, Any] | None = None

    def select(self, columns: str) -> "_ProbeQuery":
        self.columns = columns
        return self

    def limit(self, value: int) -> "_ProbeQuery":
        self.limit_value = value
        return self

    def insert(self, payload: Mapping[str, Any]) -> "_ProbeQuery":
        self.payload = payload
        return self

    def execute(self) -> Any:
        if self.payload is not None:
            return self._insert()
        sql = f'select {self.columns} from public."{self.table_name}"'
        if self.limit_value is not None:
            sql += f" limit {int(self.limit_value)}"
        result = self.conn.pgconn.exec_(sql.encode("utf-8"))
        if result.status not in _OK_STATUSES:
            raise _ProviderError(
                _sqlstate(result),
                result.error_message.decode("utf-8", "replace"),
            )
        return SimpleNamespace(data=[])

    def _insert(self) -> Any:
        row = dict(self.payload or {})
        columns = list(row)
        values = [
            Jsonb(row[column]) if column == "receipt" else row[column]
            for column in columns
        ]
        quoted = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        sql = (
            f'insert into public."{self.table_name}" ({quoted}) '
            f"values ({placeholders})"
        )
        try:
            with self.conn.cursor() as cursor:
                cursor.execute(sql, values)
        except psycopg.Error as exc:
            raise _ProviderError(str(exc.sqlstate or ""), str(exc)) from exc
        return SimpleNamespace(data=[row])


def _fetch_one(conn: Any, sql: str) -> Any:
    return conn.execute(sql).fetchone()


class _RecordingArchive:
    """Captures the exact archive payload without writing anywhere."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def table(self, name: str) -> "_RecordingArchive":
        assert name == wiring.RECEIPT_TABLE
        return self

    def insert(self, row: Mapping[str, Any]) -> "_RecordingArchive":
        self.rows.append(dict(row))
        return self

    def execute(self) -> Any:
        return SimpleNamespace(data=[{}])


def _receipt() -> dict[str, Any]:
    return {
        "schema_version": "WOW_V17_TEAM_EVENT_INDEPENDENT_VERIFICATION_V1",
        "verification_id": str(uuid.uuid4()),
        "sport": "NBA",
        "league_scope": "NBA",
        "controlling_specialist": "wow.nba-game-win-probability-expert",
        "model_family": "BASKETBALL_TEAM_EVENT_LOGISTIC_V1",
        "model_version": "NBA_TEAM_EVENT_LOGISTIC_V1_20261110T000000Z",
        "model_artifact_id": "artifact-1",
        "model_artifact_sha256": "a" * 64,
        "calibration_artifact_id": "cal-1",
        "calibration_sha256": "b" * 64,
        "feature_schema_version": "BASKETBALL_TEAM_EVENT_FEATURES_V2",
        "input_contract_version": "NBA_GAME_WIN_INPUTS_V1",
        "certification_id_attested": None,
        "independent_verifier": "INDEPENDENT_VERIFICATION",
        "implementation_owner": wiring.IMPLEMENTATION_OWNER,
        "verification_owner": wiring.VERIFICATION_OWNER,
        "independent_verification_status": "PASS",
        "method": {
            "harness": "wow-v17-nba-team-event-verification",
            "code_sha": "c" * 40,
            "environment": "postgres-integration",
        },
        "checks": [
            {"check_id": f"V{index}", "status": "PASS", "observed": {}, "blockers": []}
            for index in range(1, 13)
        ],
        "source_attestation": {},
        "evidence_refs": {},
        "limitations": [],
        "verdict": {
            "publishable_scope": "NONE",
            "certification_eligible": True,
            "blockers": [],
        },
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "created_at_utc": "2026-11-10T00:00:00+00:00",
        "metrics": {"holdout_brier": 0.2},
    }


def _insert_row(conn: Any, row: Mapping[str, Any]) -> None:
    columns = list(row)
    values = [
        Jsonb(row[column]) if column == "receipt" else row[column] for column in columns
    ]
    quoted = ", ".join(f'"{column}"' for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    with conn.cursor() as cursor:
        cursor.execute(
            f'insert into public."{wiring.RECEIPT_TABLE}" ({quoted}) '
            f"values ({placeholders})",
            values,
        )


# --------------------------------------------------------------------------- #
# States
# --------------------------------------------------------------------------- #
def test_pre_migration_state_reports_the_missing_ledger():
    with _database(apply_ledger=False) as conn:
        result = wiring.verification_prerequisites(_ProbeAdapter(conn))

    assert result["ready"] is False
    assert result["code"] == "NBA_VERIFICATION_LEDGER_MISSING"
    assert result["missing"] == [
        wiring.RECEIPT_TABLE,
        f"{wiring.CALIBRATOR_TABLE}.columns",
    ]
    classifications = {probe["table"]: probe for probe in result["probes"]}
    assert classifications[wiring.RECEIPT_TABLE]["classification"] == "TABLE_MISSING"
    assert classifications[wiring.RECEIPT_TABLE]["provider_code"] == "42P01"
    assert (
        classifications[wiring.CALIBRATOR_TABLE]["classification"] == "COLUMN_MISSING"
    )
    assert classifications[wiring.CALIBRATOR_TABLE]["provider_code"] == "42703"
    assert result["can_execute"] is False


def test_partial_state_reports_missing_calibrator_evidence():
    with _database(apply_ledger=True, drop_calibrator_evidence=True) as conn:
        result = wiring.verification_prerequisites(_ProbeAdapter(conn))

    assert result["ready"] is False
    assert result["code"] == "NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING"
    assert result["missing"] == [f"{wiring.CALIBRATOR_TABLE}.columns"]
    assert result["probes"][0]["classification"] == "READY"
    assert result["probes"][1]["provider_code"] == "42703"


def test_post_migration_state_is_ready_and_archives_a_receipt():
    with _database(apply_ledger=True) as conn:
        adapter = _ProbeAdapter(conn)
        prerequisites = wiring.verification_prerequisites(adapter)
        assert prerequisites["ready"] is True
        assert prerequisites["code"] is None
        assert [probe["classification"] for probe in prerequisites["probes"]] == [
            "READY",
            "READY",
        ]

        receipt = _receipt()
        archived = wiring.archive_receipt(adapter, receipt)
        assert archived["duplicate"] is False
        stored = _fetch_one(
            conn,
            "select independent_verification_status, can_execute, publishable_scope, "
            "receipt_digest, certification_eligible "
            f"from public.{wiring.RECEIPT_TABLE}",
        )

    assert stored == ("PASS", False, "NONE", wiring.receipt_digest(receipt), True)


def test_migration_is_idempotent():
    with _database(apply_ledger=True) as conn:
        _apply(conn, LEDGER_MIGRATION.read_text(encoding="utf-8"))
        assert wiring.verification_prerequisites(_ProbeAdapter(conn))["ready"] is True


# --------------------------------------------------------------------------- #
# The archive row must also satisfy the real DDL and its guards
# --------------------------------------------------------------------------- #
def test_ledger_rejects_mutation_truncate_and_execution_columns():
    with _database(apply_ledger=True) as conn:
        adapter = _ProbeAdapter(conn)
        recorder = _RecordingArchive()
        receipt = _receipt()
        wiring.archive_receipt(recorder, receipt)
        row = recorder.rows[0]
        # The row must exist for a per-row trigger to fire; recording it in
        # memory alone would let an UPDATE of zero rows look accepted.
        wiring.archive_receipt(adapter, receipt)
        assert _fetch_one(
            conn, f"select count(*) from public.{wiring.RECEIPT_TABLE}"
        ) == (1,)

        assert _expect_rejected(
            conn, f"update public.{wiring.RECEIPT_TABLE} set can_execute = false"
        ) == _MUTATION_REJECTED
        assert _expect_rejected(
            conn, f"delete from public.{wiring.RECEIPT_TABLE}"
        ) == _MUTATION_REJECTED
        assert _expect_rejected(
            conn, f"truncate public.{wiring.RECEIPT_TABLE}"
        ) == "55000"

        executable = dict(row)
        executable["receipt_id"] = str(uuid.uuid4())
        executable["can_execute"] = True
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_row(conn, executable)

        inconsistent = dict(row)
        inconsistent["receipt_id"] = str(uuid.uuid4())
        inconsistent["failed_checks"] = ["V6"]
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_row(conn, inconsistent)

        same_owner = dict(row)
        same_owner["receipt_id"] = str(uuid.uuid4())
        same_owner["verification_owner"] = same_owner["implementation_owner"]
        with pytest.raises(psycopg.errors.CheckViolation):
            _insert_row(conn, same_owner)

        assert _fetch_one(
            conn, f"select count(*) from public.{wiring.RECEIPT_TABLE}"
        ) == (1,)


def test_archive_payload_matches_the_migration_columns():
    """Every archived column must exist in the ledger with the same name."""
    with _database(apply_ledger=True) as conn:
        recorder = _RecordingArchive()
        wiring.archive_receipt(recorder, _receipt())
        declared = {
            row[0]
            for row in conn.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'public' and table_name = %s",
                (wiring.RECEIPT_TABLE,),
            ).fetchall()
        }
        adapter = _ProbeAdapter(conn)
        wiring.archive_receipt(adapter, _receipt())

    assert set(recorder.rows[0]) <= declared
    assert {"recorded_at", "receipt", "can_execute"} <= declared


# --------------------------------------------------------------------------- #
# Render predeploy RPC contract: read-only attestation must fail on drift
# --------------------------------------------------------------------------- #
def _predeploy_compatible(conn: Any) -> bool:
    """Read the actual compatible Boolean from the SQL readiness RPC."""
    row = conn.execute(
        "select (public.wow_nba_team_event_verification_receipt_schema_ready()"
        "->>'compatible')::boolean"
    ).fetchone()
    assert row is not None
    return row[0]


def test_predeploy_schema_rpc_attests_nonexecution_checks():
    """Constraint names without the restrictive expressions are not proof."""
    with _database(apply_ledger=True) as conn:
        _apply(conn, PREDEPLOY_MIGRATION.read_text(encoding="utf-8"))
        assert _predeploy_compatible(conn) is True
        for name, expression in (
            ("wow_independent_verification_receipts_can_execute_check",
             "can_execute = false"),
            ("wow_independent_verification_receipts_publishable_scope_check",
             "publishable_scope = 'NONE'"),
        ):
            table = "public.wow_independent_verification_receipts"
            _apply(conn, f"alter table {table} drop constraint {name}")
            assert _predeploy_compatible(conn) is False
            # Reusing the expected name with an unconditional check must also
            # fail; existence of a named constraint is not sufficient.
            _apply(conn, f"alter table {table} add constraint {name} check (true)")
            assert _predeploy_compatible(conn) is False
            _apply(conn, f"alter table {table} drop constraint {name}")
            _apply(conn, f"alter table {table} add constraint {name} check ({expression})")
            assert _predeploy_compatible(conn) is True


def test_predeploy_schema_rpc_attests_truncate_guard_and_privileges():
    """A named trigger on the wrong event cannot attest append-only status."""
    with _database(apply_ledger=True) as conn:
        _apply(conn, PREDEPLOY_MIGRATION.read_text(encoding="utf-8"))
        assert _predeploy_compatible(conn) is True
        table = "public.wow_independent_verification_receipts"
        _apply(conn, f"grant truncate on {table} to service_role")
        assert _predeploy_compatible(conn) is False
        _apply(conn, f"revoke truncate on {table} from service_role")
        assert _predeploy_compatible(conn) is True

        _apply(conn, f"drop trigger wow_independent_verification_receipts_no_truncate on {table}")
        _apply(
            conn,
            "create trigger wow_independent_verification_receipts_no_truncate "
            f"before insert on {table} for each row "
            "execute function public.wow_independent_verification_receipts_no_truncate()",
        )
        assert _predeploy_compatible(conn) is False


def test_predeploy_schema_rpc_rejects_ledger_calibrator_guard_and_grant_drift():
    """Exercise fail-closed verdicts against mutated real PostgreSQL schemas."""
    table = "public.wow_independent_verification_receipts"
    with _database(apply_ledger=False) as conn:
        _apply(conn, PREDEPLOY_MIGRATION.read_text(encoding="utf-8"))
        assert _predeploy_compatible(conn) is False

    with _database(apply_ledger=True) as conn:
        _apply(conn, PREDEPLOY_MIGRATION.read_text(encoding="utf-8"))
        assert _predeploy_compatible(conn) is True
        mutations = (
            (
                "alter table public.wow_basketball_team_event_calibrators "
                "drop column calibration_sha256",
                "alter table public.wow_basketball_team_event_calibrators "
                "add column calibration_sha256 text",
            ),
            (
                f"alter table {table} drop constraint wow_ivr_owner_independence",
                f"alter table {table} add constraint wow_ivr_owner_independence "
                "check (implementation_owner <> verification_owner)",
            ),
            (
                f"alter table {table} disable trigger "
                "wow_independent_verification_receipts_immutable",
                f"alter table {table} enable trigger "
                "wow_independent_verification_receipts_immutable",
            ),
            (
                f"grant select on {table} to anon",
                f"revoke select on {table} from anon",
            ),
            (
                f"grant select (receipt_id) on {table} to authenticated",
                f"revoke select (receipt_id) on {table} from authenticated",
            ),
            (
                f"grant update (receipt_id) on {table} to service_role",
                f"revoke update (receipt_id) on {table} from service_role",
            ),
        )
        for corrupt, restore in mutations:
            _apply(conn, corrupt)
            assert _predeploy_compatible(conn) is False, corrupt
            _apply(conn, restore)
            assert _predeploy_compatible(conn) is True, restore
