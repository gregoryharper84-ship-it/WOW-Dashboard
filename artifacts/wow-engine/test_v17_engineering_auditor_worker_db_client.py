from __future__ import annotations

import sys
import types

import pytest

from v17 import engineering_auditor_worker as worker


def _fake_supabase(monkeypatch):
    seen = {}
    sentinel = object()
    fake_supabase = types.ModuleType("supabase")

    def create_client(url, key):
        seen["url"] = url
        seen["key"] = key
        return sentinel

    fake_supabase.create_client = create_client
    monkeypatch.setitem(sys.modules, "supabase", fake_supabase)
    return sentinel, seen


def test_auditor_db_client_uses_service_key_env_without_ledger_import(monkeypatch):
    sentinel, seen = _fake_supabase(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key-test-value")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    assert worker._db_client() is sentinel
    assert seen == {
        "url": "https://example.supabase.co",
        "key": "service-key-test-value",
    }


def test_auditor_db_client_accepts_canonical_service_role_key_alias(monkeypatch):
    sentinel, seen = _fake_supabase(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-test-value")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    assert worker._db_client() is sentinel
    assert seen == {
        "url": "https://example.supabase.co",
        "key": "service-role-test-value",
    }


def test_auditor_db_client_prefers_explicit_service_role_key(monkeypatch):
    sentinel, seen = _fake_supabase(monkeypatch)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "preferred-role-value")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "legacy-service-value")

    assert worker._db_client() is sentinel
    assert seen["key"] == "preferred-role-value"


def test_auditor_db_client_fails_closed_without_url_or_service_credential(monkeypatch):
    _fake_supabase(monkeypatch)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_URL unavailable"):
        worker._db_client()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    with pytest.raises(RuntimeError, match="SUPABASE service credential unavailable"):
        worker._db_client()
