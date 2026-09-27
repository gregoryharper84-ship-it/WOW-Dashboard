from pathlib import Path


SQL = (
    Path(__file__).resolve().parent
    / "sql"
    / "20260927_restore_mlb_forward_cron_jobs.sql"
).read_text(encoding="utf-8")


def test_reconciliation_installs_capture_job_only_when_absent():
    assert "jobname = 'wow-mlb-forward-shadow-auto-capture'" in SQL
    assert "'2,17,32,47 * * * *'" in SQL
    assert "select public.wow_mlb_forward_auto_capture_pregame();" in SQL
    assert "if not exists" in SQL.lower()


def test_reconciliation_installs_hydrator_job_only_when_absent():
    assert "jobname = 'wow-mlb-forward-shadow-auto-hydrate'" in SQL
    assert "'5,20,35,50 * * * *'" in SQL
    assert "select public.wow_mlb_forward_auto_hydrate_pregame();" in SQL


def test_reconciliation_does_not_add_probability_or_execution_authority():
    lowered = SQL.lower()
    assert "probability_publishable=false" in lowered
    assert "can_execute=false" in lowered
    assert "market_probability" not in lowered
    assert "sportsbook" not in lowered
