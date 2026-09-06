import math
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from v17.mlb_game_winner_shadow_challenger import ShadowChallengerError
from v17.mlb_game_winner_shadow_evaluation import (
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    EvaluationEvidenceError,
    EvidenceRow,
    PAIRED_RUN_GAME_FEATURES,
    PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION,
    RETROSPECTIVE_PROVENANCE,
    RUN_SIDE_FEATURES,
    SERVING_MODE,
    TIMESTAMPED_PREGAME_PROVENANCE,
    chronological_split,
    evaluate_forward_shadow,
    evaluate_retrospective_challenger,
    materialize_paired_run_game_features,
    validate_evidence_row,
)


def side_vector(*, is_home, **overrides):
    values = {name: 1.0 for name in RUN_SIDE_FEATURES}
    values.update(
        {
            "is_home": 1.0 if is_home else 0.0,
            "park_prior_games": 65.0,
            "park_total_runs_prior": 9.1,
            "min_team_prior_games": 134.0,
        }
    )
    values.update(overrides)
    return [values[name] for name in RUN_SIDE_FEATURES]


def paired_feature_row(seed):
    rng = np.random.default_rng(seed)
    return {name: float(rng.normal()) for name in PAIRED_RUN_GAME_FEATURES}


def evidence_rows(n=120, seed=23):
    rng = np.random.default_rng(seed)
    start = datetime(2024, 4, 1, 18, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        features = paired_feature_row(seed + i)
        signal = (
            0.36 * features["off_run_diff_pg_home_minus_away"]
            - 0.27 * features["opp_starter_era_home_entity_minus_away_entity"]
            - 0.18 * features["opp_bp_era_home_entity_minus_away_entity"]
            + 0.21 * features["off_win_rate_home_minus_away"]
        )
        p = 1.0 / (1.0 + math.exp(-signal))
        home_win = bool(rng.random() < p)
        event_start = start + timedelta(days=i)
        rows.append(
            EvidenceRow(
                event_id=f"event-{i}",
                event_start_time=event_start,
                feature_row=features,
                home_win=home_win,
                provenance_status=RETROSPECTIVE_PROVENANCE,
                champion_home_probability=min(0.95, max(0.05, 0.50 + 0.04 * math.tanh(signal))),
            )
        )
    return rows


class GameWinnerShadowEvaluationTests(unittest.TestCase):
    def test_paired_contract_uses_exact_shared_historical_forward_source_schema(self):
        self.assertEqual(len(RUN_SIDE_FEATURES), 38)
        self.assertEqual(PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION, "MLB_GAME_WIN_PAIRED_RUN_FEATURES_V1")
        self.assertNotIn("is_home", PAIRED_RUN_GAME_FEATURES)

    def test_pair_materialization_preserves_home_relative_semantics(self):
        home = side_vector(
            is_home=True,
            off_runs_pg=5.2,
            off_hits_pg=9.0,
            off_days_rest=2.0,
            opp_runs_allowed_pg=4.1,
            opp_bp_era=4.5,
            opp_starter_era=4.2,
        )
        away = side_vector(
            is_home=False,
            off_runs_pg=4.4,
            off_hits_pg=7.5,
            off_days_rest=1.0,
            opp_runs_allowed_pg=3.7,
            opp_bp_era=3.6,
            opp_starter_era=3.3,
        )
        row = materialize_paired_run_game_features(RUN_SIDE_FEATURES, home, RUN_SIDE_FEATURES, away)
        self.assertEqual(tuple(row), PAIRED_RUN_GAME_FEATURES)
        self.assertAlmostEqual(row["off_runs_pg_home_minus_away"], 0.8)
        self.assertAlmostEqual(row["off_hits_pg_home_minus_away"], 1.5)
        self.assertAlmostEqual(row["off_days_rest_home_minus_away"], 1.0)
        self.assertAlmostEqual(row["opp_runs_allowed_pg_home_entity_minus_away_entity"], -0.4)
        self.assertAlmostEqual(row["opp_bp_era_home_entity_minus_away_entity"], -0.9)
        self.assertAlmostEqual(row["opp_starter_era_home_entity_minus_away_entity"], -0.9)

    def test_current_source_semantics_are_not_silently_capped_to_old_v2a_game_vector(self):
        home = side_vector(is_home=True, min_team_prior_games=134.0, park_prior_games=65.0)
        away = side_vector(is_home=False, min_team_prior_games=134.0, park_prior_games=65.0)
        row = materialize_paired_run_game_features(RUN_SIDE_FEATURES, home, RUN_SIDE_FEATURES, away)
        self.assertEqual(row["min_team_prior_games"], 134.0)
        self.assertEqual(row["park_prior_games"], 65.0)

    def test_side_identity_mismatch_fails_closed(self):
        home = side_vector(is_home=False)
        away = side_vector(is_home=False)
        with self.assertRaisesRegex(EvaluationEvidenceError, "home_away_side_identity_mismatch"):
            materialize_paired_run_game_features(RUN_SIDE_FEATURES, home, RUN_SIDE_FEATURES, away)

    def test_shared_context_conflict_fails_closed_for_evidence(self):
        home = side_vector(is_home=True, park_total_runs_prior=8.7)
        away = side_vector(is_home=False, park_total_runs_prior=9.1)
        with self.assertRaisesRegex(EvaluationEvidenceError, "MODEL_INPUTS_CONFLICT"):
            materialize_paired_run_game_features(RUN_SIDE_FEATURES, home, RUN_SIDE_FEATURES, away)

    def test_market_source_feature_is_rejected(self):
        names = list(RUN_SIDE_FEATURES) + ["sportsbook_implied_probability"]
        home = side_vector(is_home=True) + [0.55]
        away = side_vector(is_home=False) + [0.45]
        with self.assertRaisesRegex(ShadowChallengerError, "GOVERNANCE_MARKET_LEAKAGE"):
            materialize_paired_run_game_features(names, home, names, away)

    def test_timestamped_forward_row_rejects_postgame_feature_capture(self):
        event_start = datetime(2026, 9, 6, 18, tzinfo=timezone.utc)
        row = EvidenceRow(
            event_id="late",
            event_start_time=event_start,
            feature_row=paired_feature_row(7),
            home_win=True,
            provenance_status=TIMESTAMPED_PREGAME_PROVENANCE,
            feature_timestamp=event_start + timedelta(seconds=1),
            outcome_timestamp=event_start + timedelta(hours=3),
            champion_home_probability=0.55,
        )
        with self.assertRaisesRegex(EvaluationEvidenceError, "GOVERNANCE_POSTGAME_LEAKAGE"):
            validate_evidence_row(row)

    def test_chronological_split_rejects_duplicate_events(self):
        rows = evidence_rows(12)
        duplicate = EvidenceRow(**{**rows[-1].__dict__, "event_start_time": rows[-1].event_start_time + timedelta(hours=1)})
        with self.assertRaisesRegex(EvaluationEvidenceError, "duplicate_event_id"):
            chronological_split(
                rows + [duplicate],
                train_end=rows[5].event_start_time,
                calibration_end=rows[8].event_start_time,
            )

    def test_retrospective_evaluation_is_shadow_only_and_never_promotes(self):
        rows = evidence_rows(120)
        split = chronological_split(
            rows,
            train_end=rows[69].event_start_time,
            calibration_end=rows[94].event_start_time,
        )
        report = evaluate_retrospective_challenger(
            split,
            min_train=60,
            min_calibration=20,
            min_holdout=20,
            bootstrap_models=4,
            seed=1706,
        )
        self.assertEqual(report["retrospective_evidence_status"], "RETROSPECTIVE_OOS_COMPLETE")
        self.assertEqual(report["pristine_forward_evidence_status"], "FORWARD_CHALLENGER_SCORES_REQUIRED")
        self.assertEqual(report["feature_schema_version"], PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION)
        self.assertEqual(report["serving_mode"], "SHADOW_ONLY")
        self.assertFalse(report["automatic_promotion"])
        self.assertFalse(report["admission_policy_mutated"])
        self.assertFalse(report["cash_single_gate_mutated"])
        self.assertFalse(report["can_execute"])

    def test_insufficient_retrospective_evidence_is_typed(self):
        rows = evidence_rows(30)
        split = chronological_split(
            rows,
            train_end=rows[14].event_start_time,
            calibration_end=rows[21].event_start_time,
        )
        with self.assertRaisesRegex(EvaluationEvidenceError, "INSUFFICIENT_OOS_EVIDENCE"):
            evaluate_retrospective_challenger(split, bootstrap_models=0)

    def test_forward_comparison_requires_timestamped_pregame_provenance(self):
        rows = evidence_rows(10)
        with self.assertRaisesRegex(EvaluationEvidenceError, "TEMPORAL_PROVENANCE_INSUFFICIENT"):
            evaluate_forward_shadow(rows, [0.55] * 10, min_forward=5)

    def test_forward_comparison_never_mutates_serving_or_pick_policy(self):
        start = datetime(2026, 8, 28, 18, tzinfo=timezone.utc)
        rows = []
        challenger = []
        for i in range(40):
            y = i % 2 == 0
            event_start = start + timedelta(hours=4 * i)
            rows.append(
                EvidenceRow(
                    event_id=f"forward-{i}",
                    event_start_time=event_start,
                    feature_row=paired_feature_row(300 + i),
                    home_win=y,
                    provenance_status=TIMESTAMPED_PREGAME_PROVENANCE,
                    feature_timestamp=event_start - timedelta(hours=1),
                    outcome_timestamp=event_start + timedelta(hours=3),
                    champion_home_probability=0.51 if y else 0.49,
                )
            )
            challenger.append(0.58 if y else 0.42)
        report = evaluate_forward_shadow(rows, challenger, min_forward=20)
        self.assertEqual(report["pristine_forward_evidence_status"], "FORWARD_OOS_COMPLETE")
        self.assertEqual(report["serving_mode"], SERVING_MODE)
        self.assertFalse(report["automatic_promotion"])
        self.assertFalse(report["admission_policy_mutated"])
        self.assertFalse(report["cash_single_gate_mutated"])
        self.assertFalse(report["can_execute"])

    def test_module_invariants_remain_fail_safe(self):
        self.assertFalse(AUTOMATIC_PROMOTION)
        self.assertFalse(CAN_EXECUTE)
        self.assertEqual(SERVING_MODE, "SHADOW_ONLY")


if __name__ == "__main__":
    unittest.main()
