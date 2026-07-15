"""
test_pg_session_ledger.py — Unit and integration tests for PgSessionLedger.

Coverage:
  1. DB error → fail-closed (SESSION_LEDGER_UNAVAILABLE, passed=False, backend=postgres)
  2. Concurrent simultaneous first-write race → exactly one accepted, one blocked
"""
from __future__ import annotations

import threading
import unittest.mock as mock

import psycopg2
import pytest

from gate_engine.pg_session_ledger import PgSessionLedger


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _row(player: str = "Test Player", prop_type: str = "Points",
         game: str = "A vs B") -> dict:
    return {
        "row_id":    f"r-{player.lower().replace(' ', '-')}",
        "player":    player,
        "prop_type": prop_type,
        "game":      game,
        "blockers":  [],
        "gates":     {},
    }


# ===========================================================================
# 1. DB failure → fail closed
# ===========================================================================

class TestDbFailClosed:
    def test_connect_error_blocks_row(self):
        """OperationalError on connect → passed=False, SESSION_LEDGER_UNAVAILABLE."""
        with mock.patch(
            "psycopg2.connect",
            side_effect=psycopg2.OperationalError("connection refused (injected)"),
        ):
            ledger = PgSessionLedger(session_id="sid-fail-001")
            row = _row("Alice")
            ledger.check_and_register(row)

        gate = row["gates"]["exposure_gate"]
        assert gate["passed"]     is False,  f"Expected passed=False, got {gate}"
        assert gate["registered"] is False,  f"Expected registered=False, got {gate}"
        assert gate["backend"]    == "postgres"
        assert any("SESSION_LEDGER_UNAVAILABLE" in b for b in gate["blocks"]), (
            f"Expected SESSION_LEDGER_UNAVAILABLE in blocks, got {gate['blocks']}"
        )
        assert "db_error" in gate
        assert any("SESSION_LEDGER_UNAVAILABLE" in b for b in row["blockers"]), (
            f"Expected SESSION_LEDGER_UNAVAILABLE in row.blockers, got {row['blockers']}"
        )

    def test_any_exception_blocks_row(self):
        """Any exception (not just OperationalError) fails closed."""
        with mock.patch(
            "psycopg2.connect",
            side_effect=RuntimeError("unexpected failure"),
        ):
            ledger = PgSessionLedger(session_id="sid-fail-002")
            row = _row("Bob")
            ledger.check_and_register(row)

        gate = row["gates"]["exposure_gate"]
        assert gate["passed"] is False
        assert any("SESSION_LEDGER_UNAVAILABLE" in b for b in gate["blocks"])

    def test_db_error_does_not_fall_back_to_local_memory(self):
        """After a DB error, the row is blocked — NOT silently allowed through."""
        with mock.patch(
            "psycopg2.connect",
            side_effect=psycopg2.OperationalError("db down"),
        ):
            ledger = PgSessionLedger(session_id="sid-fail-003")
            row = _row("Charlie")
            ledger.check_and_register(row)

        # The row must NOT have passed=True (which would mean local memory fallback)
        gate = row["gates"]["exposure_gate"]
        assert gate["passed"] is not True, (
            "DB failure should block the row, not fall back to local-memory allow"
        )


# ===========================================================================
# 2. Concurrent first-write race
# ===========================================================================

class TestConcurrentFirstWrite:
    def test_simultaneous_same_player_exactly_one_accepted(self):
        """
        Two threads with the same session_id and same player fire simultaneously.
        Exactly one must be accepted (registered=True) and exactly one must be
        blocked (PLAYER_EXPOSURE:2x) — the SELECT FOR UPDATE prevents both
        from seeing count=0 at the same time.
        """
        import os
        import uuid

        # Unique session so this test never conflicts with other runs
        SID = f"concurrent-race-{uuid.uuid4().hex[:12]}"

        results: list[dict] = [None, None]  # type: ignore[assignment]
        barrier = threading.Barrier(2)       # synchronise both threads at start

        def _worker(idx: int) -> None:
            ledger = PgSessionLedger(
                session_id=SID,
                conn_string=os.environ.get("DATABASE_URL"),
            )
            row = _row("Concurrent Player")
            barrier.wait()                   # both threads start at the same instant
            ledger.check_and_register(row)
            results[idx] = row["gates"].get("exposure_gate", {})

        t0 = threading.Thread(target=_worker, args=(0,))
        t1 = threading.Thread(target=_worker, args=(1,))
        t0.start()
        t1.start()
        t0.join(timeout=10)
        t1.join(timeout=10)

        assert results[0] is not None, "Thread 0 did not complete"
        assert results[1] is not None, "Thread 1 did not complete"

        registered_count = sum(1 for r in results if r.get("registered") is True)
        blocked_count    = sum(1 for r in results if r.get("passed") is False)

        assert registered_count == 1, (
            f"Expected exactly 1 registered, got {registered_count}. "
            f"Results: {results}"
        )
        assert blocked_count == 1, (
            f"Expected exactly 1 blocked, got {blocked_count}. "
            f"Results: {results}"
        )

        # Both must report postgres backend
        for r in results:
            assert r.get("backend") == "postgres", f"Unexpected backend: {r}"

        # Cleanup
        try:
            import psycopg2 as _pg
            with _pg.connect(os.environ.get("DATABASE_URL", "")) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM wow_session_exposure WHERE session_id = %s",
                        (SID,),
                    )
                conn.commit()
        except Exception:
            pass  # best-effort cleanup

    def test_second_registration_of_same_player_blocked(self):
        """
        Sequential (not concurrent) duplicate registration via the same
        PgSessionLedger instance: first call passes, second is blocked.
        """
        import os
        import uuid

        SID = f"seq-dup-{uuid.uuid4().hex[:12]}"
        ledger = PgSessionLedger(
            session_id=SID,
            conn_string=os.environ.get("DATABASE_URL"),
        )

        row1 = _row("Duplicate Player")
        ledger.check_and_register(row1)
        g1 = row1["gates"]["exposure_gate"]

        row2 = _row("Duplicate Player")
        ledger.check_and_register(row2)
        g2 = row2["gates"]["exposure_gate"]

        assert g1["registered"] is True,  f"First call should register; got {g1}"
        assert g2["passed"]     is False, f"Second call should be blocked; got {g2}"
        assert any("PLAYER_EXPOSURE" in b for b in g2["blocks"]), (
            f"Expected PLAYER_EXPOSURE block, got {g2['blocks']}"
        )

        # Cleanup
        try:
            import psycopg2 as _pg
            with _pg.connect(os.environ.get("DATABASE_URL", "")) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM wow_session_exposure WHERE session_id = %s",
                        (SID,),
                    )
                conn.commit()
        except Exception:
            pass
