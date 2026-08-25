"""
gate_engine/tests/test_provenance_fault_injection.py

WOW-PATCH-2026-08-16-AUDIT gap (3a) — fault-injection proof for transactional
provenance downgrade.

The audit requires:
  "Force the provenance snapshot INSERT to fail and verify the committed
   calibration row is MODEL_QUALIFIED_HOLD."

These tests use unittest.mock to simulate a cursor whose SAVEPOINT RELEASE
(i.e. the snapshot INSERT) raises an exception, then verify:

  (a) The UPDATE to MODEL_QUALIFIED_HOLD is executed in the same transaction.
  (b) conn.commit() is called exactly once (single atomic commit).
  (c) No separate connection is opened for the downgrade (old best-effort pattern).
  (d) A FINAL_APPROVED entry with a source_snapshot_id that fails provenance
      lands in MODEL_QUALIFIED_HOLD.
  (e) A MONEY_QUALIFIED entry with a failed provenance also downgrades.
  (f) An entry with no source_snapshot_id skips the SAVEPOINT entirely and
      commits normally.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, call, patch


def _make_entry(final_label: str, snap_id: str | None = "test-snap-001") -> dict:
    return {
        "event_key":             "TEAM_A_vs_TEAM_B_2026-08-16",
        "run_id":                "run_closeout_test",
        "side":                  "over",
        "odds":                  -115,
        "line":                  5.5,
        "book":                  "draftkings",
        "source_type":           "odds_aggregator",
        "sport":                 "MLB",
        "market":                "strikeouts",
        "model_probability":     0.62,
        "calibrated_probability": 0.61,
        "stake":                 50.0,
        "final_label":           final_label,
        "source_snapshot_id":    snap_id,
        "model_timestamp":       "2026-08-16T12:00:00+00:00",
        "calibration_bucket":    "0.60-0.65",
    }


def _build_mock_conn_cursor(raises_on_release: bool = True):
    """
    Return (mock_conn, mock_cur) where:
    - mock_cur.execute() raises when called with RELEASE SAVEPOINT
    - or succeeds normally when raises_on_release=False
    """
    mock_cur  = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    if raises_on_release:
        _execute_calls: list[str] = []

        def _side_effect(sql, params=None):
            s = (sql or "").strip().upper()
            if "RELEASE SAVEPOINT" in s:
                raise Exception("Simulated snapshot INSERT failure")
            _execute_calls.append(s)

        mock_cur.execute.side_effect = _side_effect
        mock_cur._calls_log = _execute_calls

    return mock_conn, mock_cur


class TestProvenanceFaultInjection(unittest.TestCase):
    """
    Verifies that log_calibration_entry_pg() uses a single-transaction SAVEPOINT
    pattern that downgrades FINAL_APPROVED / MONEY_QUALIFIED atomically when the
    snapshot INSERT fails.
    """

    def _run_with_mock(self, entry: dict, raises: bool = True):
        """
        Patch _get_conn in llp_stage2_tables to return the mock connection,
        then call log_calibration_entry_pg() and return (mock_conn, mock_cur).
        """
        mock_conn, mock_cur = _build_mock_conn_cursor(raises_on_release=raises)

        with patch("gate_engine.llp_stage2_tables._get_conn", return_value=mock_conn):
            from gate_engine.llp_stage2_tables import log_calibration_entry_pg
            # Patch the import of source_provenance to avoid real I/O
            with patch("gate_engine.llp_stage2_tables._audit_calibration_entry_provenance",
                       return_value={"write_ok": False, "error": "mocked"}):
                try:
                    log_calibration_entry_pg(entry)
                except Exception:
                    pass  # function must not propagate — outer try/except in function catches
        return mock_conn, mock_cur

    def test_final_approved_downgraded_when_snapshot_fails(self):
        """
        When snapshot INSERT fails for FINAL_APPROVED entry:
        - An UPDATE to MODEL_QUALIFIED_HOLD must be executed.
        - conn.commit() must be called exactly once.
        """
        entry = _make_entry("FINAL_APPROVED", snap_id="snap-fa-001")
        mock_conn, mock_cur = _build_mock_conn_cursor(raises_on_release=True)

        with patch("gate_engine.llp_stage2_tables._get_conn", return_value=mock_conn):
            from gate_engine.llp_stage2_tables import log_calibration_entry_pg
            # Keep schema bootstrap commits out of this transaction-level
            # assertion.  The production call normally runs after startup
            # bootstrap; this test is proving the ledger/provenance atomic
            # transaction itself.
            with patch(
                "gate_engine.llp_stage2_tables.ensure_all_tables",
                return_value=None,
            ):
                try:
                    log_calibration_entry_pg(entry)
                except Exception:
                    pass

        calls = [str(c) for c in mock_cur.execute.call_args_list]
        all_sql = " ".join(calls).upper()

        # Must attempt SAVEPOINT
        self.assertTrue(
            any("SAVEPOINT" in c for c in calls),
            "No SAVEPOINT found; transactional pattern not used",
        )
        # Must attempt ROLLBACK TO SAVEPOINT after failure
        self.assertTrue(
            any("ROLLBACK" in c for c in calls),
            "No ROLLBACK TO SAVEPOINT after snapshot failure",
        )
        # Must execute UPDATE to downgrade the label
        self.assertTrue(
            any("UPDATE" in c and "MODEL_QUALIFIED_HOLD" in c for c in calls),
            f"No UPDATE to MODEL_QUALIFIED_HOLD found in execute calls: {calls}",
        )
        # Single commit only
        self.assertEqual(
            mock_conn.commit.call_count, 1,
            f"Expected exactly 1 commit, got {mock_conn.commit.call_count}",
        )

    def test_money_qualified_downgraded_when_snapshot_fails(self):
        """MONEY_QUALIFIED entries must also be downgraded on snapshot failure."""
        entry = _make_entry("MONEY_QUALIFIED", snap_id="snap-mq-001")
        mock_conn, mock_cur = _build_mock_conn_cursor(raises_on_release=True)

        with patch("gate_engine.llp_stage2_tables._get_conn", return_value=mock_conn):
            from gate_engine.llp_stage2_tables import log_calibration_entry_pg
            try:
                log_calibration_entry_pg(entry)
            except Exception:
                pass

        calls = [str(c) for c in mock_cur.execute.call_args_list]
        self.assertTrue(
            any("UPDATE" in c and "MODEL_QUALIFIED_HOLD" in c for c in calls),
            f"MONEY_QUALIFIED not downgraded; calls: {calls}",
        )

    def test_model_qualified_hold_not_downgraded(self):
        """
        A MODEL_QUALIFIED_HOLD entry must not trigger a downgrade UPDATE even if
        the snapshot INSERT fails — it is already at the ceiling.
        """
        entry = _make_entry("MODEL_QUALIFIED_HOLD", snap_id="snap-mqh-001")
        mock_conn, mock_cur = _build_mock_conn_cursor(raises_on_release=True)

        with patch("gate_engine.llp_stage2_tables._get_conn", return_value=mock_conn):
            from gate_engine.llp_stage2_tables import log_calibration_entry_pg
            try:
                log_calibration_entry_pg(entry)
            except Exception:
                pass

        calls = [str(c) for c in mock_cur.execute.call_args_list]
        # An UPDATE may or may not occur for the calibration record itself,
        # but if it occurs, it must NOT set the label to MODEL_QUALIFIED_HOLD
        # based on the downgrade guard (final_label not in money labels).
        # The key check: the downgrade UPDATE that specifies 'FINAL_APPROVED' or
        # 'MONEY_QUALIFIED' in its WHERE clause must NOT appear.
        downgrade_updates = [
            c for c in calls
            if "UPDATE" in c and "MODEL_QUALIFIED_HOLD" in c and "FINAL_APPROVED" in c
        ]
        self.assertEqual(
            len(downgrade_updates), 0,
            f"Unexpected downgrade UPDATE for MODEL_QUALIFIED_HOLD entry: {downgrade_updates}",
        )

    def test_no_snap_id_skips_savepoint(self):
        """
        An entry with no source_snapshot_id skips the SAVEPOINT block entirely —
        only the calibration INSERT and a single commit must occur.
        """
        entry = _make_entry("FINAL_APPROVED", snap_id=None)
        mock_conn, mock_cur = MagicMock(), MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("gate_engine.llp_stage2_tables._get_conn", return_value=mock_conn):
            from gate_engine.llp_stage2_tables import log_calibration_entry_pg
            try:
                log_calibration_entry_pg(entry)
            except Exception:
                pass

        calls = [str(c) for c in mock_cur.execute.call_args_list]
        savepoint_calls = [c for c in calls if "SAVEPOINT" in c.upper()]
        self.assertEqual(
            len(savepoint_calls), 0,
            f"SAVEPOINT used even though source_snapshot_id is None: {savepoint_calls}",
        )
        # Still must commit
        self.assertEqual(mock_conn.commit.call_count, 1, "No commit for no-snap entry")

    def test_no_separate_connection_for_downgrade(self):
        """
        The downgrade must NOT open a second DB connection.
        Only one _get_conn() call per log_calibration_entry_pg invocation.
        """
        entry = _make_entry("FINAL_APPROVED", snap_id="snap-nosep-001")

        with patch("gate_engine.llp_stage2_tables._get_conn") as mock_get_conn:
            mock_conn, mock_cur = _build_mock_conn_cursor(raises_on_release=True)
            mock_get_conn.return_value = mock_conn
            from gate_engine.llp_stage2_tables import log_calibration_entry_pg
            try:
                log_calibration_entry_pg(entry)
            except Exception:
                pass

        self.assertEqual(
            mock_get_conn.call_count, 1,
            f"More than one _get_conn() call — separate connection used for downgrade: "
            f"{mock_get_conn.call_count}",
        )

    def test_successful_snapshot_commits_normally(self):
        """
        When the snapshot INSERT succeeds, no UPDATE is executed and commit is
        called exactly once with the calibration + snapshot together.
        """
        entry = _make_entry("FINAL_APPROVED", snap_id="snap-ok-001")
        mock_conn, mock_cur = _build_mock_conn_cursor(raises_on_release=False)

        with patch("gate_engine.llp_stage2_tables._get_conn", return_value=mock_conn):
            from gate_engine.llp_stage2_tables import log_calibration_entry_pg
            try:
                log_calibration_entry_pg(entry)
            except Exception:
                pass

        calls = [str(c) for c in mock_cur.execute.call_args_list]
        # No ROLLBACK on success path
        rollback_calls = [c for c in calls if "ROLLBACK" in c.upper()]
        self.assertEqual(len(rollback_calls), 0, f"Unexpected ROLLBACK on success: {rollback_calls}")
        # Exactly one commit
        self.assertEqual(mock_conn.commit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
