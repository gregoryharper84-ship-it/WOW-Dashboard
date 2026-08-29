from pathlib import Path


SQL_PATH = Path(__file__).with_name("forward_shadow_lineup_confirmation.sql")


def _raw() -> str:
    return SQL_PATH.read_text().lower()


def _sql() -> str:
    return " ".join(_raw().split())


def test_lineup_provenance_is_immutable_and_fail_closed():
    sql = _sql()
    assert "create table if not exists public.wow_mlb_forward_lineup_snapshots" in sql
    assert "wow_mlb_forward_lineup_snapshot_immutable" in sql
    assert "before update on public.wow_mlb_forward_lineup_snapshots" in sql
    assert "before delete on public.wow_mlb_forward_lineup_snapshots" in sql
    assert "strict_pregame_provenance boolean not null default true check (strict_pregame_provenance=true)" in sql
    assert "research_only boolean not null default true check (research_only=true)" in sql
    assert "probability_publishable boolean not null default false check (probability_publishable=false)" in sql
    assert "can_execute boolean not null default false check (can_execute=false)" in sql


def test_confirmation_requires_official_live_feed_and_nine_player_orders():
    sql = _sql()
    assert "https://statsapi.mlb.com/api/v1.1/game/%s/feed/live" in sql
    assert "livedata,boxscore,teams,home,battingorder" in sql
    assert "livedata,boxscore,teams,away,battingorder" in sql
    assert "cardinality(v_home_order) <> 9" in sql
    assert "cardinality(v_away_order) <> 9" in sql
    assert "count(distinct player_id)" in sql
    assert "official_lineup_not_available" in sql
    assert "official_lineup_team_id_mismatch" in sql


def test_lineup_timestamp_is_post_fetch_and_must_remain_pregame():
    text = _raw()
    fetch_pos = text.index("r := extensions.http_get")
    capture_pos = text.index("v_capture_at := clock_timestamp()", fetch_pos)
    started_pos = text.index("event_started_during_lineup_fetch", capture_pos)
    assert fetch_pos < capture_pos < started_pos
    assert "if v_capture_at >= e.event_start_time" in text


def test_confirmation_blocks_actual_gameplay_even_before_scheduled_time():
    sql = _sql()
    assert "livedata,plays,allplays" in sql
    assert "isPitch".lower() in sql
    assert "about,iscomplete" in sql
    assert "game advisory" in sql
    assert "v_pitch_n > 0" in sql
    assert "v_completed_play_n > 0" in sql
    assert "official_gameplay_already_started" in sql
    assert "official_pitch_events_at_capture" in sql
    assert "official_completed_plays_at_capture" in sql


def test_lineup_identity_is_order_based_not_raw_response_based():
    sql = _sql()
    assert "home_batting_order',to_jsonb(v_home_order)" in sql
    assert "away_batting_order',to_jsonb(v_away_order)" in sql
    assert "uq_wow_mlb_forward_lineup_identity unique (shadow_event_id,lineup_identity_sha256)" in sql
    assert "raw_sha256 text not null" in sql


def test_confirmation_updates_current_state_and_rescores_without_publishing():
    sql = _sql()
    assert "set lineup_status='confirmed'" in sql
    assert "lineup_snapshot_id=" in sql
    assert "lineup_confirmed_at=" in sql
    assert "wow_mlb_forward_score_event(p_shadow_event_id)" in sql
    assert "'probability_publishable',false" in sql
    assert "'can_execute',false" in sql
    assert "probability_publishable=true" not in sql
    assert "can_execute=true" not in sql


def test_lineup_poll_is_staggered_after_hydration():
    sql = _sql()
    assert "'wow-mlb-forward-shadow-auto-lineup'" in sql
    assert "'8,23,38,53 * * * *'" in sql
    assert "select public.wow_mlb_forward_auto_confirm_lineups();" in sql
