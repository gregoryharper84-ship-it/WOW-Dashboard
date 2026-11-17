"""Shared locality / disposable opt-in guard for PostgreSQL contract tests."""
from __future__ import annotations

import os

import pytest

psycopg = pytest.importorskip(
    "psycopg", reason="psycopg is required for database-backed migration proof"
)


def require_disposable_local_postgres_dsn(dsn: str) -> str:
    """Accept only an explicitly opted-in local disposable PostgreSQL target.

    Never treat a hostless URI as proof of locality: libpq can take the target
    from PGHOST, PGHOSTADDR or a service definition.
    """
    if os.environ.get("WOW_TEST_POSTGRES_DISPOSABLE") != "1":
        pytest.fail("WOW_TEST_POSTGRES_DISPOSABLE_REQUIRED", pytrace=False)
    params = psycopg.conninfo.conninfo_to_dict(dsn)
    host = params.get("host") or os.environ.get("PGHOST")
    hostaddr = params.get("hostaddr") or os.environ.get("PGHOSTADDR")
    service = params.get("service") or os.environ.get("PGSERVICE")
    hosts = host.split(",") if host else []
    if (
        hostaddr
        or service
        or any(
            not entry
            or (
                entry not in {"localhost", "127.0.0.1", "::1"}
                and not entry.startswith("/")
            )
            for entry in hosts
        )
    ):
        pytest.fail("WOW_TEST_POSTGRES_REMOTE_TARGET_FORBIDDEN", pytrace=False)
    return dsn
