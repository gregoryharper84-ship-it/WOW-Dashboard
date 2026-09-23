from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260923_v17_mlb_probability_quality_grade_ledgers.sql"
)
SQL = SQL_PATH.read_text(encoding="utf-8")


def test_grade_ledger_has_explicit_early_final_and_dominance_kinds():
    assert "EARLY_PREGAME_MODEL_GRADE" in SQL
    assert "FINAL_PREGAME_PUBLISHED_GRADE" in SQL
    assert "DOMINANCE_DIAGNOSTIC" in SQL
    assert "unique (grade_kind, official_event_id)" in SQL


def test_grade_ledger_is_immutable_and_non_executable():
    assert "WOW_MLB_V17_GRADE_LEDGER_IMMUTABLE" in SQL
    assert "before update or delete" in SQL.lower()
    assert "can_execute boolean not null default false check (can_execute = false)" in SQL
    assert "'can_execute',false" in SQL


def test_final_grade_selects_latest_publishable_refresh_before_first_pitch():
    assert "ep.probability_publishable=true" in SQL
    assert "ep.final_refresh_status='PASS'" in SQL
    assert "ep.final_refresh_timestamp<ep.event_start_time" in SQL
    assert "order by ep.final_refresh_timestamp desc,ep.event_prediction_id::text desc" in SQL


def test_dominance_diagnostic_is_research_only_and_does_not_rewrite_probability():
    assert "WOW_V17_MLB_DOMINANCE_DIAGNOSTIC_V1" in SQL
    assert "'win_by_2_plus_probability'" in SQL
    assert "'win_by_4_plus_probability'" in SQL
    assert "'win_by_6_plus_probability'" in SQL
    assert "'loss_by_4_plus_probability'" in SQL
    assert "'research_only',true" in SQL
    assert "'probability_publishable',false" in SQL


def test_grade_ledger_data_api_surface_is_closed_and_fk_indexes_exist():
    assert "enable row level security" in SQL.lower()
    assert "revoke all on table public.wow_mlb_v17_prediction_grade_ledger from anon, authenticated" in SQL
    assert "idx_wow_mlb_v17_grade_ledger_event_prediction" in SQL
    assert "idx_wow_mlb_v17_grade_ledger_score_snapshot" in SQL
    assert "idx_wow_mlb_v17_grade_ledger_shadow_grade" in SQL
