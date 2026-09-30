from __future__ import annotations

import sys
import types

from v17 import engineering_auditor_worker as worker


def test_auditor_db_client_uses_service_role_env_without_ledger_import(monkeypatch):
    seen = {}
    sentinel = object()
    fake_supabase = types.ModuleType("supabase")

    def create_client(url, key):
        seen["url"] = url
        seen["key"] = key
        return sentinel

    fake_supabase.create_client = create_client
    monkeypatch.setitem(sys.modules, "supabase", fake_supabase)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-role-test-value")

    assert worker._db_client() is sentinel
    assert seen == {
        "url": "https://example.supabase.co",
        "key": "service-role-test-value",
    }
