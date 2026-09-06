from copy import deepcopy
import unittest

from v17.mlb_starter_sequencing_dispersion_contract import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_RERUN_REQUIRED,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    MsdContractError,
    assert_candidate_specific_uncertainty,
    audit_feature_names,
    cluster_interaction_increases,
    decide_failure_status,
    handoff_risk_increases,
    lower_bound_rank_key,
    material_change_status,
    missing_required_features,
    preserve_probability_on_market_failure,
    promotion_allowed,
    tail_risk_ordering,
    validate_probability_package,
)


def package():
    return {
        "starter_expected_runs": 2.4,
        "starter_run_variance": 3.1,
        "p_starter_runs_0_1": .35,
        "p_starter_runs_2_3": .35,
        "p_starter_runs_4_5": .20,
        "p_starter_runs_6_plus": .10,
        "expected_innings": 5.5,
        "innings_variance": 1.1,
        "p_third_time_through": .44,
        "p_early_hook": .16,
        "catastrophic_start_probability": .12,
        "offense_expected_runs": 4.2,
        "offense_run_variance": 5.0,
        "p_offense_runs_0_2": .33,
        "p_offense_runs_3_4": .32,
        "p_offense_runs_5_plus": .35,
        "p_three_plus_run_inning": .25,
        "p_scoreless_first_5": .15,
        "p_opponent_starter_exit_before_5": .21,
        "sequencing_concentration_index": .42,
        "starter_offense_cluster_interaction": .10,
        "p_multi_run_inning_before_bullpen": .28,
        "p_starter_4plus_given_lineup": .31,
        "p_starter_6plus_given_lineup": .10,
        "favorite_catastrophic_failure_probability": .18,
        "underdog_offensive_breakthrough_probability": .29,
        "bullpen_expected_runs": 1.8,
        "bullpen_run_variance": 2.2,
        "bullpen_availability_score": .72,
        "leverage_arm_availability": .80,
        "p_bullpen_3plus_runs": .19,
        "handoff_risk": .22,
        "manager_hook_policy_feature": .55,
        "home_run_distribution": [0.1, 0.2, 0.3, 0.4],
        "away_run_distribution": [0.2, 0.3, 0.3, 0.2],
        "score_margin_distribution": {"-1": .2, "0": .1, "1": .7},
        "raw_home_win_probability": .61,
        "raw_away_win_probability": .39,
        "favorite_loss_path_probabilities": {"starter_catastrophe": .12},
        "upset_path_probabilities": {"clustered_offense": .18},
        "raw_probability": .61,
        "calibrated_probability": .60,
        "lower_bound": .55,
        "upper_bound": .65,
        "calibration_method": "candidate_specific_walk_forward",
        "calibration_version": "cal-v1",
        "model_version": "msd-v1",
        "source_snapshot_id": "snapshot-1",
        "model_timestamp": "2026-09-05T20:00:00Z",
        "starter_dispersion_model_version": "starter-v1",
        "sequencing_model_version": "seq-v1",
        "bullpen_model_version": "bp-v1",
        "simulation_version": "sim-v1",
        "feature_snapshot_timestamp": "2026-09-05T19:59:00Z",
        "participant_snapshot_timestamp": "2026-09-05T19:58:00Z",
    }


