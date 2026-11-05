"""Exercise the actual CM bootstrap function without starting the Flask server."""
import ast
import logging
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

APP = Path(__file__).resolve().parents[2] / "app.py"


def _bootstrap_namespace(connection):
    tree = ast.parse(APP.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_cm_ensure_schema")
    namespace = {
        "_CM_SCHEMA_READY": False,
        "_CM_SLIP_MIGRATE_READY": False,
        "_CM_SCHEMA_LOCK": threading.Lock(),
        "get_db_conn": lambda: connection,
        "app": SimpleNamespace(logger=logging.getLogger(__name__)),
    }
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "_CM_SCHEMA_DDL", "_CM_SLIP_MIGRATE_DDL", "_CM_SHADOW_RESULT_MIGRATE_DDL"
                ):
                    namespace[target.id] = ast.literal_eval(node.value)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(APP), "exec"), namespace)
    return namespace


class Connection:
    def __init__(self, fail=None):
        self.statements = []
        self.committed = False
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self

    def execute(self, statement):
        self.statements.append(statement)
        if self.fail and self.fail in statement:
            raise RuntimeError("DDL failed")

    def commit(self):
        self.committed = True


def test_cm_required_ddl_failure_never_marks_schema_ready():
    conn = Connection(fail="CREATE TABLE")
    ns = _bootstrap_namespace(conn)
    with pytest.raises(RuntimeError, match="DDL failed"):
        ns["_cm_ensure_schema"]()
    assert not ns["_CM_SCHEMA_READY"]
    assert not conn.committed
    assert conn.statements[:2] == ["SET LOCAL lock_timeout = '3000ms'", "SET LOCAL statement_timeout = '10000ms'"]


@pytest.mark.parametrize("ddl,savepoint", [
    ("ALTER TABLE cm_slips", "cm_slip_migration"),
    ("ALTER TABLE kalshi_wx_shadow_results", "cm_shadow_migration"),
])
def test_cm_optional_migration_failure_uses_savepoint_and_retains_required_schema(ddl, savepoint):
    conn = Connection(fail=ddl)
    ns = _bootstrap_namespace(conn)
    ns["_cm_ensure_schema"]()
    assert conn.committed
    assert ns["_CM_SCHEMA_READY"]
    assert f"ROLLBACK TO SAVEPOINT {savepoint}" in conn.statements
    assert f"RELEASE SAVEPOINT {savepoint}" in conn.statements
    # The following optional migration still executes after rollback.
    assert ns["_CM_SHADOW_RESULT_MIGRATE_DDL"] in conn.statements


def test_cm_success_keeps_readiness_idempotent_and_deadlines_transaction_local():
    conn = Connection()
    ns = _bootstrap_namespace(conn)
    ns["_cm_ensure_schema"]()
    assert conn.statements[:2] == ["SET LOCAL lock_timeout = '3000ms'", "SET LOCAL statement_timeout = '10000ms'"]
    assert conn.committed and ns["_CM_SCHEMA_READY"]
    count = len(conn.statements)
    ns["_cm_ensure_schema"]()
    assert len(conn.statements) == count


def test_cm_optional_slip_failure_is_retried_without_restarting_required_schema():
    conn = Connection(fail="ALTER TABLE cm_slips")
    ns = _bootstrap_namespace(conn)
    ns["_cm_ensure_schema"]()
    assert ns["_CM_SCHEMA_READY"]
    assert not ns["_CM_SLIP_MIGRATE_READY"]
    conn.fail = None
    ns["_cm_ensure_schema"]()
    assert conn.statements.count(ns["_CM_SLIP_MIGRATE_DDL"]) == 2
    assert ns["_CM_SCHEMA_READY"] and ns["_CM_SLIP_MIGRATE_READY"]
    count = len(conn.statements)
    ns["_cm_ensure_schema"]()
    assert len(conn.statements) == count


def test_cm_real_postgres_held_lock_timeout_and_retry(tmp_path):
    """Use a disposable cluster, or the existing explicitly isolated CI DB."""
    import psycopg2
    from psycopg2 import sql

    server = None
    url = os.environ.get("DATABASE_URL") if os.environ.get("WOW_CI") == "1" else None
    if url:
        parsed = psycopg2.extensions.parse_dsn(url)
        if parsed.get("host") not in ("127.0.0.1", "localhost") or parsed.get("dbname") != "wow_test":
            pytest.skip("Refuse any database except isolated local CI wow_test")
    else:
        pgserver = pytest.importorskip("pgserver", reason="requires disposable PostgreSQL or CI wow_test")
        server = pgserver.get_server(tmp_path / "pgdata", cleanup_mode="delete")
        url = server.get_uri()
    import uuid
    schema = "cm_deadline_" + uuid.uuid4().hex
    locked = None
    bootstrap_connections = []
    try:
        with psycopg2.connect(url) as setup:
            with setup.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        dsn = psycopg2.extensions.make_dsn(url, options=f"-csearch_path={schema}")
        ns = _bootstrap_namespace(None)
        def connect_schema():
            connection = psycopg2.connect(dsn, connect_timeout=10)
            bootstrap_connections.append(connection)
            return connection

        ns["get_db_conn"] = connect_schema
        # A real optional statement error must not abort required DDL.
        ns["_CM_SLIP_MIGRATE_DDL"] += "\nSELECT 1/0;"
        ns["_cm_ensure_schema"]()
        assert ns["_CM_SCHEMA_READY"]
        assert not ns["_CM_SLIP_MIGRATE_READY"]
        with psycopg2.connect(dsn) as check:
            with check.cursor() as cursor:
                cursor.execute("SELECT to_regclass('cm_boards')")
                assert cursor.fetchone()[0] == "cm_boards"
        # Remove the transient error and retry without clearing readiness.
        ns["_CM_SLIP_MIGRATE_DDL"] = ns["_CM_SLIP_MIGRATE_DDL"].removesuffix("\nSELECT 1/0;")
        ns["_cm_ensure_schema"]()
        assert ns["_CM_SLIP_MIGRATE_READY"]
        ns["_CM_SCHEMA_READY"] = False
        locked = psycopg2.connect(dsn)
        with locked.cursor() as cursor:
            cursor.execute("LOCK TABLE cm_boards IN ACCESS EXCLUSIVE MODE")
        errors = []

        def initialize():
            try:
                ns["_cm_ensure_schema"]()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=initialize, daemon=True)
        worker.start()
        worker.join(timeout=6)
        exceeded = worker.is_alive()
        locked.rollback()
        worker.join(timeout=5)
        assert not exceeded, "CM DDL exceeded bounded lock wait"
        assert len(errors) == 1 and errors[0].pgcode == "55P03"
        assert not ns["_CM_SCHEMA_READY"]
        ns["_cm_ensure_schema"]()
        assert ns["_CM_SCHEMA_READY"]
        # SHOW must inspect the actual bootstrap sessions, including rollback
        # and recovery. A newly opened session would trivially show defaults.
        for check in bootstrap_connections:
            assert not check.closed
            with check.cursor() as cursor:
                cursor.execute("SHOW lock_timeout")
                assert cursor.fetchone()[0] == "0"
                cursor.execute("SHOW statement_timeout")
                assert cursor.fetchone()[0] == "0"
    finally:
        for connection in bootstrap_connections:
            connection.close()
        if locked is not None:
            locked.rollback()
            locked.close()
        with psycopg2.connect(url) as cleanup:
            with cleanup.cursor() as cursor:
                cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        if server is not None:
            server.cleanup()
