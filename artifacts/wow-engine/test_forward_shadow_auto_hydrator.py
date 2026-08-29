from pathlib import Path


SQL_PATH = Path(__file__).with_name("forward_shadow_auto_hydrator.sql")


def _sql() -> str:
    return " ".join(SQL_PATH.read_text().split()).lower()


def test_auto_hydrator_selects_only_freshest_still_pregame_snapshot():
    sql = _sql()
    assert "se.event_start_time > clock_timestamp()" in sql
    assert "order by s.captured_at desc limit 1" in sql
    assert "where snapshot_id=v_snapshot_id" in sql


def test_auto_hydrator_freezes_required_2026_schedule_context():
    sql = _sql()
    assert "unsupported_frozen_feature_season" in sql
    assert "mlb_schedule_season_to_date" in sql
    assert "gameType=R".lower() in sql
    assert "wow_mlb_forward_cache_url" in sql
    assert "wow_mlb_forward_materialize_schedule" in sql
    assert "schedule_context_ready" in sql


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
