"""Fail-closed startup warmup deadlines for the PostgreSQL session exposure ledger.

Source tests only: no production database connection, migration, or wager.
"""
import os
import sys
import types

import pytest

from gate_engine import pg_session_ledger


class _FakeConnection:
    def __init__(self, *, reject_ddl=False):
        self.calls = []
        self.commit_calls = 0
        self.reject_ddl = reject_ddl

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self

    def execute(self, query):
        self.calls.append(query)
        if self.reject_ddl and query == pg_session_ledger._DDL:
            raise RuntimeError("synthetic lock timeout")

    def commit(self):
        self.commit_calls += 1


def _install_fake_psycopg2(monkeypatch, *, reject_ddl=False):
    observed = []
    conn = _FakeConnection(reject_ddl=reject_ddl)

    def connect(*args, **kwargs):
        observed.append((args, kwargs))
        return conn

    driver = types.ModuleType("psycopg2")
    driver.connect = connect
    # Existing unit tests model a DSN with a pre-resolved address; dedicated
    # tests below exercise the bounded resolver path.
    driver.extensions = types.SimpleNamespace(
        parse_dsn=lambda _url: {"host": "fake", "hostaddr": "192.0.2.10"}
    )
    monkeypatch.setitem(sys.modules, "psycopg2", driver)
    return observed, conn


def test_startup_schema_uses_connection_and_transaction_local_ddl_deadlines(monkeypatch):
    observed, conn = _install_fake_psycopg2(monkeypatch)
    pg_session_ledger.ensure_table_exists("postgresql://fake/safety-test")

    assert observed == [(("postgresql://fake/safety-test",), {"connect_timeout": 5})]
    assert conn.calls == [
        "SET LOCAL lock_timeout = '3000ms'",
        "SET LOCAL statement_timeout = '10000ms'",
        pg_session_ledger._DDL,
    ]
    assert conn.commit_calls == 1


def test_startup_schema_uses_configured_url_without_disabling_deadlines(monkeypatch):
    observed, conn = _install_fake_psycopg2(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://configured/ci")
    pg_session_ledger.ensure_table_exists()

    assert observed == [(("postgresql://configured/ci",), {"connect_timeout": 5})]
    assert conn.calls[-1] == pg_session_ledger._DDL



def test_startup_dns_is_resolved_before_connect_and_hostaddr_is_bound(monkeypatch):
    """Pass a bounded, resolved address to libpq without dropping the TLS hostname."""
    import socket

    observed, conn = _install_fake_psycopg2(monkeypatch)
    driver = sys.modules["psycopg2"]
    driver.extensions.parse_dsn = lambda _url: {"host": "db.example.invalid", "port": "5432"}
    monkeypatch.setattr(
        pg_session_ledger.socket,
        "getaddrinfo",
        lambda host, port, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.40", port))
        ],
    )

    url = "postgresql://user:secret@db.example.invalid:5432/safety-test"
    pg_session_ledger.ensure_table_exists(url)

    assert observed == [( (url,), {"connect_timeout": 5, "hostaddr": "192.0.2.40"} )]
    assert conn.commit_calls == 1


def test_startup_dns_timeout_fails_before_opening_database_connection(monkeypatch):
    """A stalled system resolver cannot hold synchronous warmup indefinitely."""
    import threading

    observed, _conn = _install_fake_psycopg2(monkeypatch)
    driver = sys.modules["psycopg2"]
    driver.extensions.parse_dsn = lambda _url: {"host": "db.example.invalid", "port": "5432"}
    release = threading.Event()

    def blocked_resolver(*_args, **_kwargs):
        release.wait(1.0)
        return []

    monkeypatch.setattr(pg_session_ledger.socket, "getaddrinfo", blocked_resolver)
    monkeypatch.setattr(pg_session_ledger, "DNS_RESOLUTION_TIMEOUT_SECONDS", 0.01)
    try:
        with pytest.raises(TimeoutError, match="SESSION_LEDGER_DNS_TIMEOUT"):
            pg_session_ledger.ensure_table_exists(
                "postgresql://user:secret@db.example.invalid:5432/safety-test"
            )
        assert observed == []
    finally:
        # Let the daemon resolver exit promptly, then wait for its single-flight
        # slot to be returned before another test can enter the resolver path.
        release.set()
        assert pg_session_ledger._DNS_RESOLUTION_SLOT.acquire(timeout=1.0)
        pg_session_ledger._DNS_RESOLUTION_SLOT.release()



