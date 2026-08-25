"""
tests/test_universal_agent_b2_db.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B2

Database integration tests for the B2 orchestration layer.

All tests hit the real Postgres database — no mocks for persistence.
Tests are skipped if DATABASE_URL is not set or psycopg2 is unavailable.

Test classes
------------
  TestB2DBPersistence     — evidence packet + agent results + budget events written
  TestB2DBResumability    — SKIPPED_RESUMED on second run; no duplicate ACCEPTED rows

No live LLM/API calls. No app.py import. No Weather code.
"""
from __future__ import annotations

import os
import unittest
import uuid
import functools

try:
    import psycopg2
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

from gate_engine.universal_agent.audit_store import (
    ensure_tables,
    get_evidence_packet,
    get_agent_result,
    is_work_completed,
)
from gate_engine.universal_agent.evidence_packet import build_test_packet
from gate_engine.universal_agent.orchestrator import run_orchestrator, B1_ROLE_IDS
from gate_engine.universal_agent.role_runner import MockRoleRunner, RoleRunnerStatus
from gate_engine.universal_agent.roles.registry_b1 import build_b1_registry
from gate_engine.universal_agent.roles.data_slate_integrity import (
    valid_data_slate_integrity_payload,
)
from gate_engine.universal_agent.roles.news_status import valid_news_status_payload
from gate_engine.universal_agent.roles.market_exact_line import (
    valid_market_exact_line_payload,
)
from gate_engine.universal_agent.roles.sport_specialist import (
    valid_sport_specialist_payload,
)
from gate_engine.universal_agent.roles.failure_contradiction import (
    valid_failure_contradiction_payload,
)
from gate_engine.universal_agent.roles.final_refresh import valid_final_refresh_payload


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url or not _PSYCOPG2_AVAILABLE:
        return None
    try:
        return psycopg2.connect(url)
    except Exception:
        return None


_SKIP_REASON = "DATABASE_URL not set or psycopg2 not available"


def _needs_db(test_func):
    @functools.wraps(test_func)
    def wrapper(self, *args, **kwargs):
        if self._conn is None:
            self.skipTest(_SKIP_REASON)
        return test_func(self, *args, **kwargs)
    return wrapper


# ── Shared test fixtures ──────────────────────────────────────────────────────

_ALL_AGENT_IDS = [
    "uac-data-slate-integrity-v1",
    "uac-news-status-v1",
    "uac-market-exact-line-v1",
    "uac-sport-specialist-v1",
    "uac-failure-contradiction-v1",
    "uac-final-refresh-v1",
]

_VALID_PRESETS: dict = {
    "uac-data-slate-integrity-v1": valid_data_slate_integrity_payload(),
    "uac-news-status-v1":          valid_news_status_payload(),
    "uac-market-exact-line-v1":    valid_market_exact_line_payload(),
    "uac-sport-specialist-v1":     valid_sport_specialist_payload(),
    "uac-failure-contradiction-v1": valid_failure_contradiction_payload(),
    "uac-final-refresh-v1":        valid_final_refresh_payload(),
}


def _run_all_valid_with_db(conn, *, run_id=None, snapshot_id=None):
    run_id      = run_id      or f"db-run-{uuid.uuid4()}"
    snapshot_id = snapshot_id or f"db-snap-{uuid.uuid4()}"
    packet   = build_test_packet(run_id=run_id, snapshot_id=snapshot_id)
    registry = build_b1_registry()
    runner   = MockRoleRunner(presets=dict(_VALID_PRESETS))
    runners  = {aid: runner for aid in _ALL_AGENT_IDS}
    result   = run_orchestrator(packet, registry, runners, db_conn=conn)
    return result, packet


# ══════════════════════════════════════════════════════════════════════════════
# D3 — Persistence tests
# ══════════════════════════════════════════════════════════════════════════════

