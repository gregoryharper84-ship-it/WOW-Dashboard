"""The Kalshi Weather wake must dispatch collection, not read health.

A health GET can never start the empirical cohort: the only collection
entrypoint is the authenticated cron-run POST. These checks keep the scheduler
authenticated, fail-closed and publication-free.
"""
from pathlib import Path

SQL = Path(__file__).with_name(
    "kalshi_weather_v2_collection_trigger_cron.sql"
).read_text(encoding="utf-8").lower()

FUNCTION_BODY = SQL.split("create or replace function", 1)[1]

RUNBOOK = Path(__file__).parents[2] / "docs" / "kalshi-weather" / (
    "SUPABASE_SHADOW_WAKE_AND_CALIBRATION_READINESS.md"
)


def test_health_only_wake_is_retired_and_cannot_survive_the_migration():
    assert "cron.unschedule('kalshi-weather-v2-shadow-read-only-wake')" in SQL
    assert "net.http_get" not in SQL
    # The retired name may appear only in the retirement guard. It must never be
    # rescheduled, and the only installed job must be the authenticated trigger.
    schedule_section = SQL.split("select cron.schedule", 1)[1]
    assert "shadow-read-only-wake" not in schedule_section
    assert "authenticated-collection-trigger" in schedule_section
    # Only the function body defines the outbound request; it must not read health.
    assert "/health" not in FUNCTION_BODY


def test_scheduler_posts_the_authenticated_collection_endpoint():
    assert "'kalshi-weather-v2-authenticated-collection-trigger'" in SQL
    assert "'*/30 * * * *'" in SQL
    assert "select public.wow_kalshi_weather_trigger_collection();" in SQL
    assert "net.http_post(" in SQL
    assert "'/internal/kalshi-weather/v17/empirical-cohort/cron-run'" in SQL
    assert "'x-wow-weather-cohort-key'" in SQL
    assert "timeout_milliseconds := 90000" in SQL
    # The weather trigger owns its cohort header; it must not borrow the
    # internal API bearer credential.
    assert "authorization" not in SQL


def test_secrets_are_read_from_vault_and_never_embedded():
    assert "vault.decrypted_secrets" in SQL
    assert "'wow_kalshi_weather_service_url'" in SQL
    assert "'wow_kalshi_weather_cohort_key'" in SQL
    assert "v_base_url text;" in SQL
    assert "v_cohort_key text;" in SQL
    # No credential value is ever written into the scheduled command text.
    assert "bearer " not in SQL


def test_missing_configuration_fails_closed_before_any_dispatch():
    assert "'kalshi_weather_service_url_secret_missing'" in SQL
    assert "'kalshi_weather_cohort_key_secret_missing'" in SQL
    # Both guards must precede the dispatch, so an unconfigured job is inert.
    first_guard = SQL.index("'kalshi_weather_service_url_secret_missing'")
    second_guard = SQL.index("'kalshi_weather_cohort_key_secret_missing'")
    dispatch = SQL.index("net.http_post(")
    assert first_guard < dispatch
    assert second_guard < dispatch


def test_trigger_has_no_probability_or_execution_authority():
    assert "'probability_publishable',false" in SQL
    assert "'can_execute',false" in SQL
    assert "'collection_mode','bounded_rotating_capture_only_shadow'" in SQL
    assert "certify" not in FUNCTION_BODY
    assert "insert " not in SQL


def test_internal_function_is_not_reachable_through_the_data_api():
    assert "security invoker" in SQL
    assert "set search_path to ''" in SQL
    assert "revoke all privileges on function public.wow_kalshi_weather_trigger_collection()" in SQL
    assert "from public, anon, authenticated, service_role" in SQL


def test_service_url_must_be_tls_on_the_approved_origin_before_dispatch():
    assert "'kalshi_weather_service_url_not_approved'" in SQL
    assert "^https://wow-kalshi-weather-v2" in SQL
    assert r"\.onrender\.com$" in SQL
    # The destination guard must precede the request that carries the key.
    assert SQL.index("kalshi_weather_service_url_not_approved") < SQL.index("net.http_post(")
    after_guard = SQL.split("kalshi_weather_service_url_not_approved", 1)[1]
    assert "'x-wow-weather-cohort-key'" in after_guard
    assert "'destination_host',split_part" in after_guard


def test_repeated_migration_cannot_install_a_duplicate_scheduler():
    assert SQL.count("select cron.schedule(") == 1
    assert "$dedupe$" in SQL
    assert "perform cron.unschedule(v_job_id)" in SQL
    assert "where jobname = 'kalshi-weather-v2-authenticated-collection-trigger'" in SQL
    assert SQL.index("$dedupe$") < SQL.index("select cron.schedule(")


def test_runbook_distinguishes_implemented_merged_applied_and_verified():
    doc = RUNBOOK.read_text(encoding="utf-8")
    for state in (
        "IMPLEMENTED_IN_PR",
        "APPROVED_AND_MERGED",
        "MIGRATION_APPLIED",
        "PRODUCTION_VERIFIED",
    ):
        assert state in doc
    # No false-green: the document must not describe intent as live production.
    assert "**2026-11-08: the read-only wake was retired.**" not in doc
    assert "two times per hour), active." not in doc
    assert "Current production state (2026-11-08): `IMPLEMENTED_IN_PR` only." in doc
    assert "**Not applied.**" in doc
    assert "Render currently runs `8857e488`" in doc
