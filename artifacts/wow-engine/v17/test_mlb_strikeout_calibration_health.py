from __future__ import annotations

import unittest

from v17.mlb_strikeout_calibration_health import (
    HEALTH_HEALTHY,
    HEALTH_INSUFFICIENT_SAMPLE,
    HEALTH_WATCH,
    MODEL_INPUTS_INSUFFICIENT,
    SettledStrikeoutRecord,
    evaluate_strikeout_calibration_health,
)


def _record(i, *, win, p=0.68, lb=0.62, feature_k_rate=0.24):
    return SettledStrikeoutRecord(
        prediction_id=f"pred-{i}",
        direction="MORE",
        model_probability=p,
        calibrated_probability=p,
        calibrated_lower_bound=lb,
        official_result="WIN" if win else "LOSS",
        feature_snapshot={"pitcher_k_rate": feature_k_rate},
    )


class WellCalibratedFixtureTest(unittest.TestCase):
    def test_healthy_when_hit_rate_tracks_predicted_probability(self):
        records = [_record(i, win=(i % 100) < 68) for i in range(200)]
        result = evaluate_strikeout_calibration_health(records)
        self.assertEqual(result.state, HEALTH_HEALTHY)
        self.assertEqual(result.sample_count, 200)
        self.assertIsNotNone(result.brier_score)
        self.assertFalse(result.reason_codes)
        self.assertFalse(result.can_execute)


class DegradedCalibrationFixtureTest(unittest.TestCase):
    def test_watch_when_hit_rate_diverges_from_predicted_probability(self):
        # Model predicts ~0.68 hit rate but only ~0.30 actually hit: real drift.
        records = [_record(i, win=(i % 100) < 30) for i in range(200)]
        result = evaluate_strikeout_calibration_health(records)
        self.assertEqual(result.state, HEALTH_WATCH)
        self.assertIn("RELIABILITY_ERROR_ABOVE_WATCH_THRESHOLD", result.reason_codes)

    def test_watch_when_lower_bound_coverage_collapses(self):
        records = [_record(i, win=(i % 100) < 20, p=0.68, lb=0.62) for i in range(200)]
        result = evaluate_strikeout_calibration_health(records)
        self.assertEqual(result.state, HEALTH_WATCH)
        self.assertIn("LOWER_BOUND_COVERAGE_BELOW_WATCH_THRESHOLD", result.reason_codes)

    def test_watch_on_feature_drift_even_if_calibration_looks_fine(self):
        records = [_record(i, win=(i % 100) < 68, feature_k_rate=0.40) for i in range(200)]
        result = evaluate_strikeout_calibration_health(
            records,
            baseline_feature_means={"pitcher_k_rate": 0.224},
            baseline_feature_stddevs={"pitcher_k_rate": 0.03},
        )
        self.assertEqual(result.state, HEALTH_WATCH)
        self.assertIn("FEATURE_DRIFT_DETECTED", result.reason_codes)
        self.assertTrue(any(f.watch for f in result.feature_drift))


class InsufficientSampleFixtureTest(unittest.TestCase):
    def test_insufficient_sample_state_below_minimum(self):
        records = [_record(i, win=True) for i in range(5)]
        result = evaluate_strikeout_calibration_health(records)
        self.assertEqual(result.state, HEALTH_INSUFFICIENT_SAMPLE)
        self.assertIn(MODEL_INPUTS_INSUFFICIENT, result.reason_codes)
        self.assertIsNone(result.brier_score)


class MissingOrIncompleteOutcomeFixtureTest(unittest.TestCase):
    def test_push_and_void_rows_are_excluded_not_counted_as_evidence(self):
        settled = [_record(i, win=(i % 100) < 68) for i in range(200)]
        pushes = [
            SettledStrikeoutRecord(
                prediction_id=f"push-{i}",
                direction="MORE",
                model_probability=0.68,
                calibrated_probability=0.68,
                calibrated_lower_bound=0.62,
                official_result="PUSH",
            )
            for i in range(30)
        ]
        result = evaluate_strikeout_calibration_health(settled + pushes)
        self.assertEqual(result.sample_count, 200)

    def test_invalid_calibrated_output_is_flagged_and_excluded(self):
        settled = [_record(i, win=(i % 100) < 68) for i in range(200)]
        broken = [
            SettledStrikeoutRecord(
                prediction_id="broken-1",
                direction="MORE",
                model_probability=0.68,
                calibrated_probability=1.4,  # out of (0,1) domain -> invalid output
                calibrated_lower_bound=0.62,
                official_result="WIN",
            )
        ]
        result = evaluate_strikeout_calibration_health(settled + broken)
        self.assertEqual(result.sample_count, 200)
        self.assertIn("MODEL_OUTPUT_INVALID", result.reason_codes)


class MonitorCannotMutateStoredProbabilitiesTest(unittest.TestCase):
    def test_evaluation_does_not_alter_input_records(self):
        records = [_record(i, win=(i % 100) < 68) for i in range(200)]
        before = [
            (r.prediction_id, r.model_probability, r.calibrated_probability, r.calibrated_lower_bound)
            for r in records
        ]
        evaluate_strikeout_calibration_health(records)
        after = [
            (r.prediction_id, r.model_probability, r.calibrated_probability, r.calibrated_lower_bound)
            for r in records
        ]
        self.assertEqual(before, after)

    def test_result_type_exposes_no_mutation_hook(self):
        records = [_record(i, win=(i % 100) < 68) for i in range(200)]
        result = evaluate_strikeout_calibration_health(records)
        # Frozen dataclass: no setter path exists for stored probabilities.
        with self.assertRaises(Exception):
            result.brier_score = 0.0  # type: ignore[misc]

    def test_no_auto_promotion_or_retraining_side_effects(self):
        records = [_record(i, win=(i % 100) < 30) for i in range(200)]
        result = evaluate_strikeout_calibration_health(records)
        self.assertEqual(result.state, HEALTH_WATCH)
        self.assertFalse(result.can_execute)
        result_dict = result.as_dict()
        for forbidden_key in ("retrain", "recalibrate", "promote", "disable_specialist"):
            self.assertNotIn(forbidden_key, result_dict)


if __name__ == "__main__":
    unittest.main()