class TestB2DBPersistence(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._conn = _get_conn()
        if cls._conn is not None:
            ensure_tables(cls._conn)

    @classmethod
    def tearDownClass(cls):
        if cls._conn is not None:
            cls._conn.close()

    @_needs_db
    def test_evidence_packet_persisted(self):
        result, packet = _run_all_valid_with_db(self._conn)
        stored = get_evidence_packet(self._conn, packet.snapshot_id)
        self.assertIsNotNone(stored, "Evidence packet not found in DB")
        self.assertEqual(stored["snapshot_id"], packet.snapshot_id)

    @_needs_db
    def test_evidence_packet_idempotent_duplicate_snapshot(self):
        """Second run with same snapshot_id does not raise (ON CONFLICT DO NOTHING)."""
        run_id      = f"idempotent-run-{uuid.uuid4()}"
        snapshot_id = f"idempotent-snap-{uuid.uuid4()}"
        _run_all_valid_with_db(self._conn, run_id=run_id, snapshot_id=snapshot_id)
        # Second call with same snapshot_id must not raise
        try:
            _run_all_valid_with_db(self._conn, run_id=run_id, snapshot_id=snapshot_id)
        except Exception as exc:
            self.fail(f"Second run with same snapshot_id raised: {exc}")

    @_needs_db
    def test_persisted_flag_true_with_db_conn(self):
        result, _ = _run_all_valid_with_db(self._conn)
        self.assertTrue(result.persisted)

    @_needs_db
    def test_accepted_agent_results_in_db(self):
        result, packet = _run_all_valid_with_db(self._conn)
        for r in result.role_results:
            if r.status == RoleRunnerStatus.ACCEPTED:
                stored = get_agent_result(
                    self._conn,
                    run_id=packet.run_id,
                    snapshot_id=packet.snapshot_id,
                    agent_id=r.agent_id,
                )
                self.assertIsNotNone(stored,
                    f"No DB row for ACCEPTED agent {r.agent_id}")
                self.assertEqual(stored["status"], RoleRunnerStatus.ACCEPTED)

    @_needs_db
    def test_all_six_accepted_result_rows_in_db(self):
        result, packet = _run_all_valid_with_db(self._conn)
        count = 0
        for aid in _ALL_AGENT_IDS:
            stored = get_agent_result(
                self._conn,
                run_id=packet.run_id,
                snapshot_id=packet.snapshot_id,
                agent_id=aid,
            )
            if stored is not None:
                count += 1
        self.assertEqual(count, 6)

    @_needs_db
    def test_failed_agent_result_in_db(self):
        """A RUNNER_FAILED result should also appear in uac_agent_results."""
        run_id      = f"fail-run-{uuid.uuid4()}"
        snapshot_id = f"fail-snap-{uuid.uuid4()}"
        presets = dict(_VALID_PRESETS)
        presets["uac-data-slate-integrity-v1"] = RuntimeError("test failure")
        packet   = build_test_packet(run_id=run_id, snapshot_id=snapshot_id)
        registry = build_b1_registry()
        runner   = MockRoleRunner(presets=presets)
        runners  = {aid: runner for aid in _ALL_AGENT_IDS}
        run_orchestrator(packet, registry, runners, db_conn=self._conn)

        stored = get_agent_result(
            self._conn,
            run_id=run_id,
            snapshot_id=snapshot_id,
            agent_id="uac-data-slate-integrity-v1",
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], RoleRunnerStatus.RUNNER_FAILED)

    @_needs_db
    def test_accepted_roles_marked_in_resumability_table(self):
        result, packet = _run_all_valid_with_db(self._conn)
        for r in result.role_results:
            if r.status == RoleRunnerStatus.ACCEPTED:
                work_unit_id = f"{packet.snapshot_id}:{r.agent_id}"
                completed = is_work_completed(
                    self._conn,
                    run_id=packet.run_id,
                    work_unit_id=work_unit_id,
                )
                self.assertTrue(completed,
                    f"Accepted agent {r.agent_id} not in resumability table")

    @_needs_db
    def test_failed_roles_not_marked_in_resumability(self):
        """RUNNER_FAILED roles must NOT be in the resumability table."""
        run_id      = f"fail-res-{uuid.uuid4()}"
        snapshot_id = f"fail-res-snap-{uuid.uuid4()}"
        presets = dict(_VALID_PRESETS)
        presets["uac-sport-specialist-v1"] = RuntimeError("crash")
        packet   = build_test_packet(run_id=run_id, snapshot_id=snapshot_id)
        registry = build_b1_registry()
        runner   = MockRoleRunner(presets=presets)
        runners  = {aid: runner for aid in _ALL_AGENT_IDS}
        run_orchestrator(packet, registry, runners, db_conn=self._conn)

        work_unit_id = f"{snapshot_id}:uac-sport-specialist-v1"
        completed = is_work_completed(
            self._conn,
            run_id=run_id,
            work_unit_id=work_unit_id,
        )
        self.assertFalse(completed,
            "RUNNER_FAILED agent should NOT be in resumability table")


# ══════════════════════════════════════════════════════════════════════════════
# D4 — Resumability tests
# ══════════════════════════════════════════════════════════════════════════════

