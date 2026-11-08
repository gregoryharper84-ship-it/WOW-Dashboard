"""The Kalshi Weather wake must dispatch collection, not read health.

These source-contract tests inspect executable SQL with comments removed, so a
comment cannot satisfy a security or fail-closed assertion.
"""
import re
from pathlib import Path

SQL = Path(__file__).with_name(
    "kalshi_weather_v2_collection_trigger_cron.sql"
).read_text(encoding="utf-8").lower()
COMMENT_FREE_SQL = re.sub(r"--[^\\n]*", "", SQL)
FUNCTION_BODY = COMMENT_FREE_SQL.split("create or replace function", 1)[1]

RUNBOOK = Path(__file__).parents[2] / "docs" / "kalshi-weather" / (
    "SUPABASE_SHADOW_WAKE_AND_CALIBRATION_READINESS.md"
)


def _guard_block(marker: str) -> str:
    start = COMMENT_FREE_SQL.index(marker)
    end = COMMENT_FREE_SQL.index("end if;", start)
    return COMMENT_FREE_SQL[start:end]


def test_health_only_wake_is_retired_and_cannot_survive_the_migration():
    assert "cron.unschedule('kalshi-weather-v2-shadow-read-only-wake')" in COMMENT_FREE_SQL
    assert "net.http_get" not in COMMENT_FREE_SQL
    schedule_section = COMMENT_FREE_SQL.split("select cron.schedule", 1)[1]
    assert "shadow-read-only-wake" not in schedule_section
    assert "authenticated-collection-trigger" in schedule_section
    assert COMMENT_FREE_SQL.count("select cron.schedule(") == 1


def test_scheduler_posts_the_authenticated_collection_endpoint():
    assert "'kalshi-weather-v2-authenticated-collection-trigger'" in COMMENT_FREE_SQL
    assert "'*/30 * * * *'" in COMMENT_FREE_SQL
    assert "select public.wow_kalshi_weather_trigger_collection();" in COMMENT_FREE_SQL
    assert "net.http_post(" in COMMENT_FREE_SQL
    assert "'/internal/kalshi-weather/v17/empirical-cohort/cron-run'" in COMMENT_FREE_SQL
    assert "'x-wow-weather-cohort-key'" in COMMENT_FREE_SQL
    assert "timeout_milliseconds := 90000" in COMMENT_FREE_SQL
    assert "authorization" not in COMMENT_FREE_SQL


def test_secrets_are_read_from_vault_and_never_embedded():
    assert "vault.decrypted_secrets" in COMMENT_FREE_SQL
    assert "'wow_kalshi_weather_service_url'" in COMMENT_FREE_SQL
    assert "'wow_kalshi_weather_cohort_key'" in COMMENT_FREE_SQL
    assert "v_base_url text;" in COMMENT_FREE_SQL
    assert "v_cohort_key text;" in COMMENT_FREE_SQL
    assert "bearer " not in COMMENT_FREE_SQL


def test_missing_configuration_guards_trim_and_log_before_blocking():
    cases = (
        (
            "if nullif(trim(v_base_url), '') is null then",
            "kalshi_weather_service_url_secret_missing",
        ),
        (
            "if nullif(trim(v_cohort_key), '') is null then",
            "kalshi_weather_cohort_key_secret_missing",
        ),
    )
    for marker, reason in cases:
        block = _guard_block(marker)
        assert "raise warning 'kalshi_weather_collection_blocked reason=%'" in block
        assert reason in block
        assert "'status','blocked'" in block
        assert "return jsonb_build_object(" in block
        assert "net.http_post(" not in block


def test_trigger_has_no_probability_or_execution_authority():
    assert "'probability_publishable',false" in FUNCTION_BODY
    assert "'can_execute',false" in FUNCTION_BODY
    assert "'collection_mode','bounded_rotating_capture_only_shadow'" in FUNCTION_BODY
    assert "insert " not in FUNCTION_BODY


def test_internal_function_is_not_reachable_through_the_data_api():
    assert "security invoker" in COMMENT_FREE_SQL
    assert "set search_path to ''" in COMMENT_FREE_SQL
    assert "revoke all privileges on function public.wow_kalshi_weather_trigger_collection()" in COMMENT_FREE_SQL
    assert "from public, anon, authenticated, service_role" in COMMENT_FREE_SQL


def test_service_url_is_pinned_to_the_exact_verified_render_origin():
    assert r"^https://wow-kalshi-weather-v2\\.onrender\\.com$" in COMMENT_FREE_SQL
    assert "a-z0-9-" not in COMMENT_FREE_SQL
    block = _guard_block("if v_base_url !~*")
    assert "raise warning 'kalshi_weather_collection_blocked reason=%'" in block
    assert "kalshi_weather_service_url_not_approved" in block
    assert "'status','blocked'" in block
    assert COMMENT_FREE_SQL.index("if v_base_url !~*") < COMMENT_FREE_SQL.index("net.http_post(")


def test_repeated_migration_cannot_install_a_duplicate_scheduler():
    assert "$dedupe$" in COMMENT_FREE_SQL
    assert "perform cron.unschedule(v_job_id)" in COMMENT_FREE_SQL
    assert "where jobname = 'kalshi-weather-v2-authenticated-collection-trigger'" in COMMENT_FREE_SQL
    assert COMMENT_FREE_SQL.index("$dedupe$") < COMMENT_FREE_SQL.index("select cron.schedule(")


def test_runbook_distinguishes_implemented_merged_applied_and_verified():
    doc = RUNBOOK.read_text(encoding="utf-8")
    for state in (
        "IMPLEMENTED_IN_PR",
        "APPROVED_AND_MERGED",
        "MIGRATION_APPLIED",
        "PRODUCTION_VERIFIED",
    ):
        assert state in doc
    assert "Current production state (2026-11-08): `IMPLEMENTED_IN_PR` only." in doc
    assert "**Not applied.**" in doc
    assert "Render currently runs `8857e488`" in doc
    assert "https://wow-kalshi-weather-v2.onrender.com" in doc
    assert "50 total" in doc
    assert "30 chronological training" in doc
    assert "20 chronological holdout" in doc
