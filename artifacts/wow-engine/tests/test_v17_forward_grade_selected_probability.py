from __future__ import annotations

import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "v17" / "sql" / "20260921_v17_mlb_forward_grade_selected_probability.sql"


def _old_home_oriented_metrics(p_home: float, home_win: bool) -> tuple[float, float]:
    y_home = 1.0 if home_win else 0.0
    brier = (y_home - p_home) ** 2
    logloss = -(y_home * math.log(p_home) + (1.0 - y_home) * math.log(1.0 - p_home))
    return brier, logloss


def _selected_side_metrics(p_home: float, home_win: bool) -> tuple[str, float, bool, float, float]:
    p_away = 1.0 - p_home
    predicted_side = "HOME" if p_home >= p_away else "AWAY"
    p_selected = p_home if predicted_side == "HOME" else p_away
    actual_outcome = home_win if predicted_side == "HOME" else not home_win
    y_selected = 1.0 if actual_outcome else 0.0
    brier = (y_selected - p_selected) ** 2
    logloss = -(y_selected * math.log(p_selected) + (1.0 - y_selected) * math.log(1.0 - p_selected))
    return predicted_side, p_selected, actual_outcome, brier, logloss


def test_selected_side_metrics_are_binary_equivalent_to_existing_proper_scoring():
    for p_home in (0.41, 0.49, 0.50, 0.53, 0.61):
        for home_win in (False, True):
            old_brier, old_logloss = _old_home_oriented_metrics(p_home, home_win)
            _, _, _, new_brier, new_logloss = _selected_side_metrics(p_home, home_win)
            assert math.isclose(old_brier, new_brier, rel_tol=0.0, abs_tol=1e-15)
            assert math.isclose(old_logloss, new_logloss, rel_tol=0.0, abs_tol=1e-15)


def test_away_selection_persists_away_probability_semantics():
    side, p_selected, actual, _, _ = _selected_side_metrics(0.46, home_win=False)
    assert side == "AWAY"
    assert math.isclose(p_selected, 0.54)
    assert actual is True


def test_migration_repairs_future_write_and_preserves_immutable_history():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "v_prediction_probability := case" in sql
    assert "when v_predicted_side = 'HOME' then s.calibrated_home_probability" in sql
    assert "else s.calibrated_away_probability" in sql
    assert "v_prediction_probability,\n    v_predicted_side" in sql
    assert "wow_mlb_forward_shadow_grades_selected_side_v" in sql
    assert "selected_side_probability" in sql
    assert "stored_prediction_probability_matches_side" in sql
    assert "historical grade rows are never rewritten" in sql.lower()


def test_migration_preserves_execution_invariant():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "'can_execute',false" in sql
    assert "update public.wow_mlb_forward_shadow_grades" not in sql.lower()
