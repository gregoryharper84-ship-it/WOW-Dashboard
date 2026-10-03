from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_MLB = ROOT / "v17" / "sql" / "20260927_restore_mlb_forward_cron_jobs.sql"
SMOOTH_WOW = ROOT / "migrations" / "20261002_smooth_wow_cron_load.sql"


def _minutes(spec: str) -> set[int]:
    minute_field = spec.split()[0]
    return {int(value) for value in minute_field.split(",")}


def test_effective_maintenance_schedules_do_not_collide():
    mlb = RESTORE_MLB.read_text(encoding="utf-8")
    smoothing = SMOOTH_WOW.read_text(encoding="utf-8")

    capture = "2,17,32,47 * * * *"
    stale = "3,8,13,18,23,28,33,38,43,48,53,58 * * * *"
    hydrate = "4,19,34,49 * * * *"
    mlb_grade = "7,22,37,52 * * * *"
    ledger_grade = "9,24,39,54 * * * *"

    assert capture in mlb
    for spec in (stale, hydrate, mlb_grade, ledger_grade):
        assert spec in smoothing

    schedules = [capture, stale, hydrate, mlb_grade, ledger_grade]
    for index, left in enumerate(schedules):
        for right in schedules[index + 1 :]:
            assert _minutes(left).isdisjoint(_minutes(right))

    assert len(_minutes(stale)) == 12


def test_cron_smoothing_preserves_existing_job_set_and_safety():
    sql = SMOOTH_WOW.read_text(encoding="utf-8").lower()

    assert "from cron.job" in sql
    assert "if v_job_id is not null then" in sql
    assert "cron.unschedule" in sql
    assert "wow-v17-reconcile-stale-pick-runs" in sql
    assert "wow-mlb-forward-shadow-auto-hydrate" in sql
    assert "wow-mlb-forward-shadow-auto-grade" in sql
    assert "wow-governed-primary-ledger-auto-grade" in sql
    assert "can_execute=false" in sql
    assert "probability rule" in sql
