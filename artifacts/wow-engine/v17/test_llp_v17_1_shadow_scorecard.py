from __future__ import annotations

import unittest

from v17.llp_v17_1_shadow_scorecard import (
    compare_to_baseline,
    evaluate_lambda,
    evaluate_shadow_rankings,
    select_ranked_rows,
    strategy_label,
)


def _row(
    *,
    slate: str,
    event: str,
    selection: str,
    p: float,
    lb: float,
    outcome: int,
    lam: float,
    sport: str = "MLB",
    league: str = "MLB",
) -> dict:
    score = p - lam * (p - lb)
    return {
        "requested_slate_date": slate,
        "official_event_id": event,
        "selection": selection,
        "sport": sport,
        "league": league,
        "market_role": "FAVORITE" if p >= 0.5 else "UNDERDOG",
        "governance_class": "CLEAR",
        "calibrated_probability": p,
        "calibrated_lower_bound": lb,
        "lower_bound_width": p - lb,
        "lambda_penalty": lam,
        "uncertainty_adjusted_score": score,
        "rank_eligible_shadow": True,
        "outcome_target": outcome,
    }


def _fixture() -> list[dict]:
    rows: list[dict] = []
    for lam in (0.0, 0.25, 1.0):
        # Slate 1: point ranking prefers A (winner); lower-bound ranking prefers B (loser).
        rows.extend(
            [
                _row(slate="2026-09-20", event="e1", selection="A", p=0.68, lb=0.53, outcome=1, lam=lam),
                _row(slate="2026-09-20", event="e1", selection="A_OPP", p=0.32, lb=0.20, outcome=0, lam=lam),
                _row(slate="2026-09-20", event="e2", selection="B", p=0.59, lb=0.55, outcome=0, lam=lam),
                _row(slate="2026-09-20", event="e2", selection="B_OPP", p=0.41, lb=0.30, outcome=1, lam=lam),
            ]
        )
        # Slate 2: both strategies agree on C as the strongest winner.
        rows.extend(
            [
                _row(slate="2026-09-21", event="e3", selection="C", p=0.70, lb=0.62, outcome=1, lam=lam),
                _row(slate="2026-09-21", event="e3", selection="C_OPP", p=0.30, lb=0.22, outcome=0, lam=lam),
                _row(slate="2026-09-21", event="e4", selection="D", p=0.60, lb=0.50, outcome=1, lam=lam),
                _row(slate="2026-09-21", event="e4", selection="D_OPP", p=0.40, lb=0.31, outcome=0, lam=lam),
            ]
        )
    return rows


class TestLLPV171ShadowScorecard(unittest.TestCase):
    def test_strategy_labels_preserve_objective_separation(self):
        self.assertEqual(strategy_label(0.0), "CALIBRATED_PROBABILITY")
        self.assertEqual(strategy_label(0.25), "UNCERTAINTY_ADJUSTED")
        self.assertEqual(strategy_label(1.0), "CALIBRATED_LOWER_BOUND")

    def test_one_side_per_event_is_selected(self):
        ranked = select_ranked_rows(_fixture(), lambda_penalty=0.0)
        slate1 = [row for row in ranked if row.slate_id == "2026-09-20"]
        self.assertEqual(len(slate1), 2)
        self.assertEqual({row.selection for row in slate1}, {"A", "B"})

    def test_point_and_lower_bound_can_produce_different_top1(self):
        point = select_ranked_rows(_fixture(), lambda_penalty=0.0)
        lower = select_ranked_rows(_fixture(), lambda_penalty=1.0)
        point_slate1 = min((row for row in point if row.slate_id == "2026-09-20"), key=lambda row: row.rank)
        lower_slate1 = min((row for row in lower if row.slate_id == "2026-09-20"), key=lambda row: row.rank)
        self.assertEqual(point_slate1.selection, "A")
        self.assertEqual(lower_slate1.selection, "B")
        self.assertEqual(point_slate1.outcome_target, 1)
        self.assertEqual(lower_slate1.outcome_target, 0)

    def test_point_top1_rate_beats_lower_bound_in_fixture(self):
        point = evaluate_lambda(_fixture(), lambda_penalty=0.0)
        lower = evaluate_lambda(_fixture(), lambda_penalty=1.0)
        self.assertEqual(point.slate_n, 2)
        self.assertAlmostEqual(point.top1_win_rate, 1.0)
        self.assertAlmostEqual(lower.top1_win_rate, 0.5)
        self.assertLessEqual(point.top3_brier, 1.0)
        self.assertGreaterEqual(point.top3_ece, 0.0)

    def test_comparison_counts_corrected_top1_flip(self):
        comparison = compare_to_baseline(_fixture(), lambda_penalty=0.0, baseline_lambda=1.0)
        self.assertEqual(comparison.common_slate_n, 2)
        self.assertEqual(comparison.top1_selection_flips, 1)
        self.assertEqual(comparison.baseline_wrong_to_challenger_correct, 1)
        self.assertEqual(comparison.baseline_correct_to_challenger_wrong, 0)
        self.assertFalse(comparison.automatic_promotion_allowed)
        self.assertFalse(comparison.can_execute)

    def test_full_evaluation_is_advisory_only(self):
        report = evaluate_shadow_rankings(_fixture(), min_cohort_events=1)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["baseline_lambda"], 1.0)
        self.assertEqual(report["lambda_grid"], [0.0, 0.25, 1.0])
        self.assertEqual(report["graded_unique_slate_event_n"], 4)
        self.assertFalse(report["automatic_promotion_allowed"])
        self.assertFalse(report["production_mutation_allowed"])
        self.assertFalse(report["can_execute"])
        self.assertIn("MLB", report["by_sport"])
        self.assertIn("FAVORITE", report["by_market_role"])
        self.assertIn("CLEAR", report["by_governance_class"])

    def test_lambda_and_side_copies_do_not_inflate_cohort_eligibility(self):
        self.assertEqual(len(_fixture()), 24)
        report = evaluate_shadow_rankings(_fixture(), min_cohort_events=5)
        self.assertEqual(report["graded_unique_slate_event_n"], 4)
        self.assertNotIn("MLB", report["by_sport"])

    def test_no_rows_returns_empty_non_promoting_report(self):
        report = evaluate_shadow_rankings([], min_cohort_events=1)
        self.assertEqual(report["status"], "NO_GRADED_SHADOW_ROWS")
        self.assertFalse(report["automatic_promotion_allowed"])
        self.assertFalse(report["can_execute"])


if __name__ == "__main__":
    unittest.main()
