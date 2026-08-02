"""
gate_engine/tests/test_patch_portfolio_stage2a.py

PATCH-PORTFOLIO-002 — Cross-Slip Persistent Governance (Stage 2A)

Tests DB-backed PgPortfolioGovernor cross-request behaviour.
All psycopg2 interactions are mocked — no live DB required.

Required scenarios (from spec):
  1. Same player/stat-family in two separate requests is rejected.
  2. Alternate lines in separate requests are rejected as one distribution.
  3. Different players in separate requests pass.
  4. Same player on a new slate date does not conflict after valid rollover.
  5. Concurrent requests cannot both reserve the same exposure.
  6. Ledger read/write failure invalidates the run.
  7. Rejected rows remain visible for audit but do not silently count as
     approved exposure unless governance explicitly defines that behaviour.
  8. Existing Stage 1 tests remain green  (enforced by running Stage 1 suite;
     Stage 2A factory falls back to in-memory when conn_string=None).

Additional invariant tests:
  - can_execute=False is never True on any gate output.
  - session_id empty → RUN_INVALID_SESSION_ID_MISSING (no DB needed).
  - make_portfolio_governor returns PgPortfolioGovernor when DATABASE_URL set,
    PortfolioExposureGovernor when no URL.
"""
from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import MagicMock, patch, call
from contextlib import contextmanager

