from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_MLB = ROOT / "v17" / "sql" / "20260927_restore_mlb_forward_cron_jobs.sql"
SMOOTH_WOW = ROOT / "migrations" / "20261003022500_smooth_wow_cron_load.sql"
BACKPRESSURE = ROOT / "migrations" / "20261003033000_reduce_cron_backpressure.sql"


def _minutes(spec: str) -> set[int]:
    minute_field = spec.split()[0]
    if minute_field.isdigit():
        return {int(minute_field)}
    return {int(value) for value in minute_field.split(",")}


def _cyclic_distance(left: int, right: int) -> int:
    delta = abs(left - right)
    return min(delta, 60 - delta)


def test_effective_active_maintenance_schedules_have_runtime_spacing():
    mlb = RESTORE_MLB.read_text(encoding="utf-8")
    backpressure = BACKPRESSURE.read_text(encoding="utf-8")

    capture = "2,17,32,47 * * * *"
    stale = "13 * * * *"
    hydrate = "8,23,38,53 * * * *"

    assert capture in mlb
    assert hydrate in mlb
    assert stale in backpressure
    assert hydrate in backpressure

    active = {
        "capture": _minutes(capture),
        "stale": _minutes(stale),
        "hydrate": _minutes(hydrate),
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

    assert len(active["stale"]) == 1
    assert len(active["capture"]) == 4
    assert len(active["hydrate"]) == 4


def test_backpressure_migration_preserves_existing_job_set_and_safety():
    sql = BACKPRESSURE.read_text(encoding="utf-8").lower()

    assert "from cron.job" in sql
    assert "if v_job_id is not null then" in sql
    assert "cron.unschedule" in sql
    assert "wow-v17-reconcile-stale-pick-runs" in sql
    assert "13 * * * *" in sql
    assert "wow-mlb-forward-shadow-auto-hydrate" in sql
    assert "8,23,38,53 * * * *" in sql
    assert "can_execute=false" in sql
    assert "sporting probability" in sql


def test_prior_smoothing_migration_remains_immutable_history():
    sql = SMOOTH_WOW.read_text(encoding="utf-8")
    assert "3,8,13,18,23,28,33,38,43,48,53,58 * * * *" in sql
    assert "4,19,34,49 * * * *" in sql
