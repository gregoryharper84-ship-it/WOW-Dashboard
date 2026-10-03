from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "20260922_pick_request_stale_run_reconciler.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_stale_reconciler_is_orchestration_only_and_fail_closed():
    sql = _sql()
    assert "wow_reconcile_stale_pick_request_runs" in sql
    assert "where r.run_status = 'running'" in sql
    assert "j.status in ('queued', 'retry_wait')" in sql
    assert "j.status = 'running'" in sql
    assert "lease_expires_at" in sql
    assert "stopped_infrastructure_exact_once_protected" in sql
    assert "stale_run_pending_without_active_job" in sql
    assert "stale_run_manifest_row_count_mismatch" in sql
    assert "can_execute = false" in sql
    assert "update public.wow_pick_request_row_states" not in sql


def test_stale_reconciler_finalizes_only_from_durable_row_truth():
    sql = _sql()
    assert "count(*) filter (where s.terminal_status = 'completed')" in sql
    assert "count(*) filter (where s.terminal_status = 'held')" in sql
    assert "count(*) filter (where s.terminal_status = 'rejected')" in sql
    assert "count(*) filter (where s.terminal_status = 'pending')" in sql
    assert "when a.completed_rows = a.persisted_rows then 'complete'" in sql
    assert "when a.completed_rows > 0 then 'degraded'" in sql
    assert "else 'blocked'" in sql
    assert "finalized_from_durable_row_truth" in sql


def test_pending_stale_rows_are_never_retried_or_scored_by_reconciler():
    sql = _sql()
    assert "when a.pending_rows > 0 then 'stopped_infrastructure_exact_once_protected'" in sql
    assert "active_job_proven', false" in sql
    assert "reopen_allowed = case" in sql
    assert "then false" in sql
    assert "score-pick-request" not in sql
    assert "prediction_id =" not in sql
    assert "probability_publishable =" not in sql


def test_reconciler_runs_periodically_without_duplicate_cron_jobs():
    sql = _sql()
    assert "cron.unschedule" in sql
    assert "wow-v17-reconcile-stale-pick-runs" in sql
    assert "'*/5 * * * *'" in sql
    assert "wow_reconcile_stale_pick_request_runs(3600)" in sql
    assert "if p_stale_after_seconds < 300" in sql


def test_reconciler_function_is_not_exposed_to_end_users():
    sql = _sql()
    assert "revoke all on function public.wow_reconcile_stale_pick_request_runs(integer) from public, anon, authenticated" in sql
    assert "grant execute on function public.wow_reconcile_stale_pick_request_runs(integer) to service_role" in sql
