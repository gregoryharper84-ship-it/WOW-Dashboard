"""Pure projection: focus, negative cases, reconciliation, and no approval promotion."""
import unittest
from v17.user_visibility_projection import project_pick_visibility

def scored(**overrides):
    prediction = {
        "prediction_id": "persisted-1", "model_family": "NFL_FITTED_V1",
        "model_artifact_version": "2026-09-20", "calibration_status": "PASS",
        "calibrated_probability": .64, "calibrated_probability_lower_bound": .56,
        "calibrated_probability_upper_bound": .70, "money_lane_status": "PAYOUT_UNRESOLVED",
    }
    row = {
        "row_key": "nfl-1", "terminal_status": "REJECTED",
        "terminal_label": "NO_LOW_PROBABILITY", "code": "NO_LOW_PROBABILITY",
        "model_evaluated": True, "probability_publishable": True,
        "rank_eligible": False, "source_snapshot_id": "immutable-source-1",
        "result": {"prediction": prediction}, "can_execute": False,
    }
    row.update(overrides)
    return row

class VisibilityTests(unittest.TestCase):
    def test_payout_unresolved_is_analysis_not_pick(self):
        sections = project_pick_visibility([scored()])
        self.assertEqual(len(sections["model_analysis_not_approved"]), 1)
        self.assertFalse(sections["model_approved_picks"])
        self.assertEqual(sections["model_analysis_not_approved"][0]["calibrated_probability"], .64)
        self.assertFalse(sections["model_analysis_not_approved"][0]["recommendation_authorized"])

    def test_missing_model_does_not_get_probability(self):
        row = scored(model_evaluated=False, probability_publishable=False,
                     terminal_label="MODEL_UNAVAILABLE", result={})
        sections = project_pick_visibility([row])
        self.assertEqual(len(sections["withheld_rows"]), 1)
        self.assertNotIn("calibrated_probability", sections["withheld_rows"][0])

    def test_uncertified_and_missing_identity_withheld(self):
        base = scored()
        for field, value in (("probability_publishable", False), ("model_evaluated", False),
                             ("source_snapshot_id", None)):
            with self.subTest(field=field):
                self.assertEqual(len(project_pick_visibility([{**base, field: value}])["withheld_rows"]), 1)
        bad = {**base["result"]["prediction"], "model_family": None}
        self.assertEqual(len(project_pick_visibility([{**base, "result": {"prediction": bad}}])["withheld_rows"]), 1)

    def test_bad_calibration_cannot_leak(self):
        for value in (float("nan"), float("inf"), -0.01, 1.01, True, "0.62"):
            with self.subTest(value=str(value)):
                bad = {**scored()["result"]["prediction"], "calibrated_probability": value}
                self.assertEqual(len(project_pick_visibility([scored(result={"prediction": bad})])["withheld_rows"]), 1)
        bad = {**scored()["result"]["prediction"], "calibrated_probability_lower_bound": .70}
        self.assertEqual(len(project_pick_visibility([scored(result={"prediction": bad})])["withheld_rows"]), 1)

    def test_approved_pick_only_from_complete_backend_receipt(self):
        row = scored(
            terminal_status="COMPLETED", terminal_label="FINAL_APPROVED",
            code="FINAL_APPROVED", rank_eligible=True,
            downstream_portfolio_evaluation_allowed=True,
            result={"prediction": {**scored()["result"]["prediction"], "money_lane_status": "RESOLVED"},
                    "probability_qualification": {"final_approved_allowed": True}},
        )
        self.assertEqual(len(project_pick_visibility([row])["model_approved_picks"]), 1)
        row["result"]["probability_qualification"]["final_approved_allowed"] = False
        self.assertEqual(len(project_pick_visibility([row])["model_analysis_not_approved"]), 1)

    def test_mixed_rows_reconcile_and_execution_disabled(self):
        out = project_pick_visibility([
            scored(), scored(row_key="mlb-2", probability_publishable=False),
            scored(row_key="nba-3", model_evaluated=False, result={}),
        ])
        self.assertEqual((out["rows_in"], out["rows_reconciled"]), (3, 3))
        self.assertEqual((len(out["model_analysis_not_approved"]), len(out["withheld_rows"])), (1, 2))
        self.assertIs(out["can_execute"], False)

if __name__ == "__main__":
    unittest.main()
