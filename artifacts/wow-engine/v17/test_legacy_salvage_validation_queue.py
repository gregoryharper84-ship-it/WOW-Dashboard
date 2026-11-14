"""Confidence debt queue regression: unknown is never false/qualified."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


def _load(module_name: str):
    path = Path(__file__).with_name(module_name + ".py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


queue = _load("legacy_salvage_validation_queue")


def row(index: int, missing=None, issues=None, market="NO_MARKET"):
    return {
        "row_key": f"row-{index}",
        "diagnostic_only": True,
        "source_fields_missing": missing or [],
        "sporting_evidence_issues": issues or [],
        "market_evidence_type": market,
    }


def batch(rows):
    return {
        "rows_received": len(rows),
        "rows_reported": len(rows),
        "rows": rows,
        "diagnostic_only": True,
        "can_execute": False,
    }


class AdvisoryConfidenceDebtQueueTests(unittest.TestCase):
    def test_27_source_rows_reconcile_without_blanket_failure(self):
        rows = [row(i, market="EXACT_LINE") for i in range(27)]
        rows[0]["source_fields_missing"] = ["direction"]
        rows[1]["sporting_evidence_issues"] = ["critical_participant_unavailable"]
        rows[2]["sporting_evidence_issues"] = ["material_update_after_model"]
        output = queue.build_validation_queue(batch(rows), market_analysis_requested=False)
        self.assertEqual((output["rows_received"], output["rows_reported"]), (27, 27))
        self.assertEqual(len(output["rows"]), 27)
        self.assertEqual(output["rows"][0]["recovery_actions"], [
            "VERIFY_OFFICIAL_IDENTITY_AND_EXACT_CONTRACT"
        ])
        self.assertIn("VERIFY_PARTICIPANT_AVAILABILITY_AND_ROUTE",
                      output["rows"][1]["recovery_actions"])
        self.assertIn("REHYDRATE_CERTIFIED_MODEL_INPUTS_IF_PREGAME",
                      output["rows"][2]["recovery_actions"])
        self.assertEqual(output["rows"][3]["recovery_actions"], [])

    def test_unknown_debt_is_null_not_false(self):
        out = queue.build_validation_queue(batch([row(1, market="EXACT_LINE")]),
                                            market_analysis_requested=True)["rows"][0]
        self.assertTrue(all(out["confidence_debt"][field] is None
                            for field in queue.DEBT_FIELDS))

    def test_missing_market_only_debt_when_market_requested(self):
        no_price = batch([row(1, market="NO_MARKET")])
        result = queue.build_validation_queue(no_price, market_analysis_requested=True)["rows"][0]
        self.assertTrue(result["confidence_debt"]["market_debt"])
        self.assertIsNone(result["confidence_debt"]["ledger_debt"])
        self.assertIn("FIND_EXACT_LINE_PRICE_FOR_MARKET_AUDIT_ONLY",
                      result["recovery_actions"])

        prob_only = queue.build_validation_queue(no_price, market_analysis_requested=False)["rows"][0]
        self.assertIsNone(prob_only["confidence_debt"]["market_debt"])
        self.assertNotIn("FIND_EXACT_LINE_PRICE_FOR_MARKET_AUDIT_ONLY",
                         prob_only["recovery_actions"])

    def test_stale_source_and_started_event_have_separate_recovery(self):
        r = row(1, issues=["source_evidence_stale", "scheduled_event_has_started"])
        out = queue.build_validation_queue(batch([r]))["rows"][0]
        self.assertTrue(out["confidence_debt"]["source_provenance_debt"])
        self.assertTrue(out["confidence_debt"]["status_debt"])
        self.assertIn("REFRESH_OFFICIAL_EVENT_SCHEDULE_STATUS", out["recovery_actions"])
        self.assertIn("REFRESH_AND_TIMESTAMP_SOURCE_EVIDENCE", out["recovery_actions"])

    def test_malformed_preflight_is_rejected(self):
        with self.assertRaises(ValueError):
            queue.build_validation_queue({"rows_received": 2, "rows": [row(1)],
                                          "diagnostic_only": True, "can_execute": False})
        with self.assertRaises(ValueError):
            queue.build_validation_queue({"rows_received": 1, "rows": [row(1)],
                                          "diagnostic_only": False, "can_execute": False})

    def test_output_never_contains_probability_or_terminal_approval(self):
        out = queue.build_validation_queue(
            batch([row(1, missing=["exact_line"], market="NO_MARKET")]),
            market_analysis_requested=True,
        )
        self.assertFalse(out["can_execute"])
        self.assertFalse(out["probability_authority"])
        self.assertEqual(out["terminal_authority"], "V17_TERMINAL_REDUCER")
        for item in out["rows"]:
            for key in ("model_probability", "calibrated_probability",
                        "calibrated_lower_bound", "terminal_label"):
                self.assertIsNone(item[key])
            self.assertFalse(item["can_execute"])


if __name__ == "__main__":
    unittest.main()
