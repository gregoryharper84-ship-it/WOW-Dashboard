from pathlib import Path


SQL_PATH = Path(__file__).with_name("forward_shadow_auto_hydrator.sql")


def _sql() -> str:
    return " ".join(SQL_PATH.read_text().split()).lower()


def test_auto_hydrator_selects_only_fresh_authoritatively_pregame_snapshot():
    sql = _sql()
    assert "s.captured_at >= clock_timestamp() - interval '24 hours'" in sql
    assert "unchanged pregame identity" in sql
    assert "'scheduled','pre-game','pregame','delayed start','warmup'" in sql
    assert "btrim(coalesce(se.event_status,'')) = '' and se.event_start_time > clock_timestamp()" in sql
    assert "order by s.captured_at desc limit 1" in sql
    assert "where snapshot_id=v_snapshot_id" in sql


def test_auto_hydrator_rechecks_official_status_after_nominal_start():
    sql = _sql()
    assert "create or replace function public.wow_mlb_current_pregame_status" in sql
    assert "https://statsapi.mlb.com/api/v1.1/game/%s/feed/live" in sql
    assert "e.event_start_time <= clock_timestamp()" in sql
    assert "public.wow_mlb_current_pregame_status(e.official_event_id)" in sql
    assert "current_status_blocked" in sql
    assert "event_not_pregame" in sql
    assert "event_pregame_status_unproven" in sql
    assert "pitch_events" in sql
    assert "completed_plays" in sql


def test_current_status_helper_is_server_only_and_execution_disabled():
    sql = _sql()
    assert "revoke all on function public.wow_mlb_current_pregame_status(text) from public, anon, authenticated" in sql
    assert "grant execute on function public.wow_mlb_current_pregame_status(text) to service_role" in sql
    assert "'can_execute',false" in sql


def test_auto_hydrator_keeps_delayed_and_pregame_rows_after_nominal_start():
    sql = _sql()
    assert sql.count("'delayed start'") >= 4
    assert sql.count("'pre-game'") >= 4
    assert sql.count("'warmup'") >= 4
    assert "'in progress'" in sql
    assert "'final'" in sql
    assert "'game over'" in sql


def test_auto_hydrator_freezes_required_2026_prior_day_schedule_context():
    sql = _sql()
    assert "unsupported_frozen_feature_season" in sql
    assert "mlb_schedule_season_to_date" in sql
    assert "startdate=03/25/2026" in sql
    assert "enddate=%s" in sql
    assert "to_char(v_slate_date - 1, 'mm/dd/yyyy')" in sql
    assert "wow_mlb_forward_cache_url" in sql
    assert "wow_mlb_forward_materialize_schedule" in sql
    assert "schedule_context_ready" in sql
    assert "gametype=r" not in sql


def test_auto_hydrator_reuses_existing_governed_feature_and_model_paths():
    sql = _sql()
    assert "wow_mlb_capture_recent_bullpen_workload" in sql
    assert "wow_mlb_forward_cache_event_inputs" in sql
    assert "wow_mlb_forward_build_side_features" in sql
    assert "wow_mlb_forward_score_event" in sql
    assert "home_probable_pitcher_id is not null" in sql
    assert "away_probable_pitcher_id is not null" in sql
    assert "delayed_starter_unresolved" in sql


def test_auto_hydrator_remains_fail_closed():
    sql = _sql()
    assert "'probability_publishable',false" in sql
    assert "'can_execute',false" in sql
    assert "update public.wow_mlb_v2d_frozen_spec" not in sql
    assert "production_feature_ready=true" not in sql
    assert "production_feature_ready = true" not in sql
    assert "governed_probability_capability','available" not in sql


def test_auto_hydrator_is_staggered_every_fifteen_minutes():
    sql = _sql()
    assert "select cron.schedule(" in sql
    assert "'wow-mlb-forward-shadow-auto-hydrate'" in sql
    assert "'5,20,35,50 * * * *'" in sql
    assert "select public.wow_mlb_forward_auto_hydrate_pregame();" in sql
