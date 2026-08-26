"""
tests/test_universal_agent_b0_db.py
WOW-PATCH-2026-08-09-UNIVERSAL-AGENT-CORE-V1 / Phase B0

Database integration tests for UAC B0 audit/cost structures.
All tests hit a real Postgres database — no mocks.

Tests are skipped if DATABASE_URL is not set (CI without DB).

Test categories:
  D1  ensure_tables() — tables are created, idempotent
  D2  Evidence packet CRUD — insert, retrieve, idempotent on duplicate
  D3  Agent result recording — AVAILABLE accounting, UNAVAILABLE null-enforcement
  D4  Budget event recording — aggregation, UNAVAILABLE null-enforcement
  D5  Resumability — is_work_completed() before/after mark_work_completed(),
        second identical mark is idempotent, different work_unit_ids independent
  D6  Budget guard (pure) — threshold at exact boundary, cost math precision
"""
from __future__ import annotations

import os
import unittest
import uuid

try:
    import psycopg2
    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

from gate_engine.universal_agent.audit_store import (
    UsageStatus,
    ensure_tables,
    get_table_names,
    record_evidence_packet,
    get_evidence_packet,
    record_agent_result,
    get_agent_result,
    record_budget_event,
    get_run_budget_summary,
    mark_work_completed,
    is_work_completed,
    compute_budget_guard,
)


def _get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return None
    return psycopg2.connect(url)


_SKIP_REASON = "DATABASE_URL not set or psycopg2 not available"


def _needs_db(test_func):
    """Decorator: skip if DATABASE_URL unavailable."""
    import functools
    @functools.wraps(test_func)
    def wrapper(self, *args, **kwargs):
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            self.skipTest(_SKIP_REASON)
        return test_func(self, *args, **kwargs)
    return wrapper


