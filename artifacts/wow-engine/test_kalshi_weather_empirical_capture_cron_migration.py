"""Contract tests for the authenticated Supabase Cron capture trigger.

The failure this guards against is subtle: a scheduler that reports success
while never invoking the collector. These tests assert the scheduled work is
the authenticated POST endpoint, that the shared secret comes from Vault and
fails closed, and that no execution surface is added.
"""
from pathlib import Path

MIGRATION = (
    Path(__file__).parent
    / "migrations"
    / "20261102_kalshi_weather_empirical_capture_cron.sql"
)
CAPTURE_ENDPOINT = "/internal/kalshi-weather/v17/empirical-cohort/cron-run"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _executable() -> str:
    """SQL with comment lines removed, so prose cannot satisfy a path check."""
    return "\n".join(
        line for line in _sql().splitlines() if not line.strip().startswith("--")
    )


def _lower() -> str:
    return _sql().lower()


def test_capture_job_posts_to_the_authenticated_collector():
    sql = _sql()
    assert "net.http_post" in sql
    assert CAPTURE_ENDPOINT in sql
    assert "cron.schedule" in _lower()
    assert "kalshi-weather-v2-empirical-capture" in sql


def test_health_wake_is_separate_and_never_used_for_collection():
    sql = _executable()
    assert "net.http_get" in sql
    capture_function, remainder = sql.split("$reschedule$", 1)
    assert "/health" not in capture_function
    assert "/health" in remainder
    assert "net.http_post" not in remainder


def test_capture_key_comes_from_vault_and_fails_closed():
    sql = _sql()
    assert "vault.decrypted_secrets" in sql
    assert "kalshi_weather_cohort_key" in sql
    assert "cohort_key is null" in sql
    assert "raise exception" in _lower()


def test_header_value_is_the_vault_variable_never_a_literal():
    sql = _sql()
    assert "'X-WOW-Weather-Cohort-Key', btrim(cohort_key)" in sql
    assert "bearer " not in _lower()


def test_old_read_only_job_is_removed_before_rescheduling():
    sql = _executable()
    assert "cron.unschedule" in sql.lower()
    assert "kalshi-weather-v2-shadow-read-only-wake" in sql
    assert sql.index("cron.unschedule") < sql.index("kalshi-weather-v2-empirical-capture")


def test_capture_function_is_least_privilege():
    sql = _sql()
    assert "security definer" in _lower()
    assert "set search_path = ''" in _lower()
    assert "revoke all on function" in _lower()
    assert "grant execute on function" in _lower()


def test_no_execution_or_capability_promotion_surface_is_added():
    sql = _lower()
    for prohibited in (
        "can_execute = true",
        "place_order",
        "cancel_order",
        "modify_order",
        "capability_status = 'available'",
    ):
        assert prohibited not in sql
