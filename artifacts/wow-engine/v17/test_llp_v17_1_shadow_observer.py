from __future__ import annotations

import unittest

from v17.llp_v17_1_shadow_observer import (
    AUTOMATIC_PROMOTION_ALLOWED,
    CAN_EXECUTE,
    PRODUCTION_CALIBRATION_MUTATION_ALLOWED,
    PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED,
    PRODUCTION_RANKING_MUTATION_ALLOWED,
    TERMINAL_AUTHORITY,
    ShadowObserverError,
    build_lambda_grid_observations,
    build_shadow_observation,
    cohort_key,
    shadow_manifest,
)


def _row() -> dict:
    return {
        "prediction_id": "pred-1",
        "candidate_id": "cand-1",
        "official_event_id": "event-1",
        "sport": "MLB",
        "league": "MLB",
        "selection": "BAL",
        "opponent": "TOR",
        "scheduled_start_utc": "2026-09-22T00:00:00Z",
        "immutable_model_timestamp": "2026-09-21T22:00:00Z",
        "calibrated_probability": 0.64,
        "calibrated_lower_bound": 0.52,
        "calibrated_upper_bound": 0.74,
    }


class TestLLPV171ShadowObserver(unittest.TestCase):
    def test_grid_records_point_middle_and_lower_bound_views(self):
        observations = build_lambda_grid_observations(
            _row(),
            lambdas=(0.0, 0.25, 1.0),
            observed_at="2026-09-21T22:10:00Z",
        )
        self.assertEqual(len(observations), 3)
        self.assertAlmostEqual(observations[0].uncertainty_adjusted_score, 0.64)
        self.assertAlmostEqual(observations[1].uncertainty_adjusted_score, 0.61)
        self.assertAlmostEqual(observations[2].uncertainty_adjusted_score, 0.52)
        self.assertTrue(all(row.calibrated_probability == 0.64 for row in observations))
        self.assertTrue(all(row.calibrated_lower_bound == 0.52 for row in observations))

    def test_soft_uncertainty_remains_shadow_eligible(self):
        observation = build_shadow_observation(
            _row(),
            lambda_penalty=0.25,
            soft_uncertainties=("LINEUP_UNCERTAINTY",),
            observed_at="2026-09-21T22:10:00Z",
        )
        self.assertEqual(observation.governance_class, "SOFT_UNCERTAINTY")
        self.assertTrue(observation.rank_eligible_shadow)

    def test_hard_block_remains_shadow_ineligible(self):
        observation = build_shadow_observation(
            _row(),
            lambda_penalty=0.25,
            hard_blockers=("EVENT_ALREADY_STARTED",),
            observed_at="2026-09-21T22:10:00Z",
        )
        self.assertEqual(observation.governance_class, "HARD_BLOCK")
        self.assertFalse(observation.rank_eligible_shadow)

    def test_market_divergence_is_diagnostic_and_does_not_mutate_probability(self):
        observation = build_shadow_observation(
            _row(),
            lambda_penalty=0.25,
            market_no_vig_probability=0.53,
            divergence_threshold=0.08,
            observed_at="2026-09-21T22:10:00Z",
        )
        self.assertEqual(observation.market_divergence_status, "RECHECK_INPUTS")
        self.assertAlmostEqual(observation.market_divergence, 0.11)
        self.assertEqual(observation.market_prior_weight, 0.0)
        self.assertFalse(observation.probability_mutated)
        self.assertFalse(observation.production_policy_mutated)

    def test_observation_identity_is_deterministic_per_prediction_lambda_schema(self):
        first = build_shadow_observation(
            _row(),
            lambda_penalty=0.25,
            observed_at="2026-09-21T22:10:00Z",
        )
        second = build_shadow_observation(
            _row(),
            lambda_penalty=0.25,
            observed_at="2026-09-21T22:11:00Z",
        )
        self.assertEqual(first.observation_id, second.observation_id)

    def test_cohort_key_buckets_uncertainty_width(self):
        observation = build_shadow_observation(
            _row(),
            lambda_penalty=0.25,
            observed_at="2026-09-21T22:10:00Z",
        )
        self.assertEqual(cohort_key(observation), ("MLB", "MLB", "CLEAR", "WIDTH_10_TO_15PP"))

    def test_identity_fields_are_required(self):
        bad = _row()
        bad.pop("official_event_id")
        with self.assertRaises(ShadowObserverError):
            build_shadow_observation(bad, lambda_penalty=0.25)

    def test_shadow_observer_cannot_promote_mutate_or_execute(self):
        manifest = shadow_manifest()
        self.assertFalse(CAN_EXECUTE)
        self.assertFalse(AUTOMATIC_PROMOTION_ALLOWED)
        self.assertFalse(PRODUCTION_RANKING_MUTATION_ALLOWED)
        self.assertFalse(PRODUCTION_CALIBRATION_MUTATION_ALLOWED)
        self.assertFalse(PRODUCTION_MARKET_PRIOR_MUTATION_ALLOWED)
        self.assertEqual(TERMINAL_AUTHORITY, "V17_TERMINAL_REDUCER")
        self.assertEqual(manifest["serving_mode"], "SHADOW_ONLY")
        self.assertFalse(manifest["can_execute"])


if __name__ == "__main__":
    unittest.main()
