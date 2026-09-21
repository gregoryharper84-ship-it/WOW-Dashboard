from __future__ import annotations

import unittest

from v17.llp_v17_1_sharpness_challenger import (
    AUTOMATIC_PROMOTION_ALLOWED,
    CAN_EXECUTE,
    MARKET_PRIOR_WEIGHT,
    PRODUCTION_CALIBRATION_MUTATION_ALLOWED,
    PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED,
    PRODUCTION_RANKING_MUTATION_ALLOWED,
    CandidateProbability,
    UncertaintyConsumption,
    challenger_manifest,
    evaluate_ranking_strategy,
    governance_classification,
    hierarchical_cohort_target,
    market_divergence_diagnostic,
    rank_candidates,
    sensitivity_attribution,
    uncertainty_consumption_audit,
)


class TestLLPV171SharpnessChallenger(unittest.TestCase):
    def test_probability_and_lower_bound_can_rank_differently(self):
        candidates = [
            CandidateProbability("A", calibrated_probability=0.68, calibrated_lower_bound=0.53),
            CandidateProbability("B", calibrated_probability=0.59, calibrated_lower_bound=0.55),
        ]
        by_probability = rank_candidates(candidates, strategy="CALIBRATED_PROBABILITY")
        by_lower = rank_candidates(candidates, strategy="CALIBRATED_LOWER_BOUND")
        self.assertEqual(by_probability[0].candidate_id, "A")
        self.assertEqual(by_lower[0].candidate_id, "B")

    def test_lambda_boundaries_reproduce_probability_and_lower_bound(self):
        candidate = CandidateProbability("A", calibrated_probability=0.68, calibrated_lower_bound=0.53)
        score0 = rank_candidates([candidate], strategy="UNCERTAINTY_ADJUSTED", lambda_penalty=0.0)[0]
        score1 = rank_candidates([candidate], strategy="UNCERTAINTY_ADJUSTED", lambda_penalty=1.0)[0]
        self.assertAlmostEqual(score0.uncertainty_adjusted_score, 0.68)
        self.assertAlmostEqual(score1.uncertainty_adjusted_score, 0.53)

    def test_soft_uncertainty_does_not_become_structural_rejection(self):
        candidate = CandidateProbability(
            "soft",
            calibrated_probability=0.61,
            calibrated_lower_bound=0.49,
            soft_uncertainties=("LINEUP_UNCERTAINTY",),
        )
        ranked = rank_candidates([candidate], strategy="CALIBRATED_PROBABILITY")
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].governance_class, "SOFT_UNCERTAINTY")
        self.assertTrue(ranked[0].rank_eligible_shadow)

    def test_hard_block_remains_fail_closed(self):
        candidate = CandidateProbability(
            "hard",
            calibrated_probability=0.72,
            calibrated_lower_bound=0.61,
            hard_blockers=("EVENT_ALREADY_STARTED",),
        )
        ranked = rank_candidates([candidate], strategy="CALIBRATED_PROBABILITY")
        self.assertEqual(ranked, [])
        self.assertEqual(governance_classification(("EVENT_ALREADY_STARTED",), ()), "HARD_BLOCK")

    def test_market_divergence_triggers_recheck_without_blending(self):
        diagnostic = market_divergence_diagnostic(0.67, 0.54, threshold=0.08)
        self.assertEqual(diagnostic.status, "RECHECK_INPUTS")
        self.assertAlmostEqual(diagnostic.calibrated_probability, 0.67)
        self.assertAlmostEqual(diagnostic.market_prior_weight, 0.0)
        self.assertFalse(diagnostic.probability_mutated)

    def test_uncertainty_duplicate_consumption_is_diagnostic_only(self):
        audit = uncertainty_consumption_audit(
            [
                UncertaintyConsumption(
                    source="LINEUP_UNCERTAINTY",
                    consumed_by_core_model=True,
                    consumed_by_failure_path=True,
                    consumed_by_bound=True,
                )
            ]
        )
        self.assertTrue(audit["redundancy_review_required"])
        self.assertEqual(audit["duplicate_consumption_sources"], ("LINEUP_UNCERTAINTY",))
        self.assertFalse(audit["probability_mutated"])
        self.assertFalse(audit["production_policy_mutated"])

    def test_sensitivity_attribution_orders_largest_effect_first(self):
        effects = sensitivity_attribution(
            0.62,
            {
                "replacement_starter": 0.54,
                "neutral_weather": 0.615,
                "rested_bullpen": 0.65,
            },
        )
        self.assertEqual(effects[0].factor, "replacement_starter")
        self.assertAlmostEqual(effects[0].delta_percentage_points, -8.0)

    def test_hierarchical_target_shrinks_small_cohort_more_than_large(self):
        small = hierarchical_cohort_target(
            n=5,
            mean_predicted_probability=0.65,
            observed_rate=0.80,
            global_observed_rate=0.55,
            prior_strength=30,
        )
        large = hierarchical_cohort_target(
            n=300,
            mean_predicted_probability=0.65,
            observed_rate=0.80,
            global_observed_rate=0.55,
            prior_strength=30,
        )
        self.assertLess(small.local_weight, large.local_weight)
        self.assertLess(abs(small.shrunk_observed_target - 0.55), abs(large.shrunk_observed_target - 0.55))
        self.assertLess(abs(large.shrunk_observed_target - 0.80), abs(small.shrunk_observed_target - 0.80))

    def test_strategy_replay_can_compare_top_rank_accuracy(self):
        rows = [
            {
                "slate_id": "s1",
                "candidate_id": "A",
                "calibrated_probability": 0.68,
                "calibrated_lower_bound": 0.53,
                "outcome": 1,
            },
            {
                "slate_id": "s1",
                "candidate_id": "B",
                "calibrated_probability": 0.59,
                "calibrated_lower_bound": 0.55,
                "outcome": 0,
            },
            {
                "slate_id": "s2",
                "candidate_id": "C",
                "calibrated_probability": 0.64,
                "calibrated_lower_bound": 0.48,
                "outcome": 1,
            },
            {
                "slate_id": "s2",
                "candidate_id": "D",
                "calibrated_probability": 0.58,
                "calibrated_lower_bound": 0.54,
                "outcome": 0,
            },
        ]
        probability = evaluate_ranking_strategy(rows, strategy="CALIBRATED_PROBABILITY", top_n=1)
        lower_bound = evaluate_ranking_strategy(rows, strategy="CALIBRATED_LOWER_BOUND", top_n=1)
        self.assertEqual(probability.top1_win_rate, 1.0)
        self.assertEqual(lower_bound.top1_win_rate, 0.0)

    def test_challenger_cannot_mutate_production_or_execute(self):
        manifest = challenger_manifest()
        self.assertFalse(CAN_EXECUTE)
        self.assertFalse(AUTOMATIC_PROMOTION_ALLOWED)
        self.assertFalse(PRODUCTION_RANKING_MUTATION_ALLOWED)
        self.assertFalse(PRODUCTION_CALIBRATION_MUTATION_ALLOWED)
        self.assertFalse(PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED)
        self.assertEqual(MARKET_PRIOR_WEIGHT, 0.0)
        self.assertFalse(manifest["can_execute"])
        self.assertEqual(manifest["serving_mode"], "SHADOW_ONLY")


if __name__ == "__main__":
    unittest.main()
