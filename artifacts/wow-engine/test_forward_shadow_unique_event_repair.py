from pathlib import Path


SQL_PATH = Path(__file__).with_name("forward_shadow_unique_event_repair.sql")


def _sql() -> str:
    return " ".join(SQL_PATH.read_text().split()).lower()


def test_health_counts_unique_official_games_not_snapshot_rows():
    sql = _sql()
    assert "count(distinct official_event_id)" in sql
    assert "partition by e.official_event_id" in sql
    assert "order by e.snapshot_timestamp asc" in sql
    assert "where event_rank = 1" in sql


def test_auto_grader_uses_same_canonical_event_rule():
    sql = _sql()
    assert "partition by se.spec_id, se.official_event_id" in sql
    assert "where event_rank = 1 and result_status = 'pending'" in sql
    assert "wow_mlb_forward_grade_shadow_event" in sql


def test_manual_grader_blocks_noncanonical_duplicate_shadow():
    sql = _sql()
    assert "noncanonical_duplicate_event_shadow" in sql
    assert "e2.official_event_id = e.official_event_id" in sql
    assert "e2.snapshot_timestamp < e.snapshot_timestamp" in sql


def test_unique_event_repair_stays_fail_closed():
    sql = _sql()
    assert "'probability_publishable',false" in sql
    assert "'can_execute',false" in sql
    assert "update public.wow_mlb_v2d_frozen_spec" not in sql
    assert "production_feature_ready=true" not in sql
    assert "production_feature_ready = true" not in sql
