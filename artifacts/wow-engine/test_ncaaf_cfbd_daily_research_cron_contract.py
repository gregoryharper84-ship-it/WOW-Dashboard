"""Research-only NCAAF daily outcome refresh must not change terminal authority."""
from pathlib import Path

SQL = Path(__file__).with_name(
    "ncaaf_cfbd_daily_outcome_research_cron_v1.sql"
).read_text(encoding="utf-8").lower()


def test_daily_job_is_named_once_and_official_cfbd_only():
    assert "'wow-v17-ncaaf-research-outcomes-daily'" in SQL
    assert "'45 14 * * *'" in SQL
    assert "select cron.schedule(" in SQL
    assert "s.provider='cfbd'" in SQL
    assert "s.endpoint='/games/players'" in SQL
    assert "s.acquisition_status='available'" in SQL
    assert "s.can_execute=false" in SQL
    assert "not exists (" in SQL
    assert "limit 1" in SQL
    assert "normalize_ncaaf_player_week(" in SQL


def test_private_default_invoker_does_not_expose_data_or_model():
    assert "security invoker" in SQL
    assert "set search_path = ''" in SQL
    assert "revoke all on function wow_prop_history.refresh_next_ncaaf_outcome_week()" in SQL
    assert "from public, anon, authenticated" in SQL
    assert "grant execute on function wow_prop_history.refresh_next_ncaaf_outcome_week()" in SQL
    assert "to service_role" in SQL
    assert "'no_pending_player_weeks'" in SQL
    assert "'research_outcome_week_updated'" in SQL
    assert "'probability_publishable',false" in SQL
    assert "'model_training_eligible',false" in SQL
    assert "'can_execute',false" in SQL
    assert "on conflict" not in SQL  # Ingest/projection authority remains in certified source function.
