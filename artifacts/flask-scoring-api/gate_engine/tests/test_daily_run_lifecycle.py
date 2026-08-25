"""Focused regression tests for the canonical WOW Daily lifecycle boundary."""
from __future__ import annotations

import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import gate_engine.daily_run_lifecycle as lifecycle


class _CapturingPopen:
    calls: list[tuple[tuple, dict]] = []
    pid = 4242

    def __init__(self, *args, **kwargs):
        type(self).calls.append((args, kwargs))

    def wait(self):
        return 0


class _InlineThread:
    def __init__(self, *, target, args=(), kwargs=None, **_unused):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class TestDailyRunLifecycle(unittest.TestCase):
    def setUp(self):
        _CapturingPopen.calls = []
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

    def _claim(self, run_id, execution_owner):
        with self._lock:
            for run in self._runs.values():
                if run["run_id"] == run_id and run["run_status"] == "ACCEPTED":
                    run["run_status"] = "IN_PROGRESS"
                    run["progress_stage"] = "STARTING"
                    run["execution_owner"] = execution_owner
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
            patch("storage.daily_manifest.register_executor", return_value=True),
            patch("storage.daily_manifest.get_run", side_effect=self._get_run),
            patch.object(lifecycle.subprocess, "Popen", _CapturingPopen),
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
        self.assertEqual(result["latest_detail"], result["progress_detail"])
        self.assertEqual(result["rows_committed"], result["total_discovered"])
        self.assertFalse(result["can_execute"])
        self.assertEqual(len(_CapturingPopen.calls), 1)
        command, kwargs = _CapturingPopen.calls[0]
        self.assertIn("gate_engine.daily_run_executor", command[0])
        self.assertTrue(kwargs["start_new_session"])
        self.assertTrue(kwargs["close_fds"])

    def test_manifest_identity_uses_intended_date_and_timezone(self):
        captured = {}

        def capture_create_or_get(**kwargs):
            captured.update(kwargs)
            return self._create_or_get(**kwargs)

        with (
            patch("storage.daily_manifest.create_or_get_run", side_effect=capture_create_or_get),
            patch("storage.daily_manifest.claim_run", side_effect=self._claim),
            patch("storage.daily_manifest.register_executor", return_value=True),
            patch.object(lifecycle.subprocess, "Popen", _CapturingPopen),
        ):
            result = lifecycle.start_run(
                run_id=None,
                idempotency_key="date-aware-key",
                sports=["NBA"],
                environment="test",
                runtime_provenance=None,
                session_id=None,
                intended_date="2026-08-20",
                run_timezone="America/New_York",
            )

        self.assertEqual(captured["run_date"], "2026-08-20")
        self.assertEqual(captured["run_timezone"], "America/New_York")

    def test_scope_is_captured_and_echoed_as_immutable_request_identity(self):
        captured = {}

        def capture_create_or_get(**kwargs):
            captured.update(kwargs)
            return self._create_or_get(**kwargs)

        with (
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch("storage.daily_manifest.reap_expired_runs", return_value=0),
            patch("storage.daily_manifest.create_or_get_run", side_effect=capture_create_or_get),
            patch("storage.daily_manifest.claim_run", side_effect=self._claim),
            patch("storage.daily_manifest.register_executor", return_value=True),
            patch("storage.daily_manifest.get_run", side_effect=self._get_run),
            patch.object(lifecycle.subprocess, "Popen", _CapturingPopen),
        ):
            result = lifecycle.start_run(
                run_id=None,
                idempotency_key="scoped-key",
                sports=["MLB"],
                environment="test",
                runtime_provenance=None,
                session_id="scope-test",
                intended_date="2026-08-20",
                run_timezone="America/Chicago",
                scope=lifecycle.SCOPE_MONEYLINE_REMAINING_TODAY,
            )
        self.assertEqual(
            captured["request_scope"],
            lifecycle.SCOPE_MONEYLINE_REMAINING_TODAY,
        )
        self.assertTrue(captured["scope_requested_at"])
        self.assertEqual(result["scope"], lifecycle.SCOPE_MONEYLINE_REMAINING_TODAY)
        self.assertFalse(result["terminal"])
        self.assertTrue(captured["request_fingerprint"])
        self.assertEqual(result["run_date"], "2026-08-20")
        self.assertEqual(result["timezone"], "America/Chicago")

    def test_identity_is_required_before_manifest_work(self):
        with self.assertRaisesRegex(ValueError, "idempotency_key or run_id"):
            lifecycle.start_run(
                run_id=None,
                idempotency_key=None,
                sports=["NBA"],
                environment="test",
                runtime_provenance=None,
                session_id=None,
            )

    def test_acknowledgement_path_skips_schema_bootstrap_and_reaping(self):
        with (
            patch("storage.daily_manifest.ensure_tables") as ensure,
            patch("storage.daily_manifest.reap_expired_runs") as reap,
        ):
            self._start("acknowledgement-only")
        ensure.assert_not_called()
        reap.assert_not_called()

    def test_executor_is_not_run_in_caller_thread(self):
        with patch.object(
            lifecycle,
            "_launch_executor",
            return_value=MagicMock(pid=4242),
        ) as launch:
            result = self._start()
        self.assertTrue(result["accepted"])
        launch.assert_called_once()

    def test_sequential_duplicate_reuses_one_run(self):
        first = self._start("same-key")
        second = self._start("same-key")
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(_CapturingPopen.calls), 1)

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
        self.assertEqual(len(_CapturingPopen.calls), 1)

    def test_claimed_runner_is_launched_only_with_canonical_identity(self):
        stored_run = {
            "run_id": "canonical-run",
            "run_status": "ACCEPTED",
            "progress_stage": "ACCEPTED",
            "deadline_at": datetime.now(timezone.utc).isoformat(),
            "requested_sports": ["WNBA"],
            "environment": "test",
            "runtime_provenance": {"source": "first"},
            "session_id": "first-session",
        }
        with (
            patch("storage.daily_manifest.create_or_get_run", return_value=(stored_run, False)),
            patch("storage.daily_manifest.claim_run", return_value=True),
            patch("storage.daily_manifest.register_executor", return_value=True),
            patch.object(lifecycle.subprocess, "Popen", _CapturingPopen),
        ):
            lifecycle.start_run(
                run_id=None,
                idempotency_key="shared-key",
                sports=["MLB"],
                environment="live",
                runtime_provenance={"source": "retry"},
                session_id="retry-session",
                deadline_seconds=30,
            )
        command, _kwargs = _CapturingPopen.calls[0]
        self.assertEqual(command[0][command[0].index("--run-id") + 1], "canonical-run")
        self.assertNotIn("WNBA", command[0])
        self.assertNotIn("retry-session", command[0])

    def test_restart_reaper_delegates_expired_run_terminalization(self):
        with (
            patch("storage.daily_manifest.ensure_tables", return_value=True) as ensure,
            patch("storage.daily_manifest.reap_expired_runs", return_value=2) as reap,
        ):
            self.assertEqual(lifecycle.reap_expired_runs_once(), 2)
        ensure.assert_called_once()
        self.assertIn("now", reap.call_args.kwargs)

    def test_reaper_terminates_reaped_detached_executor_process_group(self):
        with (
            patch("storage.daily_manifest.ensure_tables", return_value=True),
            patch(
                "storage.daily_manifest.reap_expired_runs",
                return_value=[{"run_id": "expired-run", "executor_pid": 4242}],
            ) as reap,
            patch.object(lifecycle, "_terminate_executor_process_group") as terminate,
        ):
            self.assertEqual(lifecycle.reap_expired_runs_once(), 1)
        self.assertTrue(reap.call_args.kwargs["include_executor_records"])
        terminate.assert_called_once_with(
            run_id="expired-run",
            executor_pid=4242,
        )

    def test_reaper_escalates_from_term_to_kill_for_blocked_executor(self):
        with (
            patch.object(
                lifecycle,
                "_is_daily_executor_pid",
                side_effect=[True, True],
            ),
            patch.object(lifecycle.os, "killpg") as killpg,
            patch.object(lifecycle.time, "sleep", return_value=None),
            patch.object(lifecycle.threading, "Thread", _InlineThread),
        ):
            lifecycle._terminate_executor_process_group(
                run_id="blocked-run",
                executor_pid=4242,
            )
        self.assertEqual(
            killpg.call_args_list,
            [
                unittest.mock.call(4242, lifecycle.signal.SIGTERM),
                unittest.mock.call(4242, lifecycle.signal.SIGKILL),
            ],
        )

    def test_unexpected_child_exit_terminalizes_active_owned_manifest(self):
        process = MagicMock()
        process.wait.return_value = 17
        with patch.object(lifecycle, "_safe_terminalize") as terminalize:
            lifecycle._reap_executor_child(
                process,
                run_id="early-exit-run",
                execution_owner="owner-1",
            )
        terminalize.assert_called_once_with(
            run_id="early-exit-run",
            finished_at=unittest.mock.ANY,
            run_status="DEGRADED",
            failure_reason="RUNNER_UNEXPECTED_EXIT_17",
            failure_module="daily_run_lifecycle._reap_executor_child",
            execution_owner="owner-1",
        )

    def test_manifest_readiness_bootstraps_before_serving(self):
        with patch(
            "storage.daily_manifest.ensure_tables",
            return_value=True,
        ) as ensure:
            lifecycle.ensure_manifest_ready()
        ensure.assert_called_once()

    def test_manifest_readiness_fails_closed_when_bootstrap_fails(self):
        with patch(
            "storage.daily_manifest.ensure_tables",
            return_value=False,
        ):
            with self.assertRaisesRegex(RuntimeError, "DAILY_MANIFEST_UNAVAILABLE"):
                lifecycle.ensure_manifest_ready()

    def test_post_fork_reset_allows_a_real_worker_reaper(self):
        original_lock = lifecycle._reaper_lock
        lifecycle._reaper_started = True
        lifecycle.reset_after_fork()
        self.assertFalse(lifecycle._reaper_started)
        self.assertIsNot(lifecycle._reaper_lock, original_lock)


