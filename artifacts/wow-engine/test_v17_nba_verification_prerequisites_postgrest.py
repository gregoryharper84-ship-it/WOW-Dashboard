"""Integration: the prerequisite probe over a real PostgREST HTTP endpoint.

The PostgreSQL check proves the probe reads SQLSTATE. This one proves that the
codes a real PostgREST 12.2.3 (the version the repository CI pins) returns for
the probe's two failure modes are codes the probe actually accepts, instead of
assuming which ones it emits:

| Phase | Endpoint behaviour | Observed raw code | Probe result |
|---|---|---|---|
| A | ledger relation absent | `42P01` (404) | `NBA_VERIFICATION_LEDGER_MISSING` |
| A | calibrator evidence columns absent | `42703` (400) | (same run) |
| B | ledger migration applied | `200` | ready |
| C | evidence columns dropped | `42703` (400) | `NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING` |
| D | schema cache reloaded without the columns | `42703` / `PGRST204` | `NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING` |
| E | migration re-applied | `200` | ready |

Both raw codes are asserted to be members of the probe's accepted sets
(`_MISSING_TABLE_CODES`, `_MISSING_COLUMN_CODES`), and each phase also asserts
the probe's own typed classification, so a change in either PostgREST or the
probe fails here. Phase D accepts either code because which one appears depends
on PostgREST's schema-cache state; the probe must classify both.

Supabase exposes PostgREST under `/rest/v1` (its gateway strips that prefix);
PostgREST itself serves relations at `/`. A loopback gateway performs that
rewrite so the probe and its Supabase-shaped client issue exactly the URLs they
issue in production, and only the prefix translation is local.

Requires `WOW_TEST_POSTGRES_DSN` (a superuser DSN for the database PostgREST
serves; the base schema is applied by this module) and `WOW_TEST_POSTGREST_URL`
(PostgREST v12.2.3). Skips when either is absent, so it costs nothing where no
endpoint exists.

The anon role is a superuser here on purpose: this test is about schema-cache
and SQL error classification, not row-level security, and RLS would mask the
distinction under test. The Supabase roles the ledger migration revokes from
(`anon`, `authenticated`, `service_role`) do not exist in vanilla PostgreSQL,
so they are created first; without them that migration fails and the ledger
table is silently never created.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from v17 import nba_verification_wiring as wiring

DSN = os.environ.get("WOW_TEST_POSTGRES_DSN", "").strip()
BASE_URL = os.environ.get("WOW_TEST_POSTGREST_URL", "").strip().rstrip("/")
pytestmark = pytest.mark.skipif(
    not (DSN and BASE_URL),
    reason="WOW_TEST_POSTGRES_DSN and WOW_TEST_POSTGREST_URL are not configured",
)

psycopg = pytest.importorskip("psycopg")

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
GUARD_MIGRATION = MIGRATIONS / "20260915_harden_immutable_mutation_trigger_search_path.sql"
BASE_MIGRATION = MIGRATIONS / "20260913_basketball_team_event_specialists.sql"
LEDGER_MIGRATION = MIGRATIONS / "20261110_v17_independent_verification_receipts.sql"

SUPABASE_ROLES = ("anon", "authenticated", "service_role")
LEDGER_COLUMNS = "receipt_id,receipt_digest,independent_verification_status,can_execute"
CALIBRATOR_COLUMNS = "calibration_sha256,calibration_fit_end"
DROP_EVIDENCE_COLUMNS = (
    "alter table public.wow_basketball_team_event_calibrators "
    "drop column if exists calibration_sha256, "
    "drop column if exists calibration_fit_end;"
)


class _ProviderError(RuntimeError):
    """Carries the `code` a supabase/PostgREST client would have raised."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _apply(conn: Any, sql_text: str) -> None:
    result = conn.pgconn.exec_(sql_text.encode("utf-8"))
    if result.status not in (
        psycopg.pq.ExecStatus.COMMAND_OK,
        psycopg.pq.ExecStatus.TUPLES_OK,
    ):
        raise AssertionError(
            "statement failed: " + result.error_message.decode("utf-8", "replace")
        )


