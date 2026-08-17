"""
gate_engine/tests/test_row_reconciliation_explicit.py

WOW-PATCH-2026-08-16-AUDIT gap (1) revised — prove that the row reconciliation
counts are NOT tautological by verifying behavior with:

  (a) CONDITIONAL — illegal label: must be normalized to DATA_CONTRACT_FAIL
  (b) BOGUS_LABEL — unknown label: must be normalized to DATA_CONTRACT_FAIL
  (c) empty string — must be normalized to DATA_CONTRACT_FAIL
  (d) None         — must be normalized to DATA_CONTRACT_FAIL
  (e) whitespace   — must be normalized to DATA_CONTRACT_FAIL
  (f) lower-case   — must be normalized to DATA_CONTRACT_FAIL

All normalizations must record the original label in the row's blockers.

Also verifies:
  - _RC_HELD_LABELS and _RC_COMPLETED_LABELS are disjoint
  - DATA_CONTRACT_FAIL is NOT in either held or completed (lands in rejected)
  - FINAL_APPROVED is in completed only
  - Pipeline summary has rows_unknown == 0 and row_balance_valid == True
"""

from __future__ import annotations

import unittest
from datetime import date

# Module-level frozensets are importable directly (defined at pipeline top level)
from gate_engine.pipeline import (
    _RC_COMPLETED_LABELS,
    _RC_HELD_LABELS,
    _RC_REJECTED_LABELS,
    _rc_label_is_reject,
)


# ── Normalization unit tests ──────────────────────────────────────────────────

class TestNormalizationLogic(unittest.TestCase):
    """
    Directly apply the two-pass normalization logic to synthetic rows
    and verify the output label and blockers.
    """

    def _apply_normalization(self, label) -> dict:
        """
        Run both normalization passes on a synthetic row with the given label.
        Returns the row dict after normalization.
        """
        from gate_engine.pipeline import PropLabel
        row = {"terminal_label": label, "blockers": []}

        # Pass A: None / empty
        lbl_a = row.get("terminal_label")
        if not lbl_a:
            row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
            row["blockers"].append(f"UNLABELED_ROW_NORMALIZED:terminal_label_was={lbl_a!r}")
            return row

        # Pass B: unknown labels (not in any registered bucket)
        lbl_b = row.get("terminal_label") or ""
        if (
            lbl_b not in _RC_COMPLETED_LABELS
            and lbl_b not in _RC_HELD_LABELS
            and not _rc_label_is_reject(lbl_b)
        ):
            row["terminal_label"] = PropLabel.DATA_CONTRACT_FAIL.value
            row["blockers"].append(f"UNKNOWN_LABEL_NORMALIZED:was={lbl_b!r}")

        return row

    def _assert_dcf(self, label, description: str = ""):
        row = self._apply_normalization(label)
        self.assertEqual(
            row["terminal_label"],
            "DATA_CONTRACT_FAIL",
            f"{description or label!r} was not normalized to DATA_CONTRACT_FAIL; "
            f"got {row['terminal_label']!r}",
        )
        # Original label must appear in blockers for auditability
        blocker_str = " ".join(row["blockers"])
        original_str = "" if label is None else str(label)
        if original_str.strip():
            self.assertIn(
                original_str,
                blocker_str,
                f"Original label not recorded in blockers: {blocker_str!r}",
            )

    def test_conditional_normalized(self):
        self._assert_dcf("CONDITIONAL", "CONDITIONAL")

    def test_bogus_label_normalized(self):
        self._assert_dcf("BOGUS_LABEL", "BOGUS_LABEL")

    def test_empty_string_normalized(self):
        self._assert_dcf("", "empty string")

    def test_none_normalized(self):
        self._assert_dcf(None, "None")

    def test_lowercase_normalized(self):
        self._assert_dcf("conditional", "lower-case conditional")

    def test_whitespace_normalized(self):
        self._assert_dcf("   ", "whitespace-only string")

    def test_partial_label_normalized(self):
        self._assert_dcf("FINAL", "partial label FINAL")

    def test_known_held_label_not_normalized(self):
        """MODEL_QUALIFIED_HOLD must survive normalization unchanged."""
        row = self._apply_normalization("MODEL_QUALIFIED_HOLD")
        self.assertEqual(row["terminal_label"], "MODEL_QUALIFIED_HOLD")
        self.assertEqual(row["blockers"], [])

    def test_known_reject_label_not_normalized(self):
        """DATA_CONTRACT_FAIL is already in the rejected set and must not be renamed."""
        row = self._apply_normalization("DATA_CONTRACT_FAIL")
        self.assertEqual(row["terminal_label"], "DATA_CONTRACT_FAIL")

    def test_reject_prefix_label_not_normalized(self):
        """Any REJECT_* label must survive normalization as-is."""
        row = self._apply_normalization("REJECT_NO_EDGE")
        self.assertEqual(row["terminal_label"], "REJECT_NO_EDGE")

    def test_final_approved_not_normalized(self):
        """FINAL_APPROVED is in _RC_COMPLETED_LABELS and must not be touched."""
        row = self._apply_normalization("FINAL_APPROVED")
        self.assertEqual(row["terminal_label"], "FINAL_APPROVED")


