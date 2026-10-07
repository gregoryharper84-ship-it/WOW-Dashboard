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


def test_auditor_startup_retry_backoff_is_bounded(monkeypatch):
    monkeypatch.setenv("WOW_ENGINEERING_AUDITOR_STARTUP_RETRY_BASE_SECONDS", "5")
    monkeypatch.setenv("WOW_ENGINEERING_AUDITOR_STARTUP_RETRY_MAX_SECONDS", "60")

    assert worker._startup_retry_delay_seconds(1) == 5
    assert worker._startup_retry_delay_seconds(2) == 10
    assert worker._startup_retry_delay_seconds(4) == 40
    assert worker._startup_retry_delay_seconds(10) == 60


def test_auditor_startup_retries_transient_timeout_then_enters_resident_loop(monkeypatch):
    health_calls = {"count": 0}
    statuses = []

    class FakeStore:
        def __init__(self, _client):
            pass

        def health(self):
            health_calls["count"] += 1
            if health_calls["count"] == 1:
                raise TimeoutError("transient Supabase timeout")
            return {}

        def touch_runtime(self, **kwargs):
            statuses.append(kwargs.get("status"))

        def reconcile_backlog(self, now=None):
            return 71

        def reconcile_due(self, now=None):
            return []

        def refresh_runtime_counts(self, now=None):
            return None

    class FakeStopEvent:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return False

        def wait(self, seconds):
            self.waits.append(seconds)
            # First wait is startup backoff. Second wait is the resident loop,
            # where the test asks the daemon to stop cleanly.
            return len(self.waits) >= 2

    stop = FakeStopEvent()
    monkeypatch.setattr(worker, "_db_client", lambda: object())
    monkeypatch.setattr(worker, "EngineeringAuditStore", FakeStore)
    monkeypatch.setattr(worker, "bootstrap_open_github_work", lambda store: 0)
    monkeypatch.setattr(
        worker,
        "reconcile_github_updates",
        lambda store, since, seen_workflow_runs: {
            "github_work_events": 0,
            "code_health_events": 0,
        },
    )
    monkeypatch.setattr(worker, "_startup_retry_delay_seconds", lambda attempt: 5)
    monkeypatch.setattr(worker, "_interval_seconds", lambda: 30)

    worker.run_engineering_auditor_loop(stop)

    assert health_calls["count"] == 2
    assert stop.waits == [5, 30]
    assert "STARTING" in statuses
    assert "RUNNING" in statuses
    assert statuses[-1] == "STOPPED"


def test_auditor_startup_does_not_retry_missing_secure_configuration(monkeypatch):
    class FakeStopEvent:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return False

        def wait(self, seconds):
            self.waits.append(seconds)
            return False

    stop = FakeStopEvent()
    monkeypatch.setattr(
        worker,
        "_db_client",
        lambda: (_ for _ in ()).throw(RuntimeError("SUPABASE_URL unavailable")),
    )

    worker.run_engineering_auditor_loop(stop)

    assert stop.waits == []
