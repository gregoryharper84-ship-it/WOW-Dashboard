from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "20261002_smooth_wow_cron_load.sql"

SCHEDULES = {
    "ncaaf_close": {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
    "mlb_lineup": {1, 6, 11, 16, 21, 26, 31, 36, 41, 46, 51, 56},
    "mlb_capture": {2, 17, 32, 47},
    "stale_reconcile": {3, 8, 13, 18, 23, 28, 33, 38, 43, 48, 53, 58},
    "mlb_hydrate": {4, 19, 34, 49},
    "mlb_grade": {7, 22, 37, 52},
    "primary_ledger_grade": {9, 24, 39, 54},
}


def test_known_wow_cron_starts_do_not_share_exact_minutes():
    names = list(SCHEDULES)
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            assert SCHEDULES[left].isdisjoint(SCHEDULES[right]), (left, right)


def test_load_smoothing_migration_is_idempotent_and_preserves_absent_jobs():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "from cron.job" in sql
    assert "if v_job_id is not null then" in sql
    assert "cron.unschedule(v_job_id)" in sql
    assert "cron.schedule(v.jobname, v.cron_expr, v.command_text)" in sql
    assert "wow-v17-reconcile-stale-pick-runs" in sql
    assert "3,8,13,18,23,28,33,38,43,48,53,58 * * * *" in sql
    assert "wow-mlb-forward-shadow-auto-hydrate" in sql
    assert "4,19,34,49 * * * *" in sql
    assert "wow-mlb-forward-shadow-auto-grade" in sql
    assert "7,22,37,52 * * * *" in sql
    assert "wow-governed-primary-ledger-auto-grade" in sql
    assert "9,24,39,54 * * * *" in sql
    assert "can_execute" in sql.lower()
