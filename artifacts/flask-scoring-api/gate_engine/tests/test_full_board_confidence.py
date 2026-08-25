import unittest

from gate_engine.full_board_confidence import (
    FULL_BOARD_CONFIDENCE_PASS,
    FULL_BOARD_RUN_INCOMPLETE,
    audit_full_board_confidence,
)


class TestFullBoardConfidence(unittest.TestCase):
    def test_complete_board_allows_optimizer_and_claim(self):
        rows = [
            {"canonical_selection_id": "a", "confidence_decision": "FINAL_CONFIDENCE_HIGH"},
            {"canonical_selection_id": "b", "confidence_decision": "FINAL_CONFIDENCE_MEDIUM"},
            {"canonical_selection_id": "c", "terminal_label": "MODEL_UNAVAILABLE"},
        ]
        result = audit_full_board_confidence(
            rows, discovered_count=3, reconciliation_passed=True
        )
        self.assertEqual(result["status"], FULL_BOARD_CONFIDENCE_PASS)
        self.assertTrue(result["optimizer_allowed"])
        self.assertTrue(result["promising_count_claim_allowed"])
        self.assertEqual(result["modeled_confidence_rows"], 2)
        self.assertEqual(result["confidence_accounted_rows"], 3)

    def test_four_of_sixty_one_forbids_board_wide_claim(self):
        rows = [
            {"canonical_selection_id": str(i), "model_probability": 0.70}
            for i in range(4)
        ]
        result = audit_full_board_confidence(
            rows, discovered_count=61, reconciliation_passed=False
        )
        self.assertEqual(result["status"], FULL_BOARD_RUN_INCOMPLETE)
        self.assertFalse(result["optimizer_allowed"])
        self.assertFalse(result["promising_count_claim_allowed"])

    def test_terminal_no_confidence_is_accounted(self):
        rows = [
            {"canonical_selection_id": "a", "terminal_label": "DATA_INSUFFICIENT"},
            {"canonical_selection_id": "b", "terminal_label": "MODEL_UNAVAILABLE"},
        ]
        result = audit_full_board_confidence(
            rows, discovered_count=2, reconciliation_passed=True
        )
        self.assertEqual(result["status"], FULL_BOARD_CONFIDENCE_PASS)
        self.assertEqual(result["confidence_categories"]["NO_CONFIDENCE"], 2)

    def test_reconciled_but_uncategorized_row_fails_closed(self):
        rows = [{"canonical_selection_id": "a", "classification": "WATCH"}]
        result = audit_full_board_confidence(
            rows, discovered_count=1, reconciliation_passed=True
        )
        self.assertEqual(result["status"], FULL_BOARD_RUN_INCOMPLETE)
        self.assertEqual(result["unaccounted_ids"], ["a"])

    def test_global_blocker_is_not_model_eligible(self):
        rows = [
            {"canonical_selection_id": "a", "global_blocker": True},
            {"canonical_selection_id": "b", "hit_probability": 0.58},
        ]
        result = audit_full_board_confidence(
            rows, discovered_count=2, reconciliation_passed=True
        )
        self.assertEqual(result["status"], FULL_BOARD_CONFIDENCE_PASS)
        self.assertEqual(result["model_eligible_rows"], 1)
        self.assertEqual(result["confidence_categories"]["GLOBAL_BLOCKER"], 1)


if __name__ == "__main__":
    unittest.main()
