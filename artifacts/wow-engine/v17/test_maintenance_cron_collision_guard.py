from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_MLB = ROOT / "v17" / "sql" / "20260927_restore_mlb_forward_cron_jobs.sql"
STAGGER_RECONCILER = ROOT / "migrations" / "20261003_retime_stale_reconciler_quiet_window.sql"


def _minutes(spec: str) -> set[int]:
    minute_field = spec.split()[0]
    return {int(value) for value in minute_field.split(",")}


def test_effective_reconciler_schedule_does_not_collide_with_mlb_maintenance():
    mlb = RESTORE_MLB.read_text(encoding="utf-8")
    reconciler = STAGGER_RECONCILER.read_text(encoding="utf-8")

    capture = "2,17,32,47 * * * *"
    hydrate = "5,20,35,50 * * * *"
    stale = "11,26,41,56 * * * *"

    assert capture in mlb
    assert hydrate in mlb
    assert stale in reconciler

    assert _minutes(stale).isdisjoint(_minutes(capture))
    assert _minutes(stale).isdisjoint(_minutes(hydrate))
    assert len(_minutes(stale)) == 4
    for minute in _minutes(stale):
        minutes_since_hydrate = min((minute - h) % 60 for h in _minutes(hydrate))
        minutes_until_capture = min((c - minute) % 60 for c in _minutes(capture))
        assert minutes_since_hydrate >= 6
        assert minutes_until_capture >= 6


def test_cron_stagger_preserves_reconciler_semantics_and_safety():
    sql = STAGGER_RECONCILER.read_text(encoding="utf-8").lower()

    assert "cron.unschedule" in sql
    assert "wow-v17-reconcile-stale-pick-runs" in sql
    assert "wow_reconcile_stale_pick_request_runs(3600)" in sql
    assert "can_execute" not in sql
    assert "probability" not in sql or "no sporting probability" in sql
