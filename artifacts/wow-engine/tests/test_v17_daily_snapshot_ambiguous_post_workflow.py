"""Protected workflow regression: ambiguous POST must never silently retry scoring."""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "wow-v17-daily-snapshot.yml"


def test_snapshot_write_like_post_not_blind_retried():
    text = WORKFLOW.read_text(encoding="utf-8")
    job = text.split("  daily-snapshot:", 1)[1].split("  nfl-full-slate-exact-once-acceptance:", 1)[0]
    assert 'DAILY_SNAPSHOT_AMBIGUOUS_POST_HOLD_RECONCILE_RECEIPT' in job
    assert "for attempt in range(" not in job
    assert "time.sleep(retry_delay_seconds)" not in job
    assert "data=body" in job


def test_snapshot_governance_still_fail_closed():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"V17_TERMINAL_REDUCER"' in text
