from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260923_v17_mlb_calibration_health_v2_hardening.sql"
)


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def test_only_one_active_ratified_policy_is_allowed():
    sql = _sql()
    assert "uq_wow_mlb_cal_health_v2_one_ratified_policy" in sql
    assert "where status='RATIFIED'" in sql


def test_ratified_policy_thresholds_are_immutable_but_retirement_is_allowed():
    sql = _sql()
    assert "ratified calibration health v2 policy thresholds/rationale are immutable" in sql
    assert "new.status not in ('RATIFIED','RETIRED')" in sql
    assert "retired calibration health v2 policy is immutable" in sql
    assert "policy history is immutable" in sql


def test_standard_binary_calibration_frame_is_home_probability_and_home_outcome():
    sql = _sql()
    assert "g.prediction_probability home_p" in sql
    assert "case when g.official_winner='HOME' then 1 else 0 end home_y" in sql
    assert "home_probability home_p" in sql
    assert "selected_won::int selected_hit" in sql
    assert "avg(selected_hit::double precision)" in sql


def test_small_final_samples_do_not_emit_unstable_shape_or_auc_metrics():
    sql = _sql()
    assert "if v_n>=30 then" in sql
    assert "v_health:='INSUFFICIENT_SAMPLE'" in sql
    # AUC and calibration slope/intercept are only computed inside the n>=30 guard.
    assert sql.index("if v_n>=30 then") < sql.index("select (rank_sum-npos*(npos+1)/2.0)/(npos*nneg)")
    assert sql.index("if v_n>=30 then") < sql.index("v_ci:=0.0")


def test_hardening_does_not_change_probability_or_publication_authority():
    sql = _sql().lower()
    assert "wow_mlb_v2d_assess_calibration_health_v2_shadow" in sql
    assert "update public.wow_mlb_v2d_frozen_spec" not in sql
    assert "update public.wow_event_predictions" not in sql
    assert "probability_publishable=true" not in sql
    assert "can_execute=true" not in sql