def test_dns_timeout_allows_only_one_inflight_resolver(monkeypatch):
    """A stuck system resolver must not leak one daemon thread per warmup."""
    import threading

    observed, _conn = _install_fake_psycopg2(monkeypatch)
    driver = sys.modules["psycopg2"]
    driver.extensions.parse_dsn = lambda _url: {"host": "db.example.invalid", "port": "5432"}
    started = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_resolver(*_args, **_kwargs):
        calls.append("resolve")
        started.set()
        release.wait(1.0)
        return []

    monkeypatch.setattr(pg_session_ledger.socket, "getaddrinfo", blocked_resolver)
    monkeypatch.setattr(pg_session_ledger, "DNS_RESOLUTION_TIMEOUT_SECONDS", 0.01)
    try:
        with pytest.raises(TimeoutError, match="SESSION_LEDGER_DNS_TIMEOUT"):
            pg_session_ledger.ensure_table_exists(
                "postgresql://user:secret@db.example.invalid:5432/safety-test"
            )
        assert started.is_set()
        with pytest.raises(TimeoutError, match="SESSION_LEDGER_DNS_BUSY"):
            pg_session_ledger.ensure_table_exists(
                "postgresql://user:secret@db.example.invalid:5432/safety-test"
            )
        assert calls == ["resolve"]
    finally:
        release.set()
    assert pg_session_ledger._DNS_RESOLUTION_SLOT.acquire(timeout=1.0)
    pg_session_ledger._DNS_RESOLUTION_SLOT.release()



@pytest.mark.parametrize(
    "parsed,parse_error,expected_code",
    [
        ({"host": "db-a.example,db-b.example", "port": "5432"}, None,
         "SESSION_LEDGER_DNS_MULTIHOST_UNSUPPORTED"),
        ({"host": "db.example.invalid", "port": "not-a-port"}, None,
         "SESSION_LEDGER_DSN_PORT_INVALID"),
        (None, ValueError("secret DSN parse detail"), "SESSION_LEDGER_DSN_INVALID"),
    ],
)
def test_invalid_dns_dsns_fail_before_connect(monkeypatch, parsed, parse_error, expected_code):
    """Malformed, multi-host and invalid-port DSNs never open a DB connection."""
    observed, _conn = _install_fake_psycopg2(monkeypatch)
    driver = sys.modules["psycopg2"]
    if parse_error is not None:
        def parse_failed(_url):
            """Simulate a libpq DSN parser rejection without exposing its detail."""
            raise parse_error
        driver.extensions.parse_dsn = parse_failed
    else:
        driver.extensions.parse_dsn = lambda _url: parsed

    with pytest.raises(ValueError, match=expected_code):
        pg_session_ledger.ensure_table_exists(
            "postgresql://user:secret@db.example.invalid:5432/safety-test"
        )
    assert observed == []


@pytest.mark.parametrize("resolver_mode", ["oserror", "empty"])
def test_dns_resolution_failure_and_empty_results_fail_before_connect(monkeypatch, resolver_mode):
    """Resolver errors and empty address sets fail closed before libpq connect."""
    observed, _conn = _install_fake_psycopg2(monkeypatch)
    driver = sys.modules["psycopg2"]
    driver.extensions.parse_dsn = lambda _url: {"host": "db.example.invalid", "port": "5432"}

    def resolve(*_args, **_kwargs):
        """Return no address or simulate the operating-system resolver error."""
        if resolver_mode == "oserror":
            raise OSError("secret resolver diagnostic")
        return []

    monkeypatch.setattr(pg_session_ledger.socket, "getaddrinfo", resolve)
    with pytest.raises(OSError, match="SESSION_LEDGER_DNS_FAILED"):
        pg_session_ledger.ensure_table_exists(
            "postgresql://user:secret@db.example.invalid:5432/safety-test"
        )
    assert observed == []


@pytest.mark.parametrize(
    "host,expected_kwargs",
    [
        ("192.0.2.55", {"connect_timeout": 5, "hostaddr": "192.0.2.55"}),
        ("/var/run/postgresql", {"connect_timeout": 5}),
    ],
)
def test_ip_literal_and_unix_socket_hosts_skip_dns(monkeypatch, host, expected_kwargs):
    """IP literals bind directly and socket-directory hosts stay with libpq."""
    observed, conn = _install_fake_psycopg2(monkeypatch)
    driver = sys.modules["psycopg2"]
    driver.extensions.parse_dsn = lambda _url: {"host": host, "port": "5432"}

    def unexpected_dns(*_args, **_kwargs):
        """Fail if a host that needs no DNS reaches the system resolver."""
        pytest.fail("DNS must be skipped for IP literals and Unix socket paths")

    monkeypatch.setattr(pg_session_ledger.socket, "getaddrinfo", unexpected_dns)
    pg_session_ledger.ensure_table_exists("postgresql://local/safety-test")
    assert observed == [(("postgresql://local/safety-test",), expected_kwargs)]
    assert conn.commit_calls == 1