class TestB2DBResumability(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._conn = _get_conn()
        if cls._conn is not None:
            ensure_tables(cls._conn)

    @classmethod
    def tearDownClass(cls):
        if cls._conn is not None:
            cls._conn.close()

    @_needs_db
    def test_second_run_produces_skipped_resumed_for_accepted(self):
        """
        After a successful run, a second identical run should show SKIPPED_RESUMED
        for all six previously ACCEPTED roles (they are in uac_run_resumability).
        """
        run_id      = f"resume-run-{uuid.uuid4()}"
        snapshot_id = f"resume-snap-{uuid.uuid4()}"
        # First run — all six ACCEPTED
        _run_all_valid_with_db(self._conn, run_id=run_id, snapshot_id=snapshot_id)
        # Second run — same run_id and snapshot_id
        result, packet = _run_all_valid_with_db(
            self._conn, run_id=run_id, snapshot_id=snapshot_id
        )
        statuses = {r.agent_id: r.status for r in result.role_results}
        for aid in _ALL_AGENT_IDS:
            self.assertEqual(
                statuses[aid], RoleRunnerStatus.SKIPPED_RESUMED,
                f"Expected SKIPPED_RESUMED for {aid}, got {statuses[aid]}"
            )

    @_needs_db
    def test_skipped_resumed_does_not_duplicate_agent_result_rows(self):
        """
        uac_agent_results has a UNIQUE constraint (run_id, snapshot_id, agent_id).
        A second run (all SKIPPED_RESUMED) must not raise a duplicate-key error.
        """
        run_id      = f"dedup-run-{uuid.uuid4()}"
        snapshot_id = f"dedup-snap-{uuid.uuid4()}"
        _run_all_valid_with_db(self._conn, run_id=run_id, snapshot_id=snapshot_id)
        try:
            _run_all_valid_with_db(self._conn, run_id=run_id, snapshot_id=snapshot_id)
        except Exception as exc:
            self.fail(f"Second run raised unexpected error: {exc}")

    @_needs_db
    def test_only_failed_roles_rerun_on_resume(self):
        """
        When one role failed in run 1, run 2 should re-execute only that role
        (RUNNER_FAILED roles are not in resumability table → not SKIPPED_RESUMED).
        The other five ACCEPTED roles should be SKIPPED_RESUMED.
        """
        run_id      = f"partial-resume-{uuid.uuid4()}"
        snapshot_id = f"partial-resume-snap-{uuid.uuid4()}"
        bad_agent   = "uac-data-slate-integrity-v1"
        bad_role    = "DATA_SLATE_INTEGRITY"

        # Run 1: DATA_SLATE_INTEGRITY fails, others succeed
        presets_run1 = dict(_VALID_PRESETS)
        presets_run1[bad_agent] = RuntimeError("first run crash")
        packet   = build_test_packet(run_id=run_id, snapshot_id=snapshot_id)
        registry = build_b1_registry()
        runner1  = MockRoleRunner(presets=presets_run1)
        runners1 = {aid: runner1 for aid in _ALL_AGENT_IDS}
        run_orchestrator(packet, registry, runners1, db_conn=self._conn)

        # Run 2: all presets valid — failed role re-executes, others skip
        runner2  = MockRoleRunner(presets=dict(_VALID_PRESETS))
        runners2 = {aid: runner2 for aid in _ALL_AGENT_IDS}
        result2  = run_orchestrator(packet, registry, runners2, db_conn=self._conn)

        statuses = {r.agent_id: r.status for r in result2.role_results}
        # The previously failed role should be re-executed → ACCEPTED
        self.assertEqual(statuses[bad_agent], RoleRunnerStatus.ACCEPTED,
            f"Re-executed role should be ACCEPTED on run 2, got {statuses[bad_agent]}")
        # The five successful roles should be skipped
        for aid in _ALL_AGENT_IDS:
            if aid != bad_agent:
                self.assertEqual(statuses[aid], RoleRunnerStatus.SKIPPED_RESUMED,
                    f"Expected SKIPPED_RESUMED for {aid}, got {statuses[aid]}")

    @_needs_db
    def test_resumability_idempotent_mark(self):
        """
        Calling mark_work_completed twice with the same (run_id, work_unit_id)
        must not raise (ON CONFLICT DO NOTHING is idempotent).
        """
        from gate_engine.universal_agent.audit_store import mark_work_completed
        run_id       = f"idem-mark-{uuid.uuid4()}"
        work_unit_id = f"snap-xxx:{uuid.uuid4()}"
        mark_work_completed(
            self._conn, run_id=run_id, work_unit_id=work_unit_id, outcome="ACCEPTED"
        )
        try:
            mark_work_completed(
                self._conn, run_id=run_id, work_unit_id=work_unit_id,
                outcome="ACCEPTED"
            )
        except Exception as exc:
            self.fail(f"Second mark_work_completed raised: {exc}")


if __name__ == "__main__":
    unittest.main()