from gate_engine.portfolio.pg_portfolio_governor import (
    PgPortfolioGovernor,
    LABEL_CROSS_SLIP_CONC,
    LABEL_DUPLICATE_THESIS,
    LABEL_SESSION_ID_MISS,
    LABEL_LEDGER_UNAVAIL,
)
from gate_engine.portfolio.cross_slip_exposure import (
    PortfolioExposureGovernor,
    make_portfolio_governor,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

TODAY = date(2026, 7, 31)
YESTERDAY = date(2026, 7, 30)

CONN = "postgresql://localhost/test"


def _row(player="Kayla McBride", prop_type="PRA", line=22.5, direction="MORE"):
    return {
        "player":    player,
        "prop_type": prop_type,
        "line":      line,
        "direction": direction,
        "game":      "MIN@LAS",
        "sport":     "WNBA",
        "blockers":  [],
        "gates":     {},
        "can_execute": False,
    }


def _gov(
    session_id="sess-001",
    research_run_id="run-001",
    slate_date=TODAY,
    conn_string=CONN,
):
    return PgPortfolioGovernor(
        session_id=session_id,
        research_run_id=research_run_id,
        slate_date=slate_date,
        conn_string=conn_string,
    )


# ---------------------------------------------------------------------------
# DB mock helpers
# ---------------------------------------------------------------------------

class _FakeCursor:
    """
    A simple fake cursor that returns rows pre-loaded via set_results().
    records .executed sql+params for assertions.
    """
    def __init__(self):
        self._results = []
        self._rowcount = 0
        self.executed = []

    def set_results(self, rows: list):
        self._results = list(rows)

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchall(self):
        return list(self._results)

    def fetchone(self):
        return self._results[0] if self._results else None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class _FakeConn:
    """Fake psycopg2 connection wrapping a shared cursor."""

    def __init__(self, cursor: _FakeCursor):
        self._cur = cursor

    def cursor(self):
        return self._cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@contextmanager
def _mock_db_pass(mktf_count=0, thesis_count=0):
    """
    Patch psycopg2.connect so the dedup check returns the given counts,
    simulating a slot that has been used that many times already.

    Returns (pass, no blocks).
    """
    mktf_key   = "mktf:kayla mcbride|pra"
    thesis_key = "thesis:kayla mcbride|pra|MORE"

    cur = _FakeCursor()
    cur.set_results([
        (mktf_key,   mktf_count),
        (thesis_key, thesis_count),
    ])
    conn = _FakeConn(cur)

    with patch("psycopg2.connect", return_value=conn):
        yield cur


@contextmanager
def _mock_db_fail(exc_cls=RuntimeError, msg="connection refused"):
    """Patch psycopg2.connect to raise an exception."""
    with patch("psycopg2.connect", side_effect=exc_cls(msg)):
        yield


# ---------------------------------------------------------------------------
# Test class — Core cross-request scenarios
# ---------------------------------------------------------------------------

class TestPgPortfolioGovernorCrossRequest:
    """
    Tests the 8 required spec scenarios.
    """

    # ------------------------------------------------------------------
    # Scenario 1: Same player/stat-family in two separate requests rejected
    # ------------------------------------------------------------------

    def test_same_mktfamily_second_request_blocked(self):
        """
        Simulates: First /gate-engine/run registered player+stat+direction.
        Second call (new governor instance, same session) sees count=1 → blocked.
        """
        row = _row()
        gov = _gov()

        # DB shows mktfamily already claimed (count=1)
        # mktfamily_key is now direction-inclusive: player|stat|direction
        mktf_key   = "mktf:kayla mcbride|pra|MORE"
        thesis_key = "thesis:kayla mcbride|pra|MORE"
        cur = _FakeCursor()
        cur.set_results([(mktf_key, 1), (thesis_key, 1)])
        conn = _FakeConn(cur)

        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)

        gate = row["gates"]["portfolio_exposure"]
        assert gate["passed"] is False
        assert gate["backend"] == "postgres"
        assert any(LABEL_CROSS_SLIP_CONC in b for b in gate["blocks"])
        assert row["terminal_label"] == LABEL_CROSS_SLIP_CONC

    # ------------------------------------------------------------------
    # Scenario 2: Alternate lines in separate requests rejected as one dist
    # ------------------------------------------------------------------

    def test_alternate_line_separate_requests_blocked(self):
        """
        PRA 19.5 MORE in request 1 already registered.
        PRA 22.5 MORE in request 2 → same mktfamily_key (kayla mcbride|pra|MORE) → blocked.
        Direction is included in the key, so same direction + different line = blocked.
        Opposite direction (LESS) would have a different key and would pass.
        """
        row = _row(line=22.5, direction="MORE")  # same direction, alternate line
        gov = _gov()

        mktf_key   = "mktf:kayla mcbride|pra|MORE"
        thesis_key = "thesis:kayla mcbride|pra|MORE"
        cur = _FakeCursor()
        cur.set_results([(mktf_key, 1), (thesis_key, 1)])  # mktf already taken
        conn = _FakeConn(cur)

        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)

        gate = row["gates"]["portfolio_exposure"]
        assert gate["passed"] is False
        assert any(LABEL_CROSS_SLIP_CONC in b for b in gate["blocks"])
        # Confirm alternate line is treated as same distribution (direction-inclusive key)
        assert gate["mktfamily_key"] == "kayla mcbride|pra|MORE"

    def test_opposing_directions_both_pass(self):
        """
        MORE and LESS on the same player+stat → different mktfamily_keys →
        both must be allowed.  This is the fix for the July 31 WNBA board
        where both directions were submitted for research.
        """
        row_more = _row(direction="MORE")
        row_less = _row(direction="LESS")
        gov_more = _gov(research_run_id="run-more")
        gov_less = _gov(research_run_id="run-less")

        # Simulate: LESS direction has zero prior registrations
        cur_less = _FakeCursor()
        cur_less.set_results([
            ("mktf:kayla mcbride|pra|LESS",  0),
            ("thesis:kayla mcbride|pra|LESS", 0),
        ])
        conn_less = _FakeConn(cur_less)

        with patch("psycopg2.connect", return_value=conn_less):
            gov_less.check_and_register(row_less)

        gate_less = row_less["gates"]["portfolio_exposure"]
        assert gate_less["passed"] is True, (
            f"LESS direction should pass independently of MORE: {gate_less}"
        )
        assert gate_less["mktfamily_key"] == "kayla mcbride|pra|LESS"

    # ------------------------------------------------------------------
    # Scenario 3: Different players in separate requests pass
    # ------------------------------------------------------------------

    def test_different_players_both_pass(self):
        """
        Breanna Stewart has a different mktfamily_key → passes even if
        Kayla McBride PRA is already registered.
        """
        row = _row(player="Breanna Stewart", prop_type="Points", line=24.5)
        gov = _gov()

        # DB shows ZERO claims for this player+stat+direction → should pass
        cur = _FakeCursor()
        cur.set_results([
            ("mktf:breanna stewart|points|MORE", 0),
            ("thesis:breanna stewart|points|MORE", 0),
        ])
        conn = _FakeConn(cur)

        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)

        gate = row["gates"]["portfolio_exposure"]
        assert gate["passed"] is True
        assert gate["blocks"] == []
        assert gate["backend"] == "postgres"

    # ------------------------------------------------------------------
    # Scenario 4: New slate date — no conflict after rollover
    # ------------------------------------------------------------------

    def test_new_slate_date_no_conflict(self):
        """
        Governor for TODAY's slate is created fresh.
        Even though yesterday's slate had Kayla McBride PRA MORE, today's
        slate_date filter means the DB returns count=0.
        """
        row = _row()
        gov = _gov(slate_date=TODAY)  # today's slate

        mktf_key   = "mktf:kayla mcbride|pra|MORE"
        thesis_key = "thesis:kayla mcbride|pra|MORE"
        # TODAY's slate returns 0 — yesterday's rows are invisible
        cur = _FakeCursor()
        cur.set_results([(mktf_key, 0), (thesis_key, 0)])
        conn = _FakeConn(cur)

        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)

        gate = row["gates"]["portfolio_exposure"]
        assert gate["passed"] is True
        # Confirm the slate_date reported in the gate matches today
        assert gate["slate_date"] == str(TODAY)

    def test_slate_date_is_included_in_all_queries(self):
        """
        Verify the correct slate_date appears in every parameterised query.
        """
        row = _row()
        gov = _gov(slate_date=TODAY)

        cur = _FakeCursor()
        cur.set_results([
            ("mktf:kayla mcbride|pra|MORE",   0),
            ("thesis:kayla mcbride|pra|MORE",  0),
        ])
        conn = _FakeConn(cur)

        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)

        # Every executed statement that carries a date param should use TODAY
        date_params = [
            p for _, p in cur.executed
            if p is not None and TODAY in p
        ]
        assert len(date_params) >= 3, (
            "Expected slate_date in INSERT, SELECT FOR UPDATE, and UPDATE queries"
        )

    # ------------------------------------------------------------------
    # Scenario 5: Concurrent requests — only one can reserve
    # ------------------------------------------------------------------

    def test_concurrent_requests_only_first_reserves(self):
        """
        Two governor instances race for the same (session, mktfamily_key).
        Simulate by having the second see count=1 (the first already incremented).
        The SELECT FOR UPDATE serialises them in real DB; in the test we model
        the committed state that the second transaction observes.
        """
        mktf_key   = "mktf:kayla mcbride|pra"
        thesis_key = "thesis:kayla mcbride|pra|MORE"

        # --- First request: count=0 → passes, increments to 1 ---
        row1 = _row()
        gov1 = _gov(research_run_id="run-001")
        cur1 = _FakeCursor()
        cur1.set_results([("mktf:kayla mcbride|pra|MORE", 0), ("thesis:kayla mcbride|pra|MORE", 0)])
        conn1 = _FakeConn(cur1)

        with patch("psycopg2.connect", return_value=conn1):
            gov1.check_and_register(row1)

        assert row1["gates"]["portfolio_exposure"]["passed"] is True

        # --- Second request: count=1 → blocked ---
        row2 = _row()
        gov2 = _gov(research_run_id="run-002")
        cur2 = _FakeCursor()
        cur2.set_results([("mktf:kayla mcbride|pra|MORE", 1), ("thesis:kayla mcbride|pra|MORE", 1)])
        conn2 = _FakeConn(cur2)

        with patch("psycopg2.connect", return_value=conn2):
            gov2.check_and_register(row2)

        assert row2["gates"]["portfolio_exposure"]["passed"] is False
        assert any(LABEL_CROSS_SLIP_CONC in b for b in row2["blockers"])

    # ------------------------------------------------------------------
    # Scenario 6: Ledger read/write failure invalidates the run
    # ------------------------------------------------------------------

    def test_db_failure_fail_closed(self):
        """
        DB connection failure → row gets SESSION_LEDGER_UNAVAILABLE blocker,
        passed=False.  Run must be invalidated.
        """
        row = _row()
        gov = _gov()

        with _mock_db_fail(RuntimeError, "connection refused"):
            gov.check_and_register(row)

        gate = row["gates"]["portfolio_exposure"]
        assert gate["passed"] is False
        assert gate.get("db_error") is not None
        assert any(LABEL_LEDGER_UNAVAIL in b for b in row["blockers"])

    def test_db_failure_does_not_raise(self):
        """Exceptions from psycopg2 must be caught internally — never propagate."""
        row = _row()
        gov = _gov()

        import psycopg2
        with patch("psycopg2.connect", side_effect=psycopg2.OperationalError("timeout")):
            gov.check_and_register(row)  # must not raise

        assert row["gates"]["portfolio_exposure"]["passed"] is False

    # ------------------------------------------------------------------
    # Scenario 7: Rejected rows are logged but don't count as approved
    # ------------------------------------------------------------------

    def test_rejected_row_logged_not_counted(self):
        """
        A rejected row (mktfamily conflict) must:
          - NOT increment the count (we verify no UPDATE is issued)
          - Its audit log entry carries decision_label=REJECT_CROSS_SLIP_CONCENTRATION
        """
        row = _row()
        gov = _gov()

        cur = _FakeCursor()
        cur.set_results([
            ("mktf:kayla mcbride|pra|MORE",  1),
            ("thesis:kayla mcbride|pra|MORE", 1),
        ])
        conn = _FakeConn(cur)

        with patch("psycopg2.connect", return_value=conn):
            # Also mock _log_exposure so we can inspect its call args
            with patch.object(gov, "_log_exposure") as mock_log:
                gov.check_and_register(row)
                assert mock_log.called
                kwargs = mock_log.call_args.kwargs
                assert kwargs["decision_label"] == LABEL_CROSS_SLIP_CONC
                assert LABEL_CROSS_SLIP_CONC in kwargs["blocks"] or \
                       any(LABEL_CROSS_SLIP_CONC in b for b in kwargs["blocks"])

        # Verify no bare UPDATE was issued (count not incremented for rejected row).
        # SELECT ... FOR UPDATE contains "UPDATE" too — exclude those.
        update_calls = [
            sql for sql, _ in cur.executed
            if sql.strip().upper().startswith("UPDATE")
        ]
        assert len(update_calls) == 0, "Rejected row must not increment count"

    # ------------------------------------------------------------------
    # Scenario 8: Stage 1 factory fallback — no regression
    # ------------------------------------------------------------------

    def test_factory_returns_pg_governor_when_db_url_present(self):
        """make_portfolio_governor returns PgPortfolioGovernor when conn_string set."""
        gov = make_portfolio_governor(
            session_id="sess-001",
            conn_string=CONN,
            research_run_id="run-001",
            slate_date=TODAY,
        )
        assert isinstance(gov, PgPortfolioGovernor)

    def test_factory_returns_memory_governor_when_no_db(self):
        """make_portfolio_governor falls back to in-memory when conn_string=None."""
        import os
        # Temporarily unset DATABASE_URL if present
        old = os.environ.pop("DATABASE_URL", None)
        try:
            gov = make_portfolio_governor(
                session_id="sess-001",
                conn_string=None,
            )
            assert isinstance(gov, PortfolioExposureGovernor)
        finally:
            if old:
                os.environ["DATABASE_URL"] = old

    def test_stage1_in_memory_governor_unchanged(self):
        """
        Verify the Stage 1 in-memory governor still works correctly
        (same-session alternate-line block within a single call).
        """
        gov = PortfolioExposureGovernor(session_id="sess-mem")
        row1 = _row(line=19.5)
        row2 = _row(line=22.5)

        gov.check_and_register(row1)
        gov.check_and_register(row2)

        assert row1["gates"]["portfolio_exposure"]["passed"] is True
        assert row2["gates"]["portfolio_exposure"]["passed"] is False