def _ensure_supabase_roles(conn: Any) -> None:
    for role in SUPABASE_ROLES:
        exists = conn.execute(
            "select 1 from pg_roles where rolname = %s", (role,)
        ).fetchone()
        if not exists:
            _apply(conn, f"create role {role} nologin")


def _reload_schema_cache(conn: Any) -> None:
    """Ask PostgREST to re-read the schema; the notification is asynchronous."""
    _apply(conn, "notify pgrst, 'reload schema'")


def _wait_for(
    probe: Callable[[], Any],
    predicate: Callable[[Any], bool],
    *,
    timeout: float = 15.0,
    interval: float = 0.1,
) -> Any:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if predicate(last):
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not reached within {timeout}s; last={last!r}")


def _http(
    base_url: str, table: str, columns: str
) -> tuple[int, str | None, str]:
    """GET one row the way a PostgREST client would, returning (status, code, msg)."""
    query = urllib.parse.urlencode({"select": columns, "limit": "1"})
    url = f"{base_url}/rest/v1/{urllib.parse.quote(table)}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, None, ""
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {}
        message = str(parsed.get("message") or body)
        return exc.code, parsed.get("code"), message[:300]


class _GatewayHandler(BaseHTTPRequestHandler):
    """Translate Supabase's `/rest/v1/<table>` onto PostgREST's `/<table>`."""

    def do_GET(self) -> None:
        if not self.path.startswith("/rest/v1/"):
            self.send_error(404)
            return
        target = self.server.upstream + self.path[len("/rest/v1") :]  # type: ignore[attr-defined]
        request = urllib.request.Request(
            target, headers={"Accept": self.headers.get("Accept", "application/json")}
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        except urllib.error.URLError as exc:
            self.send_error(502, str(exc.reason))
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:  # noqa: D401 - quiet fixture
        pass


class _GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    upstream = ""


@pytest.fixture(scope="module")
def endpoint() -> str:
    server = _GatewayServer(("127.0.0.1", 0), _GatewayHandler)
    server.upstream = BASE_URL
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _PostgrestClient:
    """Supabase-shaped client over real HTTP, so the probe runs unmodified."""

    def __init__(self, base_url: str, *, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout

    def table(self, name: str) -> "_PostgrestQuery":
        return _PostgrestQuery(self, name)


class _PostgrestQuery:
    def __init__(self, client: _PostgrestClient, table: str):
        self.client = client
        self.table_name = table
        self.columns = "*"
        self.limit_value: int | None = None

    def select(self, columns: str) -> "_PostgrestQuery":
        self.columns = columns
        return self

    def limit(self, value: int) -> "_PostgrestQuery":
        self.limit_value = value
        return self

    def execute(self) -> Any:
        params: dict[str, str] = {"select": self.columns}
        if self.limit_value is not None:
            params["limit"] = str(self.limit_value)
        url = (
            f"{self.client.base_url}/rest/v1/"
            f"{urllib.parse.quote(self.table_name)}?{urllib.parse.urlencode(params)}"
        )
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.client.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                parsed = {}
            raise _ProviderError(
                str(parsed.get("code") or f"HTTP_{exc.code}"),
                str(parsed.get("message") or body)[:300],
            ) from exc
        return SimpleNamespace(data=payload if isinstance(payload, list) else [])


@pytest.fixture(scope="module")
def conn(endpoint: str) -> Any:
    with psycopg.connect(DSN, autocommit=True) as connection:
        _ensure_supabase_roles(connection)
        _apply(connection, GUARD_MIGRATION.read_text(encoding="utf-8"))
        _apply(connection, BASE_MIGRATION.read_text(encoding="utf-8"))
        _reload_schema_cache(connection)
        # Wait until the cache knows the base tables, otherwise phase A could
        # observe a missing table instead of a missing column.
        _wait_for(
            lambda: _http(endpoint, wiring.CALIBRATOR_TABLE, "sport"),
            lambda outcome: outcome[0] == 200,
        )
        yield connection


def test_probe_classifies_real_postgrest_schema_errors(
    conn: Any, endpoint: str
) -> None:
    client = _PostgrestClient(endpoint)

    # ---- A: ledger relation absent, calibrator evidence columns absent -----
    status, code, message = _wait_for(
        lambda: _http(endpoint, wiring.RECEIPT_TABLE, LEDGER_COLUMNS),
        lambda outcome: outcome[1] == "42P01",
    )
    assert status == 404, (status, code, message)
    assert code in wiring._MISSING_TABLE_CODES, (code, message)
    assert "does not exist" in message.lower(), message

    missing = wiring.verification_prerequisites(client)
    ledger_probe, calibrator_probe = missing["probes"]
    assert missing["ready"] is False
    assert missing["code"] == "NBA_VERIFICATION_LEDGER_MISSING"
    assert ledger_probe["provider_code"] == "42P01"
    assert ledger_probe["classification"] == "TABLE_MISSING"
    assert calibrator_probe["provider_code"] == "42703"
    assert calibrator_probe["provider_code"] in wiring._MISSING_COLUMN_CODES
    assert calibrator_probe["classification"] == "COLUMN_MISSING"
    assert missing["missing"] == [
        wiring.RECEIPT_TABLE,
        f"{wiring.CALIBRATOR_TABLE}.columns",
    ]

    # ---- B: ledger migration applied ---------------------------------------
    _apply(conn, LEDGER_MIGRATION.read_text(encoding="utf-8"))
    _reload_schema_cache(conn)
    ready = _wait_for(
        lambda: wiring.verification_prerequisites(client),
        lambda outcome: outcome["ready"],
    )
    assert ready["code"] is None
    assert [probe["classification"] for probe in ready["probes"]] == [
        "READY",
        "READY",
    ]

    # ---- C: columns dropped -> Postgres answers ----------------------------
    _apply(conn, DROP_EVIDENCE_COLUMNS)
    status, code, message = _wait_for(
        lambda: _http(endpoint, wiring.CALIBRATOR_TABLE, CALIBRATOR_COLUMNS),
        lambda outcome: outcome[1] == "42703",
    )
    assert status >= 400, (status, code, message)
    stale = wiring.verification_prerequisites(client)
    assert stale["code"] == "NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING"
    assert stale["probes"][0]["classification"] == "READY"
    assert stale["probes"][1]["provider_code"] == "42703"
    assert stale["probes"][1]["classification"] == "COLUMN_MISSING"

    # ---- D: cache reloaded without the columns -----------------------------
    _reload_schema_cache(conn)
    status, code, message = _wait_for(
        lambda: _http(endpoint, wiring.CALIBRATOR_TABLE, CALIBRATOR_COLUMNS),
        lambda outcome: outcome[1] in wiring._MISSING_COLUMN_CODES,
    )
    assert code in ("42703", "PGRST204"), (status, code, message)
    cached = wiring.verification_prerequisites(client)
    assert cached["code"] == "NBA_VERIFICATION_CALIBRATOR_EVIDENCE_MISSING"
    assert cached["probes"][1]["provider_code"] in wiring._MISSING_COLUMN_CODES
    assert cached["probes"][1]["classification"] == "COLUMN_MISSING"

    # ---- E: migration re-applied, endpoint healthy again -------------------
    _apply(conn, LEDGER_MIGRATION.read_text(encoding="utf-8"))
    _reload_schema_cache(conn)
    restored = _wait_for(
        lambda: wiring.verification_prerequisites(client),
        lambda outcome: outcome["ready"],
    )
    assert restored["ready"] is True
    assert restored["code"] is None
    for probe in restored["probes"]:
        assert probe["classification"] == "READY"
        assert probe["can_execute"] is False
