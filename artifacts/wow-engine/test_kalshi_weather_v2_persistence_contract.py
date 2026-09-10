from pathlib import Path


ROOT = Path(__file__).resolve().parent
SQL = (ROOT / "migrations" / "20260910_kalshi_weather_v2_persistence.sql").read_text()


def test_kalshi_weather_v2_persistence_registers_all_immutable_ledgers():
    for table in (
        "wow_kalshi_weather_contract_rules",
        "wow_kalshi_weather_source_snapshots",
        "wow_kalshi_weather_calibration_profiles",
        "wow_kalshi_weather_predictions",
        "wow_kalshi_weather_market_snapshots",
        "wow_kalshi_weather_outcomes",
    ):
        assert f"create table if not exists public.{table}" in SQL
        assert table in SQL.split("Immutable ledgers:", 1)[1]


def test_kalshi_weather_v2_capability_starts_fail_closed_and_non_executable():
    assert "'KALSHI_WEATHER_PROBABILITY'" in SQL
    assert "'UNAVAILABLE'" in SQL
    assert "'probability_publishable', false" in SQL
    assert "can_execute = false" in SQL


def test_no_order_or_execution_table_is_created():
    lowered = SQL.lower()
    assert "create table if not exists public.wow_kalshi_orders" not in lowered
    assert "create table if not exists public.wow_kalshi_weather_orders" not in lowered
    assert "order_placement" not in lowered
