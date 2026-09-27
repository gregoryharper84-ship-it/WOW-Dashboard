from pathlib import Path


MIGRATION = Path(__file__).with_name("sql") / "20260927_fix_mlb_delayed_pregame_hydration.sql"


def _sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_migration_uses_typed_pregame_allowlist_and_bounded_lateness():
    sql = _sql()
    predicate = "event_status in ('scheduled','pre-game','warmup','delayed start')"
    lateness_guard = "event_start_time > clock_timestamp() - interval '6 hours'"
    assert sql.count(predicate) >= 3
    assert sql.count(lateness_guard) >= 3
    assert "event_start_time > clock_timestamp()" not in sql


def test_migration_preserves_shadow_only_authority():
    sql = _sql()
    assert "'probability_publishable',false" in sql
    assert "'can_execute',false" in sql
    assert "update public.wow_mlb_v2d_frozen_spec" not in sql
    assert "production_feature_ready=true" not in sql
    assert "production_feature_ready = true" not in sql