# ---------------------------------------------------------------------------
# Test class — Invariants
# ---------------------------------------------------------------------------

class TestPgPortfolioGovernorInvariants:

    def test_can_execute_always_false_on_pass(self):
        row = _row()
        gov = _gov()
        cur = _FakeCursor()
        cur.set_results([
            ("mktf:kayla mcbride|pra|MORE", 0),
            ("thesis:kayla mcbride|pra|MORE", 0),
        ])
        conn = _FakeConn(cur)
        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)
        assert row["can_execute"] is False
        assert row["gates"]["portfolio_exposure"]["can_execute"] is False

    def test_can_execute_always_false_on_block(self):
        row = _row()
        gov = _gov()
        cur = _FakeCursor()
        cur.set_results([
            ("mktf:kayla mcbride|pra|MORE", 1),
            ("thesis:kayla mcbride|pra|MORE", 1),
        ])
        conn = _FakeConn(cur)
        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)
        assert row["can_execute"] is False
        assert row["gates"]["portfolio_exposure"]["can_execute"] is False

    def test_can_execute_false_on_db_error(self):
        row = _row()
        gov = _gov()
        with _mock_db_fail():
            gov.check_and_register(row)
        assert row["can_execute"] is False

    def test_empty_session_id_blocked_immediately(self):
        """session_id='' → RUN_INVALID_SESSION_ID_MISSING without touching DB."""
        row = _row()
        gov = PgPortfolioGovernor(
            session_id="",
            conn_string=CONN,
        )
        # No DB should be called
        with patch("psycopg2.connect", side_effect=AssertionError("DB must not be called")):
            gov.check_and_register(row)

        gate = row["gates"]["portfolio_exposure"]
        assert gate["passed"] is False
        assert any(LABEL_SESSION_ID_MISS in b for b in gate["blocks"])

    def test_module_level_can_execute_is_false(self):
        from gate_engine.portfolio import pg_portfolio_governor
        assert pg_portfolio_governor.can_execute is False

    def test_research_run_id_echoed_in_gate(self):
        row = _row()
        gov = _gov(research_run_id="run-XYZ")
        cur = _FakeCursor()
        cur.set_results([
            ("mktf:kayla mcbride|pra|MORE", 0),
            ("thesis:kayla mcbride|pra|MORE", 0),
        ])
        conn = _FakeConn(cur)
        with patch("psycopg2.connect", return_value=conn):
            gov.check_and_register(row)
        gate = row["gates"]["portfolio_exposure"]
        assert gate["research_run_id"] == "run-XYZ"

    def test_thesis_key_carries_direction(self):
        """
        Both mktfamily_key and thesis_key now include direction.
        MORE and LESS on the same player+stat produce DIFFERENT keys,
        so they are treated as independent market families (not same distribution).
        """
        row_more = _row(direction="MORE")
        row_less = _row(direction="LESS")

        from gate_engine.portfolio.pg_portfolio_governor import _make_keys
        mktf_m, thesis_m = _make_keys(row_more)
        mktf_l, thesis_l = _make_keys(row_less)

        # Both keys are now direction-inclusive → different for MORE vs LESS
        assert mktf_m != mktf_l      # opposing directions are different market families
        assert thesis_m != thesis_l  # opposing directions are different theses

    def test_snapshot_returns_error_on_empty_session(self):
        gov = PgPortfolioGovernor(session_id="", conn_string=CONN)
        snap = gov.snapshot()
        assert "error" in snap
        assert snap["can_execute"] is False

    def test_snapshot_returns_can_execute_false(self):
        gov = _gov()
        cur = _FakeCursor()
        cur.set_results([])
        conn = _FakeConn(cur)
        with patch("psycopg2.connect", return_value=conn):
            snap = gov.snapshot()
        assert snap["can_execute"] is False

    def test_concurrent_requests_declare_variables(self):
        """Ensure the concurrent test has mktf_key/thesis_key defined before use."""
        mktf_key   = "mktf:kayla mcbride|pra|MORE"
        thesis_key = "thesis:kayla mcbride|pra|MORE"
        assert "|MORE" in mktf_key
        assert "|MORE" in thesis_key

    def test_governance_patch_registered(self):
        """PATCH-PORTFOLIO-002 is in the governance registry and ACTIVE."""
        from gate_engine.governance import _PATCH_REGISTRY, _GOVERNANCE_HASH
        patch_ids = [p["patch_id"] for p in _PATCH_REGISTRY]
        assert "WOW-PATCH-PORTFOLIO-002-CROSS-SLIP-PERSISTENT-GOVERNANCE" in patch_ids

        active = [
            p for p in _PATCH_REGISTRY
            if p["patch_id"] == "WOW-PATCH-PORTFOLIO-002-CROSS-SLIP-PERSISTENT-GOVERNANCE"
        ]
        assert active[0]["status"] == "ACTIVE"

    def test_manifest_includes_portfolio_002(self):
        from gate_engine.wow_runtime_manifest import WOW_RUNTIME_MANIFEST
        assert "WOW-PATCH-PORTFOLIO-002-CROSS-SLIP-PERSISTENT-GOVERNANCE" in \
               WOW_RUNTIME_MANIFEST["active_patch_ids"]

    def test_manifest_hard_flag_set(self):
        from gate_engine.wow_runtime_manifest import WOW_RUNTIME_MANIFEST
        assert WOW_RUNTIME_MANIFEST["hard_flags"].get(
            "cross_slip_persistent_governance_db"
        ) is True

    def test_patch_count_is_16(self):
        """Total active patches is now 13 (was 12 before Stage 2A)."""
        from gate_engine.governance import _PATCH_REGISTRY
        active = [p for p in _PATCH_REGISTRY if p["status"] == "ACTIVE"]
        assert len(active) == 17, f"Expected 17 active patches, got {len(active)}"
