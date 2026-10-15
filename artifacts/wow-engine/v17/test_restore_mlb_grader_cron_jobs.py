"""Contract for restoring the MLB grader cron jobs dropped on 2026-09-27.

Regression guard: the 2026-09-27 restore reinstalled only capture/hydrate and
later load-smoothing migrations preserved absent jobs, so grading silently
stopped while capture continued. These tests pin the restoration, its safety
text, and the >=4 minute runtime spacing against the live maintenance set.
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GRADERS = ROOT / "migrations" / "20261015_restore_mlb_grader_cron_jobs.sql"
RESTORE_MLB = ROOT / "v17" / "sql" / "20260927_restore_mlb_forward_cron_jobs.sql"
BACKPRESSURE = ROOT / "migrations" / "20261003033000_reduce_cron_backpressure.sql"

CAPTURE = "2,17,32,47 * * * *"
HYDRATE = "8,23,38,53 * * * *"
STALE = "13 * * * *"
MLB_GRADE = "27,57 * * * *"
LEDGER_GRADE = "42 * * * *"

SQL = GRADERS.read_text(encoding="utf-8")


def _minutes(spec: str) -> set[int]:
    field = spec.split()[0]
    return {int(value) for value in field.split(",")}


def _cyclic_distance(left: int, right: int) -> int:
    delta = abs(left - right)
    return min(delta, 60 - delta)


def test_restores_forward_shadow_grader():
    assert "'wow-mlb-forward-shadow-auto-grade'" in SQL
    assert f"'{MLB_GRADE}'" in SQL
    assert "select public.wow_mlb_forward_auto_grade_completed();" in SQL


def test_restores_governed_primary_ledger_grader():
    assert "'wow-governed-primary-ledger-auto-grade'" in SQL
    assert f"'{LEDGER_GRADE}'" in SQL
    assert "select public.wow_governed_auto_grade_predictions();" in SQL


def test_restoration_is_idempotent_by_jobname():
    lowered = SQL.lower()
    assert "from cron.job" in lowered
    assert "where jobname = v.jobname" in lowered
    assert "if v_job_id is not null then" in lowered
    assert "cron.unschedule(v_job_id)" in lowered
    assert "cron.schedule(v.jobname, v.cron_expr, v.command_text)" in lowered


def test_old_colliding_grader_minutes_are_not_reused():
    assert "'7,22,37,52 * * * *'" not in SQL
    assert "'9,24,39,54 * * * *'" not in SQL


def test_live_maintenance_set_including_graders_keeps_runtime_spacing():
    # Live schedules are sourced from the migrations that own them.
    assert CAPTURE in RESTORE_MLB.read_text(encoding="utf-8")
    backpressure = BACKPRESSURE.read_text(encoding="utf-8")
    assert HYDRATE in backpressure
    assert STALE in backpressure

    # Grader schedules are parsed from the migration, not trusted constants.
    scheduled = dict(
        re.findall(r"'(wow-[a-z0-9-]+)',\s*'([0-9,]+ \* \* \* \*)'", SQL)
    )
    assert set(scheduled) == {
        "wow-mlb-forward-shadow-auto-grade",
        "wow-governed-primary-ledger-auto-grade",
    }

    active = {
        "capture": _minutes(CAPTURE),
        "hydrate": _minutes(HYDRATE),
        "stale": _minutes(STALE),
        "mlb_grade": _minutes(scheduled["wow-mlb-forward-shadow-auto-grade"]),
        "ledger_grade": _minutes(scheduled["wow-governed-primary-ledger-auto-grade"]),
    }
    names = list(active)
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            for left in active[left_name]:
                for right in active[right_name]:
                    assert _cyclic_distance(left, right) >= 4, (
                        left_name,
                        right_name,
                        left,
                        right,
                    )


def test_restoration_adds_no_probability_or_execution_authority():
    lowered = SQL.lower()
    assert "can_execute=false" in lowered
    assert "probability_publishable=true" not in lowered
    assert "can_execute=true" not in lowered
    assert "can_execute = true" not in lowered
    for forbidden in ("market_probability", "sportsbook", "update public.", "insert into", "delete from"):
        assert forbidden not in lowered, forbidden
