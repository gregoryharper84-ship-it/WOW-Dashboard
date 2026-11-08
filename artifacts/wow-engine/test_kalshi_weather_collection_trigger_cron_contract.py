"""Contract tests for the durable, non-secret Kalshi Weather dispatch queue."""
import re
from pathlib import Path

SQL = Path(__file__).with_name(
    "kalshi_weather_v2_collection_trigger_cron.sql"
).read_text(encoding="utf-8").lower()
COMMENT_FREE_SQL = re.sub(r"--[^\n]*", "", SQL)
RUNBOOK = Path(__file__).parents[2] / "docs" / "kalshi-weather" / (
    "SUPABASE_SHADOW_WAKE_AND_CALIBRATION_READINESS.md"
)


def test_old_network_wakes_are_retired_and_exactly_one_queue_job_is_scheduled():
    """Only the idempotent durable queue schedule may remain."""
    for old_name in (
        "kalshi-weather-v2-shadow-read-only-wake",
        "kalshi-weather-v2-authenticated-collection-trigger",
    ):
        assert old_name in COMMENT_FREE_SQL
    assert "perform cron.unschedule(v_job_id)" in COMMENT_FREE_SQL
    assert "where jobname in (" in COMMENT_FREE_SQL
    assert "'kalshi-weather-v2-durable-collection-dispatch'" in COMMENT_FREE_SQL
    schedule = COMMENT_FREE_SQL.split("select cron.schedule", 1)[1]
    assert "'*/30 * * * *'" in schedule
    assert COMMENT_FREE_SQL.count("select cron.schedule(") == 1


def test_dispatch_function_only_inserts_a_nonsecret_idempotent_queue_row():
    """Cron enqueues metadata only; it cannot send credentials over HTTP."""
    body = COMMENT_FREE_SQL.split(
        "create or replace function public.wow_kalshi_weather_trigger_collection()",
        1,
    )[1]
    assert "insert into public.wow_kalshi_weather_collection_dispatch_queue" in body
    assert "on conflict (scheduled_slot) do nothing" in body
    assert "date_trunc('hour', v_now)" in body
    assert "make_interval(mins => (extract(minute from v_now)::integer / 30) * 30)" in body
    assert "'status', v_status" in body
    assert "'dispatch_transport', 'durable_database_queue'" in body
    assert "'probability_publishable', false" in body
    assert "'can_execute', false" in body
    for forbidden in (
        "net.http_post",
        "net.http_get",
        "vault.decrypted_secrets",
        "x-wow-weather-cohort-key",
        "wow_kalshi_weather_cohort_key",
        "wow_kalshi_weather_service_url",
    ):
        assert forbidden not in COMMENT_FREE_SQL


def test_queue_is_rls_protected_and_service_role_has_only_consumer_access():
    """The public table is inaccessible to Data API user roles."""
    assert "alter table public.wow_kalshi_weather_collection_dispatch_queue" in COMMENT_FREE_SQL
    assert "enable row level security" in COMMENT_FREE_SQL
    assert (
        "revoke all privileges on table public.wow_kalshi_weather_collection_dispatch_queue"
        in COMMENT_FREE_SQL
    )
    assert "from public, anon, authenticated" in COMMENT_FREE_SQL
    assert (
        "grant select, update on table public.wow_kalshi_weather_collection_dispatch_queue"
        in COMMENT_FREE_SQL
    )
    assert "to service_role" in COMMENT_FREE_SQL


def test_claim_rpc_is_atomic_service_role_only_and_recovers_stale_claims():
    """SKIP LOCKED prevents two Render replicas claiming the same dispatch."""
    body = COMMENT_FREE_SQL.split(
        "create or replace function public.claim_wow_kalshi_weather_collection_dispatch()",
        1,
    )[1].split("$function$;", 1)[0]
    assert "security invoker" in body
    assert "set search_path to ''" in body
    assert "pg_try_advisory_xact_lock" in body
    assert "hashtextextended('wow-kalshi-weather-dispatch-queue', 0)" in body
    assert "active.status = 'claimed'" in body
    assert "for update skip locked" in body
    assert "interval '15 minutes'" in body
    assert "attempts >= 3" in body
    assert "dispatch_max_attempts_exceeded" in body
    assert (
        "grant execute on function public.claim_wow_kalshi_weather_collection_dispatch()"
        in COMMENT_FREE_SQL
    )
    assert "to service_role" in COMMENT_FREE_SQL


def test_no_http_transport_or_redirect_following_secret_is_left_in_migration():
    """Redirect risk is removed by eliminating outbound HTTP, not URL filtering."""
    for forbidden in (
        "create extension if not exists pg_net",
        "net.http_post",
        "net.http_get",
        "http://",
        "https://",
        "vault.decrypted_secrets",
        "authorization",
        "x-wow-weather-cohort-key",
    ):
        assert forbidden not in COMMENT_FREE_SQL


def test_dispatch_queue_cannot_publish_probabilities_or_execute_trades():
    """Every scheduler response remains research-only."""
    assert "'probability_publishable', false" in COMMENT_FREE_SQL
    assert "'can_execute', false" in COMMENT_FREE_SQL
    assert "security invoker" in COMMENT_FREE_SQL


def test_runbook_distinguishes_implemented_merged_applied_and_verified():
    """The operational runbook describes the queue design and production boundary."""
    doc = RUNBOOK.read_text(encoding="utf-8")
    for state in (
        "IMPLEMENTED_IN_PR",
        "APPROVED_AND_MERGED",
        "MIGRATION_APPLIED",
        "PRODUCTION_VERIFIED",
    ):
        assert state in doc
    assert "Current production state (2026-11-08): `IMPLEMENTED_IN_PR` only." in doc
    assert "kalshi-weather-v2-durable-collection-dispatch" in doc
    assert "WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED=1" in doc
    assert "FOR UPDATE SKIP LOCKED" in doc
    assert "50 total" in doc
    assert "30 chronologically earlier training residuals" in doc
    assert "20 later holdout residuals" in doc
