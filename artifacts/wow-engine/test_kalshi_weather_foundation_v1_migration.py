from pathlib import Path


MIGRATION = Path(__file__).parent / "migrations" / "20261005190000_kalshi_weather_foundation_v1.sql"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_foundation_creates_append_only_digital_twin_and_microstructure_ledgers():
    sql = _sql()
    for table in (
        "wow_kalshi_weather_settlement_twins",
        "wow_kalshi_weather_market_microstructure_snapshots",
    ):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"grant select, insert on table public.{table} to service_role" in sql
    assert "before update or delete" in sql


def test_foundation_preserves_probability_market_firewall_and_execution_false():
    sql = _sql()
    assert "market_data_used_as_weather_probability_input boolean not null default false" in sql
    assert "weather_probability_input_allowed boolean not null default false" in sql
    assert "check (market_data_used_as_weather_probability_input = false)" in sql
    assert "check (weather_probability_input_allowed = false)" in sql
    assert "check (can_execute = false)" in sql


def test_market_recorder_does_not_require_prediction():
    sql = _sql()
    assert "prediction_id text references public.wow_kalshi_weather_predictions(prediction_id)" in sql
    assert "prediction_id text not null references" not in sql.split("wow_kalshi_weather_market_microstructure_snapshots", 1)[1].split(");", 1)[0]


def test_foundation_adds_no_order_execution_surface():
    sql = _sql()
    for prohibited in ("place_order", "cancel_order", "modify_order", "can_execute = true"):
        assert prohibited not in sql