def test_startup_ddl_failure_stays_failed_and_never_claims_commit(monkeypatch):
    observed, conn = _install_fake_psycopg2(monkeypatch, reject_ddl=True)
    with pytest.raises(RuntimeError, match="synthetic lock timeout"):
        pg_session_ledger.ensure_table_exists("postgresql://fake/locked")

    assert observed and observed[0][1]["connect_timeout"] == 5
    assert conn.calls[:2] == [
        "SET LOCAL lock_timeout = '3000ms'",
        "SET LOCAL statement_timeout = '10000ms'",
    ]
    assert conn.commit_calls == 0



def test_actual_postgres_conflicting_lock_times_out_and_recovery_succeeds():
    """CI-local PostgreSQL: 3s lock limit must stop stalled warmup DDL.

    A random isolated schema prevents modifying the main test ledger or any
    production DB. This test refuses non-local / non-CI databases outright.
    """
    import threading
    import time
    import uuid

    if os.environ.get("WOW_CI") != "1":
        pytest.skip("real DB lock test is CI-local only")
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.fail("CI-local DATABASE_URL is required for the real lock test")
    try:
        import psycopg2
    except ImportError:
        pytest.fail("psycopg2 is required for the CI PostgreSQL lock test")
    # psycopg2 does not expose sql as a package attribute until imported.
    # Explicit import makes this isolated CI integration test order-independent.
    from psycopg2 import sql
    parsed = psycopg2.extensions.parse_dsn(url)
    if parsed.get("host") not in ("localhost", "127.0.0.1") or parsed.get("dbname") != "wow_test":
        pytest.fail("CI PostgreSQL lock test must target loopback wow_test")
    # Explicit hostaddr wins over URL and PGHOSTADDR environment routing. All
    # schema create/use/drop connections remain on the local CI service.
    local_dsn = psycopg2.extensions.make_dsn(
        url, host="127.0.0.1", hostaddr="127.0.0.1", dbname="wow_test"
    )

    schema = "wow_v17_warmup_" + uuid.uuid4().hex[:16]
    identifier = sql.Identifier(schema)
    # For this test only, every warmup operation resolves unqualified DDL to
    # a disposable schema, never to the CI main or any real production table.
    schema_dsn = psycopg2.extensions.make_dsn(
        local_dsn, options=f"-csearch_path={schema}"
    )
    with psycopg2.connect(local_dsn, connect_timeout=5) as setup:
        with setup.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(identifier))

    locked = None
    try:
        pg_session_ledger.ensure_table_exists(schema_dsn)
        # Force the next warmup to rebuild the index; otherwise IF NOT EXISTS
        # may skip its DDL without needing a table lock.
        with psycopg2.connect(schema_dsn, connect_timeout=5) as edit:
            with edit.cursor() as cur:
                cur.execute("DROP INDEX idx_wse_expires")
        locked = psycopg2.connect(schema_dsn, connect_timeout=5)
        with locked.cursor() as cur:
            cur.execute("LOCK TABLE wow_session_exposure IN ACCESS EXCLUSIVE MODE")

        errors = []

        def attempt_warmup():
            try:
                pg_session_ledger.ensure_table_exists(schema_dsn)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=attempt_warmup, daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(timeout=8.0)
        exceeded_deadline = worker.is_alive()
        locked.rollback()  # release the lock even if the warmup hangs
        worker.join(timeout=5.0)
        elapsed = time.monotonic() - started

        assert not exceeded_deadline, f"warmup exceeded bounded lock wait ({elapsed:.2f}s)"
        assert not worker.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], psycopg2.Error)
        assert errors[0].pgcode == "55P03", repr(errors[0])

        with psycopg2.connect(schema_dsn, connect_timeout=5) as check:
            with check.cursor() as cur:
                cur.execute("SELECT to_regclass('idx_wse_expires')")
                assert cur.fetchone()[0] is None, "failed DDL must not create the index"

        pg_session_ledger.ensure_table_exists(schema_dsn)
        with psycopg2.connect(schema_dsn, connect_timeout=5) as check:
            with check.cursor() as cur:
                cur.execute("SELECT to_regclass('idx_wse_expires')")
                assert cur.fetchone()[0] is not None
    finally:
        if locked is not None:
            locked.rollback()
            locked.close()
        with psycopg2.connect(local_dsn, connect_timeout=5) as cleanup:
            with cleanup.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(identifier))