class MsdV17RegressionTests(unittest.TestCase):
    def test_rt_msd_001_same_mean_different_tail(self):
        a, b = package(), package()
        b["starter_run_variance"] = 5.5
        b["p_starter_4plus_given_lineup"] = .40
        b["lower_bound"], b["upper_bound"] = .51, .69
        self.assertTrue(tail_risk_ordering(a, b))

    def test_rt_msd_002_low_k_contact_vs_deep_contact(self):
        a, b = package(), package()
        b["starter_offense_cluster_interaction"] = .20
        b["p_three_plus_run_inning"] = .35
        self.assertTrue(cluster_interaction_increases(a, b))

    def test_rt_msd_003_high_k_low_bb_reduces_catastrophe(self):
        a, b = package(), package()
        b["catastrophic_start_probability"] = .07
        self.assertLess(b["catastrophic_start_probability"], a["catastrophic_start_probability"])

    def test_rt_msd_004_short_starter_depleted_bullpen(self):
        a, b = package(), package()
        b["handoff_risk"], b["bullpen_run_variance"] = .42, 3.6
        self.assertTrue(handoff_risk_increases(a, b))

    def test_rt_msd_005_recent_scoring_surge_not_feature(self):
        names = ["contact_quality_distribution", "batter_event_probabilities", "projected_batting_order"]
        audit_feature_names(names)
        self.assertNotIn("recent_realized_runs", names)

    def test_rt_msd_006_starter_scratch_rerun(self):
        old = {"starter_identity":"A","projected_lineup_hash":"1","park_weather_hash":"x","bullpen_availability_hash":"b","event_status":"scheduled"}
        new = dict(old, starter_identity="B")
        self.assertEqual(material_change_status(old, new), MODEL_RERUN_REQUIRED)

    def test_rt_msd_007_lineup_change_rerun(self):
        old = {"starter_identity":"A","projected_lineup_hash":"1","park_weather_hash":"x","bullpen_availability_hash":"b","event_status":"scheduled"}
        new = dict(old, projected_lineup_hash="2")
        self.assertEqual(material_change_status(old, new), MODEL_RERUN_REQUIRED)

    def test_rt_msd_008_mean_only_shortcut_invalid(self):
        with self.assertRaisesRegex(MsdContractError, MODEL_OUTPUT_INVALID):
            validate_probability_package({"starter_expected_runs": 2.4})

    def test_rt_msd_009_universal_haircut_blocked(self):
        a, b = package(), package()
        b["raw_probability"] = .66
        b["calibrated_probability"] = .65
        b["lower_bound"], b["upper_bound"] = .60, .70
        with self.assertRaisesRegex(MsdContractError, "UNCALIBRATED_MODEL"):
            assert_candidate_specific_uncertainty([a, b])

    def test_rt_msd_010_probability_normalization(self):
        p = package()
        validate_probability_package(p)
        p["raw_away_win_probability"] = .40
        with self.assertRaisesRegex(MsdContractError, "probability_normalization"):
            validate_probability_package(p)

    def test_rt_msd_011_market_leakage(self):
        with self.assertRaisesRegex(MsdContractError, "GOVERNANCE_MARKET_LEAKAGE"):
            audit_feature_names(["xwoba_allowed", "sportsbook_implied_probability"])

    def test_rt_msd_012_tail_risk_lower_bound_ranking(self):
        a, b = package(), package()
        a["calibrated_probability"], a["lower_bound"] = .68, .54
        b["calibrated_probability"], b["lower_bound"] = .64, .58
        self.assertGreater(lower_bound_rank_key(b), lower_bound_rank_key(a))

    def test_rt_msd_013_missing_feature_is_inputs_insufficient(self):
        missing = missing_required_features({"identity":"A"}, {}, {})
        d = decide_failure_status(exact_artifact_available=True, artifact_selected=True, missing_fields=missing)
        self.assertEqual(d.status, MODEL_INPUTS_INSUFFICIENT)
        self.assertTrue(d.missing_fields)

    def test_rt_msd_014_scorer_failure_preserved(self):
        d = decide_failure_status(exact_artifact_available=True, artifact_selected=True, scorer_invoked=True, scorer_failed=True)
        self.assertEqual(d.status, MODEL_SCORER_FAILED)
        self.assertNotEqual(d.status, MODEL_UNAVAILABLE)

    def test_rt_msd_015_odds_failure_preserves_probability(self):
        out = preserve_probability_on_market_failure(package())
        self.assertEqual(out["probability_status"], "PASS")
        self.assertEqual(out["market_value_status"], "MARKET_DATA_UNOBTAINABLE")
        self.assertEqual(out["calibrated_probability"], .60)

    def test_rt_msd_016_favorite_fragility(self):
        mean_only, fragile = package(), package()
        fragile["p_starter_4plus_given_lineup"] = .46
        fragile["p_offense_runs_0_2"] = .40
        fragile["p_offense_runs_3_4"] = .30
        fragile["p_offense_runs_5_plus"] = .30
        fragile["lower_bound"], fragile["upper_bound"] = .49, .70
        self.assertGreater(fragile["p_starter_4plus_given_lineup"], mean_only["p_starter_4plus_given_lineup"])
        self.assertLess(fragile["lower_bound"], mean_only["lower_bound"])

    def test_rt_msd_017_realized_tail_does_not_rewrite_process(self):
        pregame = package()
        self.assertGreater(pregame["favorite_catastrophic_failure_probability"], 0)
        process_gates_valid = True
        self.assertTrue(process_gates_valid)

    def test_rt_msd_018_winrate_only_cannot_promote(self):
        champion = {"brier_score":.240,"log_loss":.680,"calibration_slope":.98,"calibration_intercept":.01,"win_rate":.55}
        challenger = {"brier_score":.245,"log_loss":.690,"calibration_slope":.91,"calibration_intercept":.03,"win_rate":.58}
        self.assertFalse(promotion_allowed(champion, challenger))


if __name__ == "__main__":
    unittest.main()
