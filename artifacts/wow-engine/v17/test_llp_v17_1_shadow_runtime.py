from __future__ import annotations

import unittest

from v17.llp_v17_1_shadow_runtime import (
    DEFAULT_LAMBDAS,
    build_shadow_grade_rows,
    classify_shadow_reasons,
    event_prediction_side_rows,
    materialize_event_prediction_shadows,
    winner_team,
)


def _event_row() -> dict:
    return {
        "event_prediction_id": "pred-evt-1",
        "research_run_id": "run-1",
        "official_event_id": "event-1",
        "requested_slate_date": "2026-09-21",
        "scan_stage": "FINAL_REFRESH",
        "event_start_time": "2026-09-22T00:00:00Z",
        "sport": "MLB",
        "league": "MLB",
        "home_team": "BAL",
        "away_team": "TOR",
        "controlling_specialist": "MLB_GAME_WIN_PROBABILITY_EXPERT",
        "model_artifact_id": "MLB_V16_V2D_CONTEXT_SHARED_SIM_R1",
        "model_timestamp": "2026-09-21T22:00:00Z",
        "calibrated_home_probability": 0.64,
        "calibrated_home_lower_bound": 0.52,
        "calibrated_home_upper_bound": 0.74,
        "calibrated_away_probability": 0.36,
        "calibrated_away_lower_bound": 0.25,
        "calibrated_away_upper_bound": 0.48,
        "market_prior_home_probability": 0.56,
        "market_prior_away_probability": 0.44,
        "favorite_side": "HOME",
        "blockers": [],
        "terminal_reasons": [],
        "rank_eligibility_reasons": [],
        "data_gaps": [],
        "probability_invalidated": False,
        "rerun_required": False,
        "can_execute": False,
    }


class TestLLPV171ShadowRuntime(unittest.TestCase):
    def test_event_prediction_materializes_both_sides(self):
        rows = event_prediction_side_rows(_event_row())
        self.assertEqual(len(rows), 2)
        home = next(row for row in rows if row["selection"] == "BAL")
        away = next(row for row in rows if row["selection"] == "TOR")
        self.assertEqual(home["candidate_id"], "pred-evt-1:HOME")
        self.assertEqual(away["candidate_id"], "pred-evt-1:AWAY")
        self.assertEqual(home["market_role"], "FAVORITE")
        self.assertEqual(away["market_role"], "UNDERDOG")
        self.assertAlmostEqual(home["calibrated_probability"], 0.64)
        self.assertAlmostEqual(away["calibrated_probability"], 0.36)

    def test_registered_reasons_are_split_hard_and_soft(self):
        source = _event_row()
        source["blockers"] = ["EVENT_ALREADY_STARTED", "LINEUP_UNCERTAINTY", "UNREGISTERED_NOTE"]
        hard, soft = classify_shadow_reasons(source)
        self.assertIn("EVENT_ALREADY_STARTED", hard)
        self.assertIn("LINEUP_UNCERTAINTY", soft)
        self.assertNotIn("UNREGISTERED_NOTE", hard)
        self.assertNotIn("UNREGISTERED_NOTE", soft)

    def test_invalidated_probability_is_hard_blocked(self):
        source = _event_row()
        source["probability_invalidated"] = True
        hard, _ = classify_shadow_reasons(source)
        self.assertIn("STALE_PROBABILITY_AFTER_MATERIAL_UPDATE", hard)

    def test_lambda_grid_materializes_selection_specific_rows(self):
        rows = materialize_event_prediction_shadows(
            _event_row(),
            lambdas=(0.0, 0.25, 1.0),
            observed_at="2026-09-21T22:10:00Z",
        )
        self.assertEqual(len(rows), 6)
        ids = {row["observation_id"] for row in rows}
        self.assertEqual(len(ids), 6)
        self.assertTrue(any("::BAL::0.2500::" in value for value in ids))
        self.assertTrue(any("::TOR::0.2500::" in value for value in ids))
        self.assertTrue(all(row["can_execute"] is False for row in rows))
        self.assertTrue(all(row["production_policy_mutated"] is False for row in rows))

    def test_default_grid_is_five_lambdas_per_side(self):
        rows = materialize_event_prediction_shadows(_event_row(), observed_at="2026-09-21T22:10:00Z")
        self.assertEqual(len(DEFAULT_LAMBDAS), 5)
        self.assertEqual(len(rows), 10)

    def test_winner_team_prefers_final_score(self):
        source = {"home_team": "BAL", "away_team": "TOR"}
        outcome = {"home_score": 5, "away_score": 3, "official_winner": "TOR", "void": False}
        self.assertEqual(winner_team(source, outcome), "BAL")

    def test_void_has_no_winner(self):
        source = {"home_team": "BAL", "away_team": "TOR"}
        outcome = {"home_score": 5, "away_score": 3, "void": True}
        self.assertIsNone(winner_team(source, outcome))

    def test_grade_rows_use_point_probability_and_selection_identity(self):
        observations = [
            {
                "observation_id": "o-bal",
                "selection": "BAL",
                "calibrated_probability": 0.64,
            },
            {
                "observation_id": "o-tor",
                "selection": "TOR",
                "calibrated_probability": 0.36,
            },
        ]
        grades = build_shadow_grade_rows(
            observations,
            winner="BAL",
            settlement_source="OFFICIAL",
            settled_at="2026-09-22T03:00:00Z",
        )
        self.assertEqual(len(grades), 2)
        by_id = {row["observation_id"]: row for row in grades}
        self.assertEqual(by_id["o-bal"]["outcome_target"], 1)
        self.assertEqual(by_id["o-tor"]["outcome_target"], 0)
        self.assertAlmostEqual(by_id["o-bal"]["point_brier"], (0.64 - 1) ** 2)
        self.assertFalse(by_id["o-bal"]["can_execute"])


if __name__ == "__main__":
    unittest.main()
