import unittest

from gate_engine.full_board_confidence import (
    FULL_BOARD_CONFIDENCE_PASS,
    FULL_BOARD_RUN_INCOMPLETE,
    audit_full_board_confidence,
    confidence_category,
)


class TestFullBoardConfidence(unittest.TestCase):
    def test_complete_board_allows_optimizer_and_claim_with_publishable_model(self):
        rows = [
            {
                "canonical_selection_id": "a",
                "confidence_decision": "FINAL_CONFIDENCE_HIGH",
                "probability_publishable": True,
                "calibration_status": "CALIBRATED",
                "calibrated_probability_lower_bound": 0.62,
            },
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
        self.assertEqual(result["publishable_modeled_confidence_rows"], 1)
        self.assertEqual(result["confidence_accounted_rows"], 3)

    def test_complete_board_without_publishable_model_blocks_claims(self):
        rows = [
            {"canonical_selection_id": "a", "terminal_label": "MODEL_UNAVAILABLE"},
            {"canonical_selection_id": "b", "terminal_label": "DATA_INSUFFICIENT"},
        ]
        result = audit_full_board_confidence(
            rows, discovered_count=2, reconciliation_passed=True
        )
        self.assertEqual(result["status"], FULL_BOARD_CONFIDENCE_PASS)
        self.assertEqual(result["confidence_accounted_rows"], 2)
        self.assertEqual(result["publishable_modeled_confidence_rows"], 0)
        self.assertFalse(result["optimizer_allowed"])
        self.assertFalse(result["promising_count_claim_allowed"])

    def test_typed_incomplete_handoff_counts_as_no_confidence(self):
        row = {
            "canonical_selection_id": "k-row",
            "terminal_label": "DATA_CONTRACT_FAIL",
            "model_probability_handoff": {
                "status": "INCOMPLETE",
                "code": "MODEL_GAME_LOG_INCOMPLETE",
            },
            "candidate_evaluation_completed": False,
            "raw_model_probability": None,
            "calibration_status": "UNAVAILABLE",
            "probability_publishable": False,
        }
        self.assertEqual(confidence_category(row), "NO_CONFIDENCE")
        result = audit_full_board_confidence(
            [row], discovered_count=1, reconciliation_passed=True
        )
        self.assertEqual(result["status"], FULL_BOARD_CONFIDENCE_PASS)
        self.assertEqual(result["confidence_categories"]["NO_CONFIDENCE"], 1)
        self.assertFalse(result["optimizer_allowed"])
        self.assertFalse(result["promising_count_claim_allowed"])

    def test_typed_incomplete_1ip_handoff_counts_as_no_confidence(self):
        row = {
            "canonical_selection_id": "1ip-row",
            "terminal_label": "DATA_CONTRACT_FAIL",
            "model_probability_handoff": {
                "status": "INCOMPLETE",
                "code": "1IP_EVENT_TREE_INPUT_INCOMPLETE",
            },
            "candidate_evaluation_completed": False,
            "raw_model_probability": None,
            "probability_publishable": False,
        }
        self.assertEqual(confidence_category(row), "NO_CONFIDENCE")

    def test_generic_data_contract_fail_does_not_become_no_confidence(self):
        row = {
            "canonical_selection_id": "bad-contract",
            "terminal_label": "DATA_CONTRACT_FAIL",
        }
        self.assertIsNone(confidence_category(row))

    def test_duplicate_label_alone_does_not_become_no_confidence(self):
        row = {
            "canonical_selection_id": "duplicate",
            "terminal_label": "REJECT_ALTERNATE_THRESHOLD_DUPLICATE",
        }
        self.assertIsNone(confidence_category(row))

    def test_nonpublishable_or_provisional_alone_does_not_complete_confidence(self):
        row = {
            "canonical_selection_id": "raw-only",
            "terminal_label": "MODEL_QUALIFIED_HOLD",
            "model_probability_handoff": {
                "status": "RAW_MODEL_RESULT_RECORDED",
                "code": "MODEL_RESULT_AVAILABLE",
            },
            "candidate_evaluation_completed": True,
            "raw_model_probability": 0.68,
            "calibration_status": "PROVISIONAL",
            "probability_publishable": False,
        }
        self.assertIsNone(confidence_category(row))

    def test_explicit_confidence_survives_nonconfidence_terminal_constraint(self):
        row = {
            "canonical_selection_id": "duplicate-with-confidence",
            "confidence_decision": "FINAL_CONFIDENCE_MEDIUM",
            "terminal_label": "REJECT_ALTERNATE_THRESHOLD_DUPLICATE",
            "model_probability_handoff": {
                "status": "INCOMPLETE",
                "code": "MODEL_GAME_LOG_INCOMPLETE",
            },
            "candidate_evaluation_completed": False,
            "raw_model_probability": None,
        }
        self.assertEqual(confidence_category(row), "MEDIUM_CONFIDENCE")

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
        self.assertFalse(result["optimizer_allowed"])
        self.assertFalse(result["promising_count_claim_allowed"])

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
        self.assertFalse(result["optimizer_allowed"])


if __name__ == "__main__":
    unittest.main()