class TestDetachedDailyExecutor(unittest.TestCase):
    def test_heartbeat_notifies_executor_when_ownership_is_lost(self):
        from gate_engine import daily_run_executor as executor

        on_loss = MagicMock()
        heartbeat = executor._RunnerHeartbeat(
            run_id="run-lost",
            execution_owner="owner",
            on_ownership_lost=on_loss,
        )
        with patch.object(executor, "heartbeat_run", return_value=False):
            heartbeat._run()
        self.assertTrue(heartbeat.lost_ownership)
        on_loss.assert_called_once()

    def test_executor_exception_is_guaranteed_terminal(self):
        from gate_engine import daily_run_executor as executor

        heartbeat = MagicMock()
        terminalize = MagicMock()
        owned_run = {
            "run_id": "run-error",
            "run_status": "IN_PROGRESS",
            "execution_owner": "owner",
            "requested_sports": ["NBA"],
            "environment": "test",
        }
        with (
            patch.object(executor, "get_run", return_value=owned_run),
            patch.object(executor, "heartbeat_run", return_value=True),
            patch.object(executor, "mark_progress", return_value=True),
            patch.object(executor, "_RunnerHeartbeat", return_value=heartbeat),
            patch("storage.daily_manifest.terminalize_run", terminalize),
        ):
            exit_code = executor.execute_run(
                run_id="run-error",
                execution_owner="owner",
                orchestrator=MagicMock(side_effect=RuntimeError("boom")),
            )
        self.assertEqual(exit_code, 1)
        self.assertEqual(terminalize.call_args.kwargs["run_status"], "DEGRADED")
        self.assertEqual(
            terminalize.call_args.kwargs["failure_reason"],
            "RUNNER_EXCEPTION_RuntimeError",
        )
        heartbeat.start.assert_called_once()
        heartbeat.stop.assert_called_once()

    def test_executor_refuses_a_stale_or_terminal_owner(self):
        from gate_engine import daily_run_executor as executor

        with patch.object(
            executor,
            "get_run",
            return_value={
                "run_status": "DEGRADED",
                "finished_at": "2026-08-19T00:00:00+00:00",
                "execution_owner": "owner",
            },
        ):
            self.assertEqual(
                executor.execute_run(run_id="terminal", execution_owner="owner"),
                3,
            )

    def test_executor_does_not_report_success_after_heartbeat_ownership_loss(self):
        from gate_engine import daily_run_executor as executor

        heartbeat = MagicMock()
        heartbeat.lost_ownership = True
        owned_run = {
            "run_id": "run-lost-owner",
            "run_status": "IN_PROGRESS",
            "execution_owner": "owner",
            "requested_sports": ["NBA"],
            "environment": "test",
        }
        orchestrator = MagicMock()
        with (
            patch.object(executor, "get_run", return_value=owned_run),
            patch.object(executor, "heartbeat_run", return_value=True),
            patch.object(executor, "mark_progress", return_value=True),
            patch.object(executor, "_RunnerHeartbeat", return_value=heartbeat),
        ):
            self.assertEqual(
                executor.execute_run(
                    run_id="run-lost-owner",
                    execution_owner="owner",
                    orchestrator=orchestrator,
                ),
                5,
            )

    def test_executor_forwards_only_persisted_scope_values(self):
        from gate_engine import daily_run_executor as executor

        heartbeat = MagicMock()
        heartbeat.lost_ownership = False
        owned_run = {
            "run_id": "scoped-run",
            "run_status": "IN_PROGRESS",
            "execution_owner": "owner",
            "requested_sports": ["MLB"],
            "environment": "test",
            "request_scope": "MONEYLINE_REMAINING_TODAY",
            "scope_requested_at": "2026-08-20T17:00:00+00:00",
            "run_timezone": "America/Chicago",
        }
        orchestrator = MagicMock()
        with (
            patch.object(executor, "get_run", return_value=owned_run),
            patch.object(executor, "heartbeat_run", return_value=True),
            patch.object(executor, "mark_progress", return_value=True),
            patch.object(executor, "_RunnerHeartbeat", return_value=heartbeat),
        ):
            self.assertEqual(
                executor.execute_run(
                    run_id="scoped-run",
                    execution_owner="owner",
                    orchestrator=orchestrator,
                ),
                0,
            )
        self.assertEqual(
            orchestrator.call_args.kwargs["scope"],
            "MONEYLINE_REMAINING_TODAY",
        )
        self.assertEqual(
            orchestrator.call_args.kwargs["scope_requested_at"],
            "2026-08-20T17:00:00+00:00",
        )
        self.assertEqual(
            orchestrator.call_args.kwargs["run_timezone"],
            "America/Chicago",
        )
        self.assertEqual(
            orchestrator.call_args.kwargs["execution_owner"],
            "owner",
        )

    def test_executor_stops_before_scoring_when_fenced_progress_is_rejected(self):
        from gate_engine import daily_run_executor as executor

        heartbeat = MagicMock()
        owned_run = {
            "run_id": "run-fenced",
            "run_status": "IN_PROGRESS",
            "execution_owner": "owner",
        }
        orchestrator = MagicMock()
        with (
            patch.object(executor, "get_run", return_value=owned_run),
            patch.object(executor, "heartbeat_run", return_value=True),
            patch.object(executor, "mark_progress", return_value=False),
            patch.object(executor, "_RunnerHeartbeat", return_value=heartbeat),
        ):
            self.assertEqual(
                executor.execute_run(
                    run_id="run-fenced",
                    execution_owner="owner",
                    orchestrator=orchestrator,
                ),
                4,
            )
        orchestrator.assert_not_called()

    def test_executor_normalizes_database_deadline_timestamp(self):
        from gate_engine import daily_run_executor as executor

        heartbeat = MagicMock()
        heartbeat.lost_ownership = False
        deadline = datetime.now(timezone.utc) + timedelta(minutes=30)
        owned_run = {
            "run_id": "run-datetime-deadline",
            "run_status": "IN_PROGRESS",
            "execution_owner": "owner",
            "deadline_at": deadline,
        }
        orchestrator = MagicMock()
        with (
            patch.object(executor, "get_run", return_value=owned_run),
            patch.object(executor, "heartbeat_run", return_value=True),
            patch.object(executor, "mark_progress", return_value=True),
            patch.object(executor, "_RunnerHeartbeat", return_value=heartbeat),
        ):
            self.assertEqual(
                executor.execute_run(
                    run_id="run-datetime-deadline",
                    execution_owner="owner",
                    orchestrator=orchestrator,
                ),
                0,
            )
        self.assertEqual(
            orchestrator.call_args.kwargs["deadline_at"],
            deadline.isoformat(),
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

    def test_acknowledgement_db_profile_is_short_and_bounded(self):
        from storage import daily_manifest
        connection = MagicMock()
        with (
            patch.dict(os.environ, {"DATABASE_URL": "postgres://test"}, clear=False),
            patch.object(daily_manifest.psycopg2, "connect", return_value=connection) as connect,
        ):
            daily_manifest._get_conn(acknowledgement=True)
        kwargs = connect.call_args.kwargs
        self.assertEqual(kwargs["connect_timeout"], 2)
        self.assertIn("statement_timeout=3000", kwargs["options"])
        self.assertIn("lock_timeout=1000", kwargs["options"])

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

    def test_lease_renewal_is_fenced_to_the_claimed_executor(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            self.assertTrue(
                daily_manifest.heartbeat_run(
                    run_id="run-1",
                    execution_owner="owner-1",
                    lease_seconds=45,
                )
            )
        sql, params = cursor.execute.call_args.args
        self.assertIn("execution_owner = %s", sql)
        self.assertIn("INTERVAL '45 seconds'", sql)
        self.assertIn("lease_expires_at >= NOW()", sql)
        self.assertEqual(params, ("run-1", "owner-1"))

    def test_executor_pid_registration_is_owner_and_lease_fenced(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            self.assertTrue(
                daily_manifest.register_executor(
                    run_id="run-1",
                    execution_owner="owner-1",
                    executor_pid=4242,
                )
            )
        sql, params = cursor.execute.call_args.args
        self.assertIn("executor_pid = %s", sql)
        self.assertIn("execution_owner = %s", sql)
        self.assertIn("lease_expires_at >= NOW()", sql)
        self.assertEqual(params, (4242, "run-1", "owner-1"))

    def test_discovery_checkpoint_persists_board_before_scoring(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            self.assertTrue(
                daily_manifest.persist_discovery_checkpoint(
                    run_id="run-1",
                    scanned_sports=["NBA"],
                    missing_sports=[],
                    total_discovered=1,
                    source_union_counts={"NBA": 1},
                    discovery_checkpoint={"board": {"sports": {"NBA": []}}},
                    reconciliation_baseline={"phase": "DISCOVERY_BASELINE"},
                    execution_owner="owner-1",
                )
            )
        sql, _params = cursor.execute.call_args.args
        self.assertIn("discovery_checkpoint = %s", sql)
        self.assertIn("progress_stage = 'DISCOVERY_PERSISTED'", sql)
        self.assertIn("discovery_checkpoint IS NULL", sql)

    def test_scoring_transition_requires_persisted_discovery_checkpoint(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            self.assertTrue(
                daily_manifest.begin_scoring(
                    run_id="run-1",
                    execution_owner="owner-1",
                )
            )
        sql, _params = cursor.execute.call_args.args
        self.assertIn("progress_stage = 'SCORING'", sql)
        self.assertIn("discovery_checkpoint IS NOT NULL", sql)

    def test_restart_reaper_marks_expired_runner_with_typed_failure(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            self.assertEqual(
                daily_manifest.reap_expired_runs(now="2026-08-19T00:00:00+00:00"),
                1,
            )
        sql, params = cursor.execute.call_args.args
        self.assertIn("RUNNER_HEARTBEAT_EXPIRED", sql)
        self.assertIn("lease_expires_at < %s", sql)
        self.assertEqual(params, ("2026-08-19T00:00:00+00:00",) * 5)

    def test_terminalized_run_rejects_stale_row_write(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            result = daily_manifest.save_run_row(
                run_id="terminal-run",
                canonical_selection_id="SEL_1",
                market_version_id=None,
                run_date="2026-08-19",
                sport="MLB",
                player="Player",
                prop="HITS",
                side="OVER",
                line=0.5,
                game_date=None,
                terminal_bucket="watch",
                classification="WATCH",
                wow_score=None,
                final_approval_blocker=None,
                audit_valid=None,
                side_resolution="HOME",
                reconciliation_status="OK",
                full_row={},
            )
        self.assertFalse(result)
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertIn("FOR UPDATE", cursor.execute.call_args.args[0])

    def test_expired_owner_lease_rejects_row_write_before_reaper_runs(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            self.assertFalse(
                daily_manifest.save_run_row(
                    run_id="expired-run",
                    canonical_selection_id="SEL_1",
                    market_version_id=None,
                    run_date="2026-08-19",
                    sport="MLB",
                    player="Player",
                    prop="HITS",
                    side="OVER",
                    line=0.5,
                    game_date=None,
                    terminal_bucket="watch",
                    classification="WATCH",
                    wow_score=None,
                    final_approval_blocker=None,
                    audit_valid=None,
                    side_resolution="HOME",
                    reconciliation_status="OK",
                    full_row={},
                    execution_owner="stale-owner",
                )
            )
        sql, params = cursor.execute.call_args.args
        self.assertIn("execution_owner = %s", sql)
        self.assertIn("lease_expires_at >= NOW()", sql)
        self.assertEqual(params, ("expired-run", "stale-owner"))

    def test_stale_executor_cannot_finalize_a_terminalized_manifest(self):
        from storage import daily_manifest

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.rowcount = 0
        with patch.object(daily_manifest, "_get_conn", return_value=conn):
            self.assertFalse(
                daily_manifest.finalize_run(
                    run_id="terminal-run",
                    finished_at="2026-08-19T00:00:00+00:00",
                    run_status="COMPLETE",
                    scanned_sports=["MLB"],
                    missing_sports=[],
                    failed_modules=[],
                    total_discovered=1,
                    source_union_counts={},
                    reconciliation={"reconciled": True},
                    execution_owner="stale-owner",
                )
            )
        sql, params = cursor.execute.call_args.args
        self.assertIn("execution_owner = %s", sql)
        self.assertIn("lease_expires_at >= NOW()", sql)
        self.assertEqual(params[-1], "stale-owner")

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