import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from v17.mlb_probability_quality import (
    CalibrationHealthPolicy,
    CalibrationRow,
    ImmutableGradeRow,
    assess_calibration_health,
    canonicalize_early_grades,
    champion_intercept_map,
    compute_calibration_metrics,
    dominance_from_score_pmfs,
    temporal_calibration_challenge,
)


def _rows(n=600):
    # Deterministic underconfident probabilities: true logit slope is ~2.
    p = np.linspace(0.40, 0.60, n)
    q = 1.0 / (1.0 + np.exp(-2.0 * np.log(p / (1.0 - p))))
    # Deterministic pseudo-outcome sequence approximating q without randomness.
    y = np.asarray([((i * 37) % 1000) / 1000.0 < q[i] for i in range(n)], dtype=bool)
    start = datetime(2024, 8, 10, tzinfo=timezone.utc)
    return [
        CalibrationRow(
            event_id=f"e-{i:04d}",
            event_time=(start + timedelta(hours=i)).isoformat().replace("+00:00", "Z"),
            raw_home_probability=float(p[i]),
            home_win=bool(y[i]),
        )
        for i in range(n)
    ]


def test_canonicalize_early_grades_preserves_one_earliest_row_per_event():
    rows = [
        ImmutableGradeRow("game-1", "2026-08-29T01:00:00Z", "b", "NOT_YET_AVAILABLE", 0.54, True, 7),
        ImmutableGradeRow("game-1", "2026-08-28T01:00:00Z", "a", "NOT_YET_AVAILABLE", 0.52, False, -2),
        ImmutableGradeRow("game-2", "2026-08-28T02:00:00Z", "c", "CONFIRMED", 0.58, True, 3),
    ]
    canonical = canonicalize_early_grades(rows)
    assert len(canonical) == 2
    assert {row.score_snapshot_id for row in canonical} == {"a", "c"}


def test_health_pass_depends_on_quantitative_quality_not_sample_count_only():
    p = np.linspace(0.45, 0.55, 200)
    y = np.asarray([i % 2 == 0 for i in range(200)])
    metrics = compute_calibration_metrics(p, y)
    strict = CalibrationHealthPolicy(
        max_brier=0.20,
        max_log_loss=0.60,
        max_ece=0.03,
        max_bin_gap=0.08,
        max_abs_calibration_intercept=0.20,
        min_calibration_slope=0.80,
        max_calibration_slope=1.20,
        min_n=30,
    )
    result = assess_calibration_health(metrics, strict)
    assert result.status == "REVIEW_REQUIRED"
    assert result.blockers
    assert result.can_execute is False
    assert result.probability_publishable is False


def test_temporal_challenge_never_uses_test_for_selection_and_remains_challenger_only():
    result = temporal_calibration_challenge(_rows(), incumbent_intercept_shift=0.105599310461368)
    assert result.total_n == 600
    assert result.fit_n == 360
    assert result.selection_n == 120
    assert result.untouched_test_n == 120
    assert result.test_used_for_selection is False
    assert result.automatic_promotion is False
    assert result.probability_publishable is False
    assert result.can_execute is False
    methods = {item.method for item in result.untouched_test_metrics}
    assert "INCUMBENT_LOGIT_INTERCEPT_PREVALENCE_V1" in methods
    assert "PLATT_LOGIT_AFFINE_V1" in methods
    assert "BETA_CALIBRATION_V1" in methods
    assert "ISOTONIC_MONOTONE_V1" in methods


def test_champion_intercept_mapping_is_normalized_and_changes_only_calibration_mapping():
    p = np.asarray([0.45, 0.50, 0.55])
    mapped = champion_intercept_map(p, 0.105599310461368)
    assert np.all(mapped > 0)
    assert np.all(mapped < 1)
    assert np.all(np.diff(mapped) > 0)


def test_dominance_diagnostic_reports_margin_tails_without_replacing_win_probability():
    home = [0.10, 0.20, 0.35, 0.20, 0.10, 0.05]
    away = [0.20, 0.30, 0.25, 0.15, 0.07, 0.03]
    diag = dominance_from_score_pmfs(home, away, selected_side="HOME", extra_inning_selected_win_probability=0.53)
    assert math.isclose(diag.probability_sum, 1.0, abs_tol=1e-9)
    assert diag.tie_after_9_probability > 0
    assert diag.outright_win_probability >= diag.regulation_win_probability
    assert 0 <= diag.win_by_6_plus_probability <= diag.win_by_4_plus_probability <= diag.win_by_2_plus_probability <= diag.regulation_win_probability <= 1
    assert -1 <= diag.blowout_asymmetry_score <= 1
    assert diag.can_execute is False


def test_dominance_rejects_non_normalized_pmf():
    with pytest.raises(Exception):
        dominance_from_score_pmfs([0.2, 0.2], [0.5, 0.5], selected_side="HOME")


def test_temporal_challenge_rejects_non_chronological_rows():
    rows = list(_rows(180))
    rows[0], rows[1] = rows[1], rows[0]
    with pytest.raises(Exception) as exc:
        temporal_calibration_challenge(rows, incumbent_intercept_shift=0.105599310461368)
    assert getattr(exc.value, "code", None) == "MLB_CHALLENGER_ROWS_NOT_CHRONOLOGICAL"
