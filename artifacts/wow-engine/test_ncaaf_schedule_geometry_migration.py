from pathlib import Path

ROOT = Path(__file__).parent
FORWARD = ROOT / "migrations" / "20261107_ncaaf_venues_snapshot_and_schedule_geometry.sql"
ROLLBACK = ROOT / "migrations" / "20261107_ncaaf_venues_snapshot_and_schedule_geometry_rollback.sql"


def test_forward_migration_is_narrow_and_does_not_load_rows():
    sql = FORWARD.read_text()
    assert "'/venues'" in sql
    assert "trg_wow_ncaaf_source_snapshot_immutable" in sql
    assert "NCAAF_SOURCE_SNAPSHOT_IMMUTABLE" in sql
    assert "CFBD_SCHEDULE_GEOMETRY_V1" in sql
    assert "array['REST_TRAVEL']" in sql
    assert "can_execute = false" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.wow_ncaaf_source_snapshots from anon, authenticated" in sql
    assert "insert into public.wow_ncaaf_source_snapshots" not in sql.lower()
    assert "create unique index" not in sql.lower()
    assert "iczfhsmjrrafhvcpmqhr" in sql


def test_rollback_restores_the_previous_endpoint_and_refuses_live_rows():
    sql = ROLLBACK.read_text()
    assert "NCAAF_VENUES_ROLLBACK_BLOCKED_BY_SNAPSHOTS" in sql
    assert "NCAAF_GEOMETRY_ROLLBACK_BLOCKED_BY_EVIDENCE" in sql
    assert "'/venues'" not in sql.split("add constraint wow_ncaaf_source_endpoint", 1)[1]
    assert "drop trigger if exists trg_wow_ncaaf_source_snapshot_immutable" in sql
    assert "CFBD_SCHEDULE_GEOMETRY_V1" in sql
