from pathlib import Path


SQL = (
    Path(__file__).resolve().parent
    / "sql"
    / "20260927_fix_mlb_pregame_hydration_state.sql"
).read_text(encoding="utf-8")


def _normalized() -> str:
    return " ".join(SQL.split()).lower()


def test_migration_installs_server_owned_current_pregame_authority():
    sql = _normalized()
    assert "create or replace function public.wow_mlb_current_pregame_status" in sql
    assert "https://statsapi.mlb.com/api/v1.1/game/%s/feed/live" in sql
    assert "pitch_events" in sql
    assert "completed_plays" in sql
    assert "'in progress','final','game over','postponed','cancelled','canceled','suspended'" in sql
    assert "'scheduled','pre-game','delayed start','warmup'" in sql
    assert "revoke all on function public.wow_mlb_current_pregame_status(text) from public, anon, authenticated" in sql
    assert "grant execute on function public.wow_mlb_current_pregame_status(text) to service_role" in sql


def test_migration_rechecks_elapsed_games_before_hydration():
    sql = _normalized()
    assert "e.event_start_time <= clock_timestamp()" in sql
    assert "public.wow_mlb_current_pregame_status(e.official_event_id)" in sql
    assert "current_status_blocked" in sql
    assert "s.captured_at >= clock_timestamp() - interval '24 hours'" in sql
    assert "btrim(coalesce(se.event_status,'')) = '' and se.event_start_time > clock_timestamp()" in sql


def test_migration_removes_scheduled_clock_proxy_from_lineup_confirmation():
    sql = _normalized()
    lineup_start = sql.index("create or replace function public.wow_mlb_forward_confirm_lineup")
    lineup_end = sql.index("create or replace function public.wow_mlb_forward_auto_confirm_lineups")
    lineup = sql[lineup_start:lineup_end]
    assert "if clock_timestamp() >= e.event_start_time" not in lineup
    assert "event_started_during_lineup_fetch" not in lineup
    assert "official_gameplay_already_started" in lineup
    assert "official_pregame_status_unproven" in lineup
    assert "'delayed start'" in lineup


def test_migration_preserves_later_governance_hardening_while_removing_clock_proxy():
    sql = _normalized()
    assert "pg_get_functiondef" in sql
    assert "wow_v17_hydrate_mlb_event_governance_evidence(uuid,uuid,jsonb,text)" in sql
    assert "v_old_clock" in sql
    assert "v_old_states" in sql
    assert "v_new_states" in sql
    assert "'suspended'" in sql


def test_migration_preserves_shadow_only_governance():
    sql = _normalized()
    assert "'probability_publishable',false" in sql
    assert "'can_execute',false" in sql
    assert "update public.wow_mlb_v2d_frozen_spec" not in sql
    assert "production_feature_ready = true" not in sql
