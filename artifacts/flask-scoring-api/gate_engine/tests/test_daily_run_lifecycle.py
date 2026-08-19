"""Focused regression tests for the canonical WOW Daily lifecycle boundary."""
from __future__ import annotations

import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import gate_engine.daily_run_lifecycle as lifecycle


class _FakeProcess:
    started_count = 0

    def __init__(self, *args, **kwargs):
        self._alive = False

    def start(self):
        type(self).started_count += 1
        self._alive = True

    def is_alive(self):
        return self._alive

    def terminate(self):
        self._alive = False

    def join(self, timeout=None):
        return None


class _NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None


class TestDailyRunLifecycle(unittest.TestCase):
    def setUp(self):
        _FakeProcess.started_count = 0
        self._runs = {}
        self._lock = threading.Lock()

    def _create_or_get(self, **kwargs):
        key = kwargs["idempotency_key"] or kwargs["run_id"]
        with self._lock:
            existing = self._runs.get(key)
            if existing:
                return dict(existing), False
            run = {
                "run_id": kwargs["run_id"],
                "run_status": "ACCEPTED",
                "deadline_at": kwargs["deadline_at"],
                "progress_stage": "ACCEPTED",
            }
            self._runs[key] = run
            return dict(run), True

    def _claim(self, run_id):
        with self._lock:
            for run in self._runs.values():
                if run["run_id"] == run_id and run["run_status"] == "ACCEPTED":
                    run["run_status"] = "IN_PROGRESS"
                    run["progress_stage"] = "STARTING"
                    return True
        return False

    def _get_run(self, run_id):
        with self._lock:
            for run in self._runs.values():
                if run["run_id"] == run_id:
                    return dict(run)
        return None

    def _start(self, key="daily-key"):
        with (
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.reap_expired_runs", return_value=0),
            patch("storage.daily_manifest.create_or_get_run", side_effect=self._create_or_get),
            patch("storage.daily_manifest.claim_run", side_effect=self._claim),
            patch("storage.daily_manifest.get_run", side_effect=self._get_run),
            patch.object(lifecycle.multiprocessing, "Process", _FakeProcess),
            patch.object(lifecycle.threading, "Thread", _NoopThread),
        ):
            return lifecycle.start_run(
                run_id=None,
                idempotency_key=key,
                sports=["NBA"],
                environment="test",
                runtime_provenance=None,
                session_id="lifecycle-test",
                deadline_seconds=30,
            )

    def test_immediate_acceptance_returns_immutable_run_id(self):
        result = self._start()
        self.assertTrue(result["accepted"])
        self.assertTrue(result["run_id"])
        self.assertEqual(result["run_status"], "IN_PROGRESS")
        self.assertFalse(result["can_execute"])
        self.assertEqual(_FakeProcess.started_count, 1)

    def test_worker_is_not_run_in_caller_thread(self):
        with patch.object(lifecycle, "_worker", side_effect=AssertionError("inline worker")):
            result = self._start()
        self.assertTrue(result["accepted"])
        self.assertEqual(_FakeProcess.started_count, 1)

    def test_sequential_duplicate_reuses_one_run(self):
        first = self._start("same-key")
        second = self._start("same-key")
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(_FakeProcess.started_count, 1)

    def test_concurrent_duplicate_submissions_converge(self):
        results = []
        worker_thread = threading.Thread

        def submit():
            results.append(self._start("concurrent-key"))

        threads = [worker_thread(target=submit) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["run_id"], results[1]["run_id"])
        self.assertEqual(_FakeProcess.started_count, 1)

    def test_whole_run_timeout_terminalizes_hung_process(self):
        process = _FakeProcess()
        process.start()
        terminalize = MagicMock()
        with patch("storage.daily_manifest.terminalize_run", terminalize):
            lifecycle._watch_deadline(
                run_id="run-timeout",
                process=process,
                deadline_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            )
        self.assertFalse(process.is_alive())
        self.assertEqual(
            terminalize.call_args.kwargs["failure_reason"],
            "WHOLE_RUN_DEADLINE_EXCEEDED",
        )

    def test_worker_exception_is_guaranteed_terminal(self):
        terminalize = MagicMock()
        with (
            patch("storage.daily_manifest.mark_progress", return_value=True),
            patch("storage.daily_manifest.terminalize_run", terminalize),
            patch(
                "gate_engine.daily_orchestrator.run_daily_orchestration",
                side_effect=RuntimeError("boom"),
            ),
        ):
            lifecycle._worker(
                run_id="run-error",
                sports=["NBA"],
                environment="test",
                runtime_provenance=None,
                session_id=None,
                deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            )
        self.assertEqual(terminalize.call_args.kwargs["run_status"], "DEGRADED")
        self.assertEqual(
            terminalize.call_args.kwargs["failure_module"],
            "daily_run_lifecycle.worker",
        )


class TestManifestTimeoutsAndReconciliation(unittest.TestCase):
    def test_db_connection_has_connect_statement_and_lock_timeouts(self):
        from storage import daily_manifest
        connection = MagicMock()
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}, clear=False),
            patch.object(daily_manifest.psycopg2, "connect", return_value=connection) as connect,
        ):
            self.assertIs(daily_manifest._get_conn(), connection)
        kwargs = connect.call_args.kwargs
        self.assertEqual(kwargs["connect_timeout"], 5)
        self.assertIn("statement_timeout=30000", kwargs["options"])
        self.assertIn("lock_timeout=5000", kwargs["options"])

    def test_schema_bootstrap_is_cached_and_advisory_locked(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        with (
            patch.object(daily_manifest, "_schema_ready", False),
            patch.object(daily_manifest, "_get_conn", return_value=conn) as get_conn,
        ):
            self.assertTrue(daily_manifest.ensure_tables())
            self.assertTrue(daily_manifest.ensure_tables())

        get_conn.assert_called_once()
        first_sql, first_params = cursor.execute.call_args_list[0].args
        self.assertIn("pg_advisory_xact_lock", first_sql)
        self.assertEqual(first_params, (daily_manifest._SCHEMA_ADVISORY_LOCK,))

    def test_existing_reconciliation_remains_exact(self):
        from gate_engine.daily_orchestrator import _build_reconciliation
        result = _build_reconciliation(
            {"one", "two"},
            {
                "watch": [
                    {"canonical_selection_id": "one"},
                    {"canonical_selection_id": "two"},
                ],
            },
        )
        self.assertTrue(result["reconciled"])
        self.assertEqual(result["discovered_count"], 2)
        self.assertEqual(result["total_terminal"], 2)
        self.assertEqual(result["duplicate_ids"], [])
        self.assertEqual(result["missing_ids"], [])
        self.assertEqual(result["excess_ids"], [])


if __name__ == "__main__":
    unittest.main()