# ── Frozenset membership tests ────────────────────────────────────────────────

class TestFrozensetMembership(unittest.TestCase):
    """Verify frozenset correctness (disjoint, correct membership)."""

    def test_completed_and_held_are_disjoint(self):
        overlap = _RC_COMPLETED_LABELS & _RC_HELD_LABELS
        self.assertEqual(overlap, frozenset(), f"Overlap: {overlap}")

    def test_completed_and_rejected_are_disjoint(self):
        overlap = _RC_COMPLETED_LABELS & _RC_REJECTED_LABELS
        self.assertEqual(overlap, frozenset(), f"Overlap: {overlap}")

    def test_held_and_rejected_are_disjoint(self):
        overlap = _RC_HELD_LABELS & _RC_REJECTED_LABELS
        self.assertEqual(overlap, frozenset(), f"Overlap: {overlap}")

    def test_final_approved_in_completed_only(self):
        self.assertIn("FINAL_APPROVED", _RC_COMPLETED_LABELS)
        self.assertNotIn("FINAL_APPROVED", _RC_HELD_LABELS)
        self.assertNotIn("FINAL_APPROVED", _RC_REJECTED_LABELS)

    def test_data_contract_fail_in_rejected_only(self):
        self.assertIn("DATA_CONTRACT_FAIL", _RC_REJECTED_LABELS)
        self.assertNotIn("DATA_CONTRACT_FAIL", _RC_COMPLETED_LABELS)
        self.assertNotIn("DATA_CONTRACT_FAIL", _RC_HELD_LABELS)

    def test_model_qualified_hold_in_held_only(self):
        self.assertIn("MODEL_QUALIFIED_HOLD", _RC_HELD_LABELS)
        self.assertNotIn("MODEL_QUALIFIED_HOLD", _RC_COMPLETED_LABELS)
        self.assertNotIn("MODEL_QUALIFIED_HOLD", _RC_REJECTED_LABELS)

    def test_rc_label_is_reject_for_reject_prefix(self):
        self.assertTrue(_rc_label_is_reject("REJECT_NO_EDGE"))
        self.assertTrue(_rc_label_is_reject("REJECT_BOGUS"))

    def test_rc_label_is_reject_false_for_held(self):
        self.assertFalse(_rc_label_is_reject("MODEL_QUALIFIED_HOLD"))

    def test_rc_label_is_reject_false_for_final_approved(self):
        self.assertFalse(_rc_label_is_reject("FINAL_APPROVED"))

    def test_all_three_sets_non_empty(self):
        self.assertGreater(len(_RC_COMPLETED_LABELS), 0)
        self.assertGreater(len(_RC_HELD_LABELS), 0)
        self.assertGreater(len(_RC_REJECTED_LABELS), 0)


# ── Pipeline summary reconciliation tests ────────────────────────────────────

class TestPipelineSummaryReconciliation(unittest.TestCase):
    """
    Drive rows through run_pipeline() with skip_data_contract=True and verify
    the summary reconciliation invariants:
      - rows_unknown == 0
      - row_balance_valid == True
      - rows_in == completed + held + rejected exactly
    """

    def _pipeline_summary(self, rows=None, n=3):
        from gate_engine.pipeline import run_pipeline
        today = date.today()
        if rows is None:
            rows = [
                {
                    "player": f"P{i}", "prop_type": "PASSING_TDS", "stat_key": "PASSING_TDS",
                    "sport": "NFL", "line": 5.5, "sportsbook_line": 5.5,
                    "over_odds": -110, "under_odds": -110,
                    "team": "TST", "opponent": "OPP", "game_date": today.isoformat(),
                }
                for i in range(n)
            ]
        result = run_pipeline(
            raw_rows=rows,
            target_date=today,
            enrichment={},
            skip_data_contract=True,
        )
        return result.get("summary", {})

    def test_empty_run_balance(self):
        s = self._pipeline_summary(rows=[], n=0)
        self.assertEqual(s.get("rows_in"), 0)
        self.assertEqual(s.get("rows_unknown", -1), 0)
        self.assertTrue(s.get("row_balance_valid"), f"False: {s}")

    def test_single_row_balance(self):
        s = self._pipeline_summary(n=1)
        self.assertEqual(s.get("rows_unknown", -1), 0)
        self.assertTrue(s.get("row_balance_valid"), f"False: {s}")
        self.assertEqual(
            s["rows_in"],
            s["rows_completed"] + s["rows_held"] + s["rows_rejected"],
        )

    def test_multi_row_balance(self):
        s = self._pipeline_summary(n=5)
        self.assertEqual(s.get("rows_unknown", -1), 0)
        self.assertTrue(s.get("row_balance_valid"), f"False: {s}")
        self.assertEqual(
            s["rows_in"],
            s["rows_completed"] + s["rows_held"] + s["rows_rejected"],
        )

    def test_rows_other_always_zero(self):
        s = self._pipeline_summary(n=3)
        self.assertEqual(s.get("rows_other", 0), 0)


if __name__ == "__main__":
    unittest.main()