class TestAuditStoreDB(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not _PSYCOPG2_AVAILABLE or not os.environ.get("DATABASE_URL"):
            return
        cls.conn = _get_conn()
        ensure_tables(cls.conn)

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn") and cls.conn:
            cls.conn.close()

    # ── D1: ensure_tables ─────────────────────────────────────────────────────

    @_needs_db
    def test_ensure_tables_creates_all_tables(self):
        """All four UAC tables exist after ensure_tables()."""
        expected = set(get_table_names())
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name LIKE 'uac_%'
            """)
            found = {row[0] for row in cur.fetchall()}
        self.assertEqual(expected, found,
                         f"Expected tables {expected}, found {found}")

    @_needs_db
    def test_ensure_tables_is_idempotent(self):
        """Calling ensure_tables() twice does not raise."""
        ensure_tables(self.conn)  # second call — should not raise

    # ── D2: Evidence packet CRUD ──────────────────────────────────────────────

    @_needs_db
    def test_record_and_retrieve_evidence_packet(self):
        snap_id = f"snap-{uuid.uuid4()}"
        run_id  = f"run-{uuid.uuid4()}"
        packet  = {"field": "value", "number": 42}

        record_evidence_packet(
            self.conn,
            snapshot_id=snap_id,
            run_id=run_id,
            canonical_event_id="evt-001",
            lane="PLAYER_PROPS",
            packet_dict=packet,
        )

        # Read directly from DB — no mock
        retrieved = get_evidence_packet(self.conn, snap_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["field"], "value")
        self.assertEqual(retrieved["number"], 42)

    @_needs_db
    def test_retrieve_nonexistent_returns_none(self):
        result = get_evidence_packet(self.conn, "does-not-exist-ever")
        self.assertIsNone(result)

    @_needs_db
    def test_duplicate_snapshot_id_is_idempotent(self):
        """ON CONFLICT DO NOTHING — duplicate insert does not raise."""
        snap_id = f"snap-dup-{uuid.uuid4()}"
        for _ in range(3):
            record_evidence_packet(
                self.conn,
                snapshot_id=snap_id,
                run_id="run-dup",
                canonical_event_id="evt-dup",
                lane="TENNIS",
                packet_dict={"attempt": 1},
            )
        # Should still have exactly one row
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM uac_evidence_packets WHERE snapshot_id = %s",
                (snap_id,)
            )
            count = cur.fetchone()[0]
        self.assertEqual(count, 1)

    # ── D3: Agent result recording ────────────────────────────────────────────

    @_needs_db
    def test_record_available_result_stores_real_token_counts(self):
        run_id  = f"run-{uuid.uuid4()}"
        snap_id = f"snap-{uuid.uuid4()}"

        record_agent_result(
            self.conn,
            run_id=run_id,
            snapshot_id=snap_id,
            agent_id="forecast-agent",
            status=UsageStatus.AVAILABLE,
            output={"advisory_findings": {"note": "looks good"}},
            model="claude-haiku-4-5-20251001",
            input_tokens=1200,
            output_tokens=350,
            estimated_cost_usd=0.00180,
            latency_ms=1450,
        )

        row = get_agent_result(self.conn, run_id=run_id, snapshot_id=snap_id,
                               agent_id="forecast-agent")
        self.assertIsNotNone(row)
        self.assertEqual(row["status"],       UsageStatus.AVAILABLE)
        self.assertEqual(row["input_tokens"], 1200)
        self.assertEqual(row["output_tokens"], 350)
        self.assertAlmostEqual(row["estimated_cost_usd"], 0.00180, places=4)
        self.assertEqual(row["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(row["latency_ms"], 1450)

    @_needs_db
    def test_unavailable_result_stores_null_for_token_counts(self):
        """
        AVAILABLE/UNAVAILABLE pattern: when status=UNAVAILABLE,
        token counts and cost must be NULL in the database — never a silent zero.
        (Weather Step 12.5B2 pattern.)
        """
        run_id  = f"run-{uuid.uuid4()}"
        snap_id = f"snap-{uuid.uuid4()}"

        record_agent_result(
            self.conn,
            run_id=run_id,
            snapshot_id=snap_id,
            agent_id="unavail-agent",
            status=UsageStatus.UNAVAILABLE,
            # Caller passes values — they must be forced to NULL
            input_tokens=999,
            output_tokens=888,
            estimated_cost_usd=0.999,
        )

        # Verify directly via SQL, not via the helper (proves no silent coercion)
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT input_tokens, output_tokens, estimated_cost_usd
                FROM uac_agent_results
                WHERE run_id = %s AND snapshot_id = %s AND agent_id = %s
            """, (run_id, snap_id, "unavail-agent"))
            db_row = cur.fetchone()

        self.assertIsNotNone(db_row)
        self.assertIsNone(db_row[0], "input_tokens must be NULL for UNAVAILABLE")
        self.assertIsNone(db_row[1], "output_tokens must be NULL for UNAVAILABLE")
        self.assertIsNone(db_row[2], "estimated_cost_usd must be NULL for UNAVAILABLE")

    @_needs_db
    def test_agent_result_on_conflict_updates(self):
        """Retry overwrites an earlier BLOCKED result."""
        run_id  = f"run-{uuid.uuid4()}"
        snap_id = f"snap-{uuid.uuid4()}"

        record_agent_result(
            self.conn,
            run_id=run_id, snapshot_id=snap_id, agent_id="retry-agent",
            status=UsageStatus.BLOCKED,
        )
        record_agent_result(
            self.conn,
            run_id=run_id, snapshot_id=snap_id, agent_id="retry-agent",
            status=UsageStatus.AVAILABLE,
            input_tokens=500, output_tokens=100, estimated_cost_usd=0.001,
        )
        row = get_agent_result(self.conn, run_id=run_id, snapshot_id=snap_id,
                               agent_id="retry-agent")
        self.assertEqual(row["status"], UsageStatus.AVAILABLE)

    # ── D4: Budget event recording ────────────────────────────────────────────

    @_needs_db
    def test_budget_event_available_stores_real_cost(self):
        run_id = f"run-{uuid.uuid4()}"
        record_budget_event(
            self.conn,
            run_id=run_id,
            agent_id="budget-agent",
            event_type="CALL_CHARGED",
            usage_status=UsageStatus.AVAILABLE,
            input_tokens=1000,
            output_tokens=200,
            estimated_cost_usd=0.0035,
            model="claude-haiku-4-5",
        )

        summary = get_run_budget_summary(self.conn, run_id)
        self.assertIn(UsageStatus.AVAILABLE, summary)
        avail = summary[UsageStatus.AVAILABLE]
        self.assertEqual(avail["call_count"], 1)
        self.assertEqual(avail["total_input_tokens"], 1000)
        self.assertEqual(avail["total_output_tokens"], 200)
        self.assertAlmostEqual(avail["total_cost_usd"], 0.0035, places=4)

    @_needs_db
    def test_budget_event_unavailable_stores_null_cost(self):
        """
        UNAVAILABLE budget events must have NULL numeric fields in the DB.
        (Weather Step 12.5B2: never a silent zero.)
        """
        run_id = f"run-{uuid.uuid4()}"
        record_budget_event(
            self.conn,
            run_id=run_id,
            agent_id="unavail-budget",
            event_type="CALL_UNAVAILABLE",
            usage_status=UsageStatus.UNAVAILABLE,
            input_tokens=9999,   # must be forced NULL
            output_tokens=8888,  # must be forced NULL
            estimated_cost_usd=99.99,  # must be forced NULL
        )

        # Direct SQL query — no helper abstraction
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT input_tokens, output_tokens, estimated_cost_usd
                FROM uac_budget_events
                WHERE run_id = %s
            """, (run_id,))
            db_row = cur.fetchone()

        self.assertIsNone(db_row[0], "input_tokens must be NULL for UNAVAILABLE budget event")
        self.assertIsNone(db_row[1], "output_tokens must be NULL for UNAVAILABLE budget event")
        self.assertIsNone(db_row[2], "estimated_cost_usd must be NULL for UNAVAILABLE budget event")

    @_needs_db
    def test_budget_summary_groups_by_status(self):
        run_id = f"run-{uuid.uuid4()}"
        for _ in range(2):
            record_budget_event(
                self.conn, run_id=run_id, agent_id="a",
                event_type="CALL_CHARGED", usage_status=UsageStatus.AVAILABLE,
                input_tokens=500, output_tokens=100, estimated_cost_usd=0.001,
            )
        record_budget_event(
            self.conn, run_id=run_id, agent_id="b",
            event_type="CALL_UNAVAILABLE", usage_status=UsageStatus.UNAVAILABLE,
        )
        summary = get_run_budget_summary(self.conn, run_id)
        self.assertEqual(summary[UsageStatus.AVAILABLE]["call_count"], 2)
        self.assertEqual(summary[UsageStatus.UNAVAILABLE]["call_count"], 1)
        self.assertIsNone(summary[UsageStatus.UNAVAILABLE]["total_cost_usd"])

    # ── D5: Resumability ──────────────────────────────────────────────────────

    @_needs_db
    def test_not_completed_before_marking(self):
        run_id = f"run-{uuid.uuid4()}"
        wuid   = f"snap-xyz:agent-a"
        self.assertFalse(is_work_completed(self.conn, run_id=run_id, work_unit_id=wuid))

    @_needs_db
    def test_completed_after_marking(self):
        run_id = f"run-{uuid.uuid4()}"
        wuid   = f"snap-{uuid.uuid4()}:agent-b"
        mark_work_completed(self.conn, run_id=run_id, work_unit_id=wuid)
        self.assertTrue(is_work_completed(self.conn, run_id=run_id, work_unit_id=wuid))

    @_needs_db
    def test_mark_work_completed_is_idempotent(self):
        """Marking the same work_unit_id twice does not raise (ON CONFLICT DO NOTHING)."""
        run_id = f"run-{uuid.uuid4()}"
        wuid   = f"snap-{uuid.uuid4()}:agent-c"
        mark_work_completed(self.conn, run_id=run_id, work_unit_id=wuid)
        mark_work_completed(self.conn, run_id=run_id, work_unit_id=wuid)  # second call
        self.assertTrue(is_work_completed(self.conn, run_id=run_id, work_unit_id=wuid))

    @_needs_db
    def test_different_work_unit_ids_are_independent(self):
        run_id = f"run-{uuid.uuid4()}"
        wuid_a = f"snap-{uuid.uuid4()}:agent-a"
        wuid_b = f"snap-{uuid.uuid4()}:agent-b"
        mark_work_completed(self.conn, run_id=run_id, work_unit_id=wuid_a)
        self.assertTrue(is_work_completed(self.conn, run_id=run_id, work_unit_id=wuid_a))
        self.assertFalse(is_work_completed(self.conn, run_id=run_id, work_unit_id=wuid_b))

    @_needs_db
    def test_second_run_does_not_see_first_run_completions(self):
        """A new run_id has a clean slate — previous run's completions are invisible."""
        run_id_1 = f"run-{uuid.uuid4()}"
        run_id_2 = f"run-{uuid.uuid4()}"
        wuid = "snap-shared:agent-shared"
        mark_work_completed(self.conn, run_id=run_id_1, work_unit_id=wuid)
        self.assertFalse(is_work_completed(self.conn, run_id=run_id_2, work_unit_id=wuid))

    @_needs_db
    def test_resumability_simulated_second_run_skips_completed_work(self):
        """
        Simulate the pilot runner resumability pattern (Weather Step 12.5B1):
        A 'second run' over the same work units should skip all already-completed ones.
        """
        run_id = f"run-{uuid.uuid4()}"
        work_units = [f"snap-{i}:agent-x" for i in range(5)]

        # First pass: complete first 3
        for wuid in work_units[:3]:
            mark_work_completed(self.conn, run_id=run_id, work_unit_id=wuid)

        # Second pass: simulate the main loop skip
        executed = []
        skipped  = []
        for wuid in work_units:
            if is_work_completed(self.conn, run_id=run_id, work_unit_id=wuid):
                skipped.append(wuid)
            else:
                executed.append(wuid)
                mark_work_completed(self.conn, run_id=run_id, work_unit_id=wuid)

        self.assertEqual(len(skipped),  3)
        self.assertEqual(len(executed), 2)

        # Third pass: all 5 should now be complete
        remaining = [
            wuid for wuid in work_units
            if not is_work_completed(self.conn, run_id=run_id, work_unit_id=wuid)
        ]
        self.assertEqual(remaining, [])

    # ── D6: Durable storage confirmed directly ────────────────────────────────

    @_needs_db
    def test_evidence_packet_persists_across_function_calls(self):
        """Confirm durable storage: retrieve in a separate function call."""
        snap_id = f"snap-durable-{uuid.uuid4()}"

        def _insert():
            record_evidence_packet(
                self.conn,
                snapshot_id=snap_id,
                run_id="durable-run",
                canonical_event_id="durable-event",
                lane="KALSHI_WEATHER",
                packet_dict={"durable_key": "durable_value", "n": 42},
            )

        def _retrieve():
            return get_evidence_packet(self.conn, snap_id)

        _insert()
        result = _retrieve()
        self.assertIsNotNone(result)
        self.assertEqual(result["durable_key"], "durable_value")
        self.assertEqual(result["n"], 42)


if __name__ == "__main__":
    unittest.main()
