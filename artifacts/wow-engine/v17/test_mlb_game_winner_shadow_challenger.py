import math
import unittest

import numpy as np

from v17.mlb_game_winner_shadow_challenger import (
    ADMISSION_POLICY_MUTATION_ALLOWED,
    AUTOMATIC_PROMOTION_ALLOWED,
    DEFAULT_FEATURES,
    MARKET_PRIOR_WEIGHT,
    ShadowChallengerError,
    audit_feature_names,
    calibration_first_comparison,
    feature_coverage,
    fit_shadow_challenger,
    predict_shadow,
)


def synthetic_rows(n=240, seed=11):
    rng = np.random.default_rng(seed)
    rows = []
    y = []
    for _ in range(n):
        starter_kbb = rng.normal()
        off = rng.normal()
        bp = rng.normal()
        catastrophe = rng.beta(2, 8)
        cluster = rng.beta(2, 7)
        handoff = rng.beta(2, 8)
        hfa = 1.0
        # Tail terms are deliberately load-bearing in this synthetic fixture.
        z = (
            0.10 + 0.42 * starter_kbb + 0.36 * off + 0.20 * bp
            - 1.40 * catastrophe + 1.15 * cluster - 0.90 * handoff
            + 0.12 * hfa
        )
        p = 1.0 / (1.0 + math.exp(-z))
        y.append(rng.random() < p)
        row = {name: rng.normal() for name in DEFAULT_FEATURES}
        row.update({
            "starter_kbb_diff_6": starter_kbb,
            "off_ops_diff_20": off,
            "bullpen_kbb_diff_20": bp,
            "hfa_indicator": hfa,
            "starter_catastrophe_rate_diff": catastrophe,
            "offense_cluster_rate_diff": cluster,
            "handoff_risk_diff": handoff,
        })
        rows.append(row)
    return rows, y


class GameWinnerShadowSharpnessTests(unittest.TestCase):
    def setUp(self):
        rows, y = synthetic_rows()
        self.train_rows, self.train_y = rows[:150], y[:150]
        self.cal_rows, self.cal_y = rows[150:195], y[150:195]
        self.test_rows, self.test_y = rows[195:], y[195:]
        self.artifact = fit_shadow_challenger(
            self.train_rows,
            self.train_y,
            self.cal_rows,
            self.cal_y,
            bootstrap_models=24,
            seed=1706,
        )

    def test_shadow_never_mutates_admission_policy(self):
        self.assertFalse(ADMISSION_POLICY_MUTATION_ALLOWED)
        self.assertFalse(AUTOMATIC_PROMOTION_ALLOWED)
        self.assertEqual(MARKET_PRIOR_WEIGHT, 0.0)

    def test_market_features_rejected(self):
        with self.assertRaisesRegex(ShadowChallengerError, "GOVERNANCE_MARKET_LEAKAGE"):
            audit_feature_names(["starter_kbb_diff_6", "sportsbook_implied_probability"])

    def test_payout_features_rejected(self):
        with self.assertRaisesRegex(ShadowChallengerError, "GOVERNANCE_MARKET_LEAKAGE"):
            audit_feature_names(["starter_kbb_diff_6", "prizepicks_multiplier"])

    def test_postgame_features_rejected(self):
        with self.assertRaisesRegex(ShadowChallengerError, "GOVERNANCE_POSTGAME_LEAKAGE"):
            audit_feature_names(["starter_kbb_diff_6", "actual_outcome"])

    def test_missing_feature_values_are_imputed_not_filtered(self):
        rows = [dict(self.test_rows[0]), dict(self.test_rows[1])]
        rows[0]["starter_run_variance_diff"] = None
        rows[1].pop("bullpen_3plus_rate_diff")
        predictions = predict_shadow(self.artifact, rows)
        self.assertEqual(len(predictions), 2)
        self.assertTrue(all(0.0 <= p.home_probability_calibrated <= 1.0 for p in predictions))

    def test_one_shared_probability_reconciles_home_and_away(self):
        for p in predict_shadow(self.artifact, self.test_rows[:8]):
            self.assertAlmostEqual(p.home_probability_raw + p.away_probability_raw, 1.0, places=12)
            self.assertAlmostEqual(p.home_probability_calibrated + p.away_probability_calibrated, 1.0, places=12)
            self.assertFalse(p.can_execute)
            self.assertFalse(p.admission_policy_mutated)

    def test_bootstrap_interval_is_candidate_specific(self):
        predictions = predict_shadow(self.artifact, self.test_rows[:12])
        widths = [round(p.home_upper_bound - p.home_lower_bound, 8) for p in predictions]
        self.assertGreater(len(set(widths)), 1)

    def test_no_bootstrap_does_not_invent_universal_haircut(self):
        artifact = fit_shadow_challenger(
            self.train_rows,
            self.train_y,
            self.cal_rows,
            self.cal_y,
            bootstrap_models=0,
            seed=1706,
        )
        for p in predict_shadow(artifact, self.test_rows[:5]):
            self.assertAlmostEqual(p.home_lower_bound, p.home_probability_calibrated)
            self.assertAlmostEqual(p.home_upper_bound, p.home_probability_calibrated)

    def test_feature_coverage_is_observational_only(self):
        rows = [dict(self.test_rows[0]), dict(self.test_rows[1])]
        rows[1].pop("starter_run_variance_diff")
        coverage = feature_coverage(rows, DEFAULT_FEATURES)
        self.assertEqual(coverage["starter_run_variance_diff"], 0.5)
        self.assertEqual(len(predict_shadow(self.artifact, rows)), 2)

    def test_calibration_comparison_never_auto_promotes(self):
        y = [False, False, True, True, True, False, True, False, True, False] * 4
        champion = [0.46, 0.48, 0.54, 0.56, 0.58, 0.44, 0.57, 0.43, 0.55, 0.45] * 4
        challenger = [0.42, 0.45, 0.58, 0.60, 0.62, 0.40, 0.61, 0.39, 0.59, 0.41] * 4
        result = calibration_first_comparison(y, champion, challenger)
        self.assertFalse(result["automatic_promotion"])
        self.assertFalse(result["cash_single_gate_mutated"])
        self.assertFalse(result["admission_policy_mutated"])

    def test_every_hydratable_candidate_receives_probability(self):
        predictions = predict_shadow(self.artifact, self.test_rows)
        self.assertEqual(len(predictions), len(self.test_rows))
        self.assertTrue(all(p.serving_mode == "SHADOW_ONLY" for p in predictions))


if __name__ == "__main__":
    unittest.main()
