from pathlib import Path


MIGRATION = Path(__file__).parent / "migrations" / "20260910_kalshi_weather_v2_access_hardening.sql"
TABLES = (
    "wow_kalshi_weather_contract_rules",
    "wow_kalshi_weather_source_snapshots",
    "wow_kalshi_weather_calibration_profiles",
    "wow_kalshi_weather_predictions",
    "wow_kalshi_weather_market_snapshots",
    "wow_kalshi_weather_outcomes",
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_all_six_ledgers_are_covered_and_rls_enabled():
    sql = _sql()
    for table in TABLES:
        assert table in sql
    assert "enable row level security" in sql


def test_public_api_roles_have_no_direct_privileges():
    sql = _sql()
    assert "revoke all privileges on table public.%i from anon" in sql
    assert "revoke all privileges on table public.%i from authenticated" in sql
    assert "create policy" not in sql


def test_service_role_is_append_only_at_grant_layer():
    sql = _sql()
    assert "revoke all privileges on table public.%i from service_role" in sql
    assert "grant select, insert on table public.%i to service_role" in sql
    assert "grant update" not in sql
    assert "grant delete" not in sql
    assert "grant truncate" not in sql


def test_hardening_adds_no_execution_surface():
    sql = _sql()
    prohibited = ("create order", "place_order", "cancel_order", "modify_order", "can_execute = true")
    assert not any(token in sql for token in prohibited)
