from pathlib import Path


MIGRATION = Path(__file__).with_name("sql") / "20260927_fix_mlb_delayed_source_timestamp_guard.sql"


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_feature_builder_uses_same_authoritative_pregame_contract_as_hydrator():
    sql = _sql()
    assert "event_status not in ('scheduled','pre-game','warmup','delayed start')" in sql
    assert "event_start_time <= clock_timestamp() - interval '6 hours'" in sql
    assert "'reason','event_not_pregame'" in sql
    assert "'reason','pregame_window_expired'" in sql


def test_post_nominal_capture_is_not_rejected_only_for_being_after_scheduled_start():
    sql = _sql()
    # Preserve strict source-before-start behavior while the scheduled start is still future.
    assert "event_start_time > clock_timestamp() and v_latest_capture >= e.event_start_time" in sql
    # Do not retain the old unconditional nominal-clock blocker.
    assert "v_latest_capture is null or v_latest_capture>=e.event_start_time" not in sql
    assert "v_latest_capture is null or v_latest_capture >= e.event_start_time" not in sql


def test_future_dated_source_capture_still_fails_closed():
    sql = _sql()
    assert "v_latest_capture > clock_timestamp()" in sql
    assert "'reason','source_timestamp_in_future'" in sql
    assert "'reason','source_timestamp_missing'" in sql


def test_frozen_feature_contract_and_execution_authority_are_unchanged():
    sql = _sql()
    feature_names = (
        "is_home,off_runs_pg,off_hits_pg,off_hr_pg,off_bb_pg,off_so_pg,off_tb_pg,"
        "off_run_diff_pg,off_win_rate,off_sb_pg,off_cs_pg,off_days_rest,"
        "opp_runs_allowed_pg,opp_errors_pg,opp_win_rate,opp_bp_era,opp_bp_k_rate,"
        "opp_bp_bb_rate,opp_bp_hr_rate,opp_bp_pitches_3d,opp_bp_outs_3d,opp_bp_apps_3d,"
        "opp_starter_prior_starts,opp_starter_era,opp_starter_k_rate,opp_starter_bb_rate,"
        "opp_starter_h_rate,opp_starter_hr_rate,opp_starter_outs_per_start,"
        "opp_starter_tbf_per_start,opp_starter_pitches_per_start,opp_starter_strike_rate,"
        "opp_starter_days_rest,opp_starter_pitches_last3,park_total_runs_prior,"
        "park_prior_games,opp_days_rest,min_team_prior_games"
    )
    for name in feature_names.split(","):
        assert f"'{name}'" in sql
    assert "'feature_count',cardinality(v_vec)" in sql
    assert "'can_execute',false" in sql
    assert "probability_publishable',true" not in sql
    assert "update public.wow_mlb_v2d_frozen_spec" not in sql
