from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260923_v17_mlb_calibration_health_v2_shadow.sql"
)


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def test_v2_is_parallel_shadow_not_legacy_health_rewrite():
    sql = _sql()
    assert "wow_mlb_v2d_calibration_health_v2_shadow" in sql
    assert "wow_mlb_v2d_calibration_health_v2_policy_shadow" in sql
    assert "update public.wow_mlb_v2d_calibration_health set" not in sql.lower()
    assert "legacy_health_status" in sql


def test_v2_pass_requires_explicit_ratified_policy():
    sql = _sql()
    assert "where status='RATIFIED'" in sql
    assert "QUANTITATIVE_CALIBRATION_POLICY_UNRATIFIED" in sql
    assert "case when cardinality(v_blockers)=0 then 'PASS' else 'REVIEW_REQUIRED' end" in sql
    # A RATIFIED policy must define every quantitative calibration gate.
    for token in (
        "max_brier",
        "max_log_loss",
        "max_ece",
        "max_bin_gap",
        "max_abs_calibration_intercept",
        "min_calibration_slope",
        "max_calibration_slope",
    ):
        assert token in sql


def test_v2_keeps_early_and_final_cohorts_separate():
    sql = _sql()
    assert "EARLY_PREGAME_MODEL_GRADE" in sql
    assert "FINAL_PREGAME_PUBLISHED_GRADE" in sql
    assert "wow_mlb_v17_prediction_grade_ledger" in sql
    assert "wow_mlb_forward_shadow_grades" in sql


def test_v2_corrects_legacy_home_probability_name_without_rewriting_history():
    sql = _sql()
    # Legacy grade writer stored HOME probability even for AWAY-selected rows.
    # V2 derives selected-side probability from predicted_side instead.
    assert "when g.predicted_side='HOME' then g.prediction_probability" in sql
    assert "else 1.0-g.prediction_probability" in sql


def test_v2_measures_proper_scores_calibration_and_discrimination():
    sql = _sql()
    for token in (
        "brier",
        "log_loss",
        "ece_equal_count",
        "max_equal_count_gap",
        "calibration_intercept",
        "calibration_slope",
        "roc_auc",
        "selected_side_hit_rate",
        "probability_stddev",
    ):
        assert token in sql


def test_v2_is_immutable_internal_only_and_non_executable():
    sql = _sql().lower()
    assert "is immutable" in sql
    assert "before update" in sql
    assert "before delete" in sql
    assert "enable row level security" in sql
    assert "revoke all" in sql
    assert "from anon, authenticated" in sql
    assert "check (can_execute=false)" in sql
    assert "probability_publishable=false" in sql
    assert "can_execute',false" in sql


def test_v2_has_fk_covering_index_for_policy_version():
    sql = _sql()
    assert "idx_wow_mlb_cal_health_v2_policy_version" in sql
    assert "on public.wow_mlb_v2d_calibration_health_v2_shadow(policy_version)" in sql
