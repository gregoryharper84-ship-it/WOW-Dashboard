from pathlib import Path


SQL = (
    Path(__file__).resolve().parent
    / "sql"
    / "20260927_fix_mlb_pregame_hydration_state.sql"
).read_text(encoding="utf-8")


def _normalized() -> str:
    return " ".join(SQL.split()).lower()


def test_migration_uses_authoritative_pregame_status_allowlist():
    sql = _normalized()
    assert "'scheduled','pre-game','pregame','delayed start','warmup'" in sql
    assert sql.count("'delayed start'") >= 3
    assert "'in progress'" not in sql
    assert "'final'" not in sql


def test_migration_keeps_blank_status_future_time_as_bounded_fallback_only():
    sql = _normalized()
    assert "btrim(coalesce(se.event_status,'')) = '' and se.event_start_time > clock_timestamp()" in sql
    assert "s.captured_at >= clock_timestamp() - interval '24 hours'" in sql
    assert "deduplicates unchanged pregame identity" in sql


def test_migration_preserves_shadow_only_governance():
    sql = _normalized()
    assert "'probability_publishable',false" in sql
    assert "'can_execute',false" in sql
    assert "update public.wow_mlb_v2d_frozen_spec" not in sql
    assert "production_feature_ready = true" not in sql
