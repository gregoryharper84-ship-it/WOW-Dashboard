from __future__ import annotations

import unittest

from v17.llp_v17_1_calibration_monotonicity import (
    AUTOMATIC_PROMOTION_ALLOWED,
    CAN_EXECUTE,
    PRODUCTION_BOUND_MUTATION_ALLOWED,
    PRODUCTION_RANKING_MUTATION_ALLOWED,
    CalibrationBin,
    audit_lower_bound_monotonicity,
    challenger_manifest,
    monotone_shrunk_calibration_targets,
    weighted_pava,
)


class TestLLPV171CalibrationMonotonicity(unittest.TestCase):
    def test_detects_home_style_bound_inversions(self):
        bins = [
            CalibrationBin("HOME", 7, 68, 0.5362, 0.6324, 0.5136),
            CalibrationBin("HOME", 8, 68, 0.5430, 0.5441, 0.4266),
            CalibrationBin("HOME", 9, 68, 0.5506, 0.5147, 0.3983),
            CalibrationBin("HOME", 10, 68, 0.5699, 0.6912, 0.5736),
        ]
        audit = audit_lower_bound_monotonicity(bins)
        self.assertFalse(audit["monotone"])
        self.assertEqual(audit["inversion_count"], 2)
        self.assertEqual(audit["inversions"][0].prior_bin_index, 7)
        self.assertEqual(audit["inversions"][0].current_bin_index, 8)
        self.assertEqual(audit["inversions"][1].prior_bin_index, 8)
        self.assertEqual(audit["inversions"][1].current_bin_index, 9)
        self.assertFalse(audit["production_bound_mutated"])
        self.assertFalse(audit["production_ranking_mutated"])

    def test_monotone_input_passes(self):
        bins = [
            CalibrationBin("AWAY", 1, 69, 0.43, 0.32, 0.22),
            CalibrationBin("AWAY", 2, 69, 0.45, 0.40, 0.29),
            CalibrationBin("AWAY", 3, 69, 0.47, 0.48, 0.37),
        ]
        audit = audit_lower_bound_monotonicity(bins)
        self.assertTrue(audit["monotone"])
        self.assertEqual(audit["inversion_count"], 0)

    def test_weighted_pava_repairs_non_monotone_targets(self):
        fitted = weighted_pava(
            [0.55, 0.63, 0.54, 0.51, 0.69],
            [68, 68, 68, 68, 68],
        )
        self.assertEqual(len(fitted), 5)
        self.assertTrue(all(a <= b for a, b in zip(fitted, fitted[1:])))
        self.assertAlmostEqual(fitted[1], fitted[2])
        self.assertAlmostEqual(fitted[2], fitted[3])

    def test_shrink_then_monotone_is_shadow_target_only(self):
        bins = [
            CalibrationBin("HOME", 7, 68, 0.5362, 0.6324, 0.5136),
            CalibrationBin("HOME", 8, 68, 0.5430, 0.5441, 0.4266),
            CalibrationBin("HOME", 9, 68, 0.5506, 0.5147, 0.3983),
            CalibrationBin("HOME", 10, 68, 0.5699, 0.6912, 0.5736),
        ]
        targets = monotone_shrunk_calibration_targets(
            bins,
            global_observed_rate=0.54,
            prior_strength=30,
        )
        values = [row.monotone_observed_target for row in targets]
        self.assertTrue(all(a <= b for a, b in zip(values, values[1:])))
        self.assertEqual([row.original_lower_bound for row in targets], [0.5136, 0.4266, 0.3983, 0.5736])

    def test_governance_invariants(self):
        manifest = challenger_manifest()
        self.assertFalse(CAN_EXECUTE)
        self.assertFalse(AUTOMATIC_PROMOTION_ALLOWED)
        self.assertFalse(PRODUCTION_BOUND_MUTATION_ALLOWED)
        self.assertFalse(PRODUCTION_RANKING_MUTATION_ALLOWED)
        self.assertEqual(manifest["serving_mode"], "SHADOW_ONLY")
        self.assertFalse(manifest["can_execute"])


if __name__ == "__main__":
    unittest.main()
