import math
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from v17.mlb_game_winner_shadow_evaluation import (
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    EvaluationEvidenceError,
    EvidenceRow,
    HISTORICAL_V2A_FEATURES,
    RETROSPECTIVE_PROVENANCE,
    SERVING_MODE,
    TIMESTAMPED_PREGAME_PROVENANCE,
    chronological_split,
    evaluate_forward_shadow,
    evaluate_retrospective_challenger,
    materialize_forward_game_features,
    materialize_historical_game_features,
    validate_evidence_row,
)


FORWARD_NAMES = (
    "is_home", "min_team_prior_games", "off_bb_pg", "off_cs_pg",
    "off_days_rest", "off_hits_pg", "off_hr_pg", "off_run_diff_pg",
    "off_runs_pg", "off_sb_pg", "off_so_pg", "off_tb_pg", "off_win_rate",
    "opp_bp_apps_3d", "opp_bp_bb_rate", "opp_bp_era", "opp_bp_hr_rate",
    "opp_bp_k_rate", "opp_bp_outs_3d", "opp_bp_pitches_3d", "opp_days_rest",
    "opp_errors_pg", "opp_runs_allowed_pg", "opp_starter_bb_rate",
    "opp_starter_days_rest", "opp_starter_era", "opp_starter_h_rate",
    "opp_starter_hr_rate", "opp_starter_k_rate", "opp_starter_outs_per_start",
    "opp_starter_pitches_last3", "opp_starter_pitches_per_start",
    "opp_starter_prior_starts", "opp_starter_strike_rate",
    "opp_starter_tbf_per_start", "opp_win_rate", "park_prior_games",
    "park_total_runs_prior",
)


def forward_vector(**overrides):
    values = {name: 1.0 for name in FORWARD_NAMES}
    values.update({"park_prior_games": 40.0, "park_total_runs_prior": 8.7})
    values.update(overrides)
    return [values[name] for name in FORWARD_NAMES]


def historical_row(seed):
    rng = np.random.default_rng(seed)
    return {name: float(rng.normal()) for name in HISTORICAL_V2A_FEATURES}


def evidence_rows(n=120, seed=23):
    rng = np.random.default_rng(seed)
    start = datetime(2024, 4, 1, 18, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        features = historical_row(seed + i)
        signal = (
            0.35 * features["run_diff_pg_diff"]
            - 0.28 * features["starter_era_diff"]
            - 0.18 * features["bp_era_diff"]
            + 0.20 * features["win_rate_diff"]
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
    def test_historical_vector_contract_is_exact(self):
        values = [float(i) for i in range(len(HISTORICAL_V2A_FEATURES))]
        row = materialize_historical_game_features(HISTORICAL_V2A_FEATURES, values)
        self.assertEqual(tuple(row), HISTORICAL_V2A_FEATURES)
        self.assertEqual(row[HISTORICAL_V2A_FEATURES[-1]], values[-1])

    def test_market_or_postgame_features_are_rejected(self):
        with self.assertRaisesRegex(EvaluationEvidenceError, "GOVERNANCE_MARKET_LEAKAGE"):
            materialize_historical_game_features(
                list(HISTORICAL_V2A_FEATURES) + ["sportsbook_implied_probability"],
                [0.0] * (len(HISTORICAL_V2A_FEATURES) + 1),
            )
        row = EvidenceRow(
            event_id="x",
            event_start_time=datetime(2026, 9, 6, 18, tzinfo=timezone.utc),
            feature_row={"actual_outcome": 1.0},
            home_win=True,
            provenance_status=RETROSPECTIVE_PROVENANCE,
        )
        with self.assertRaisesRegex(EvaluationEvidenceError, "GOVERNANCE_POSTGAME_LEAKAGE"):
            validate_evidence_row(row)

    def test_forward_side_materialization_preserves_home_minus_away_semantics(self):
        home = forward_vector(
            off_runs_pg=5.2,
            off_hits_pg=9.0,
            off_days_rest=2.0,
            opp_runs_allowed_pg=4.1,  # away team's runs allowed
            opp_bp_era=4.5,           # away bullpen
            opp_starter_era=4.2,      # away starter
            opp_starter_prior_starts=8.0,
            min_team_prior_games=18.0,
        )
        away = forward_vector(
            off_runs_pg=4.4,
            off_hits_pg=7.5,
            off_days_rest=1.0,
            opp_runs_allowed_pg=3.7,  # home team's runs allowed
            opp_bp_era=3.6,           # home bullpen
            opp_starter_era=3.3,      # home starter
            opp_starter_prior_starts=12.0,
            min_team_prior_games=20.0,
        )
        row = materialize_forward_game_features(FORWARD_NAMES, home, FORWARD_NAMES, away)
        self.assertAlmostEqual(row["runs_pg_diff"], 0.8)
        self.assertAlmostEqual(row["hits_pg_diff"], 1.5)
        self.assertAlmostEqual(row["team_rest_diff"], 1.0)
        self.assertAlmostEqual(row["runs_allowed_pg_diff"], -0.4)
        self.assertAlmostEqual(row["bp_era_diff"], -0.9)
        self.assertAlmostEqual(row["starter_era_diff"], -0.9)
        self.assertEqual(row["starter_min_prior_starts"], 8.0)
        self.assertEqual(row["team_min_prior_games"], 18.0)

    def test_shared_park_context_conflict_fails_closed_for_evidence(self):
        home = forward_vector(park_total_runs_prior=8.7)
        away = forward_vector(park_total_runs_prior=9.1)
        with self.assertRaisesRegex(EvaluationEvidenceError, "MODEL_INPUTS_CONFLICT"):
            materialize_forward_game_features(FORWARD_NAMES, home, FORWARD_NAMES, away)

    def test_timestamped_forward_row_rejects_postgame_feature_capture(self):
        event_start = datetime(2026, 9, 6, 18, tzinfo=timezone.utc)
        row = EvidenceRow(
            event_id="late",
            event_start_time=event_start,
            feature_row=historical_row(7),
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
                    feature_row=historical_row(300 + i),
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
