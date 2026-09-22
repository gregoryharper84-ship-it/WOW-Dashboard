from pathlib import Path

import github_actions_oidc as oidc
from v17.team_event_recoverable_hold_overlay import (
    DURABLE_RETRY_WATCHER_BOUND,
    RETRY_WATCHER_CADENCE_MINUTES,
    RETRY_WATCHER_MODE,
    RETRY_WATCHER_WORKFLOW,
)


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "wow-v17-team-event-recoverable-refresh.yml"


def test_recoverable_refresh_is_durable_hourly_and_non_executing():
    text = WORKFLOW.read_text()
    assert 'cron: "17 * * * *"' in text
    assert 'concurrency:' in text
    assert 'cancel-in-progress: false' in text
    assert 'WOW_CAN_EXECUTE: "false"' in text
    assert 'WOW_DRY_RUN_ONLY: "true"' in text
    assert '"lanes": ["MONEYLINE"]' in text
    assert '"max_team_events": 24' in text
    assert '"response_mode": "COMPACT"' in text
    assert '"/internal/v17/daily-snapshot"' in text
    assert 'V17_TERMINAL_REDUCER' in text
    assert 'row_reconciliation") != "PASS"' in text


def test_recoverable_hold_metadata_matches_installed_controller():
    assert DURABLE_RETRY_WATCHER_BOUND is True
    assert RETRY_WATCHER_MODE == "HOURLY_CANONICAL_FULL_SLATE_REFRESH"
    assert RETRY_WATCHER_WORKFLOW == "wow-v17-team-event-recoverable-refresh.yml"
    assert RETRY_WATCHER_CADENCE_MINUTES == 60
    assert WORKFLOW.exists()


def test_recoverable_refresh_has_exact_oidc_trust_boundary():
    assert oidc.TEAM_EVENT_RECOVERABLE_REFRESH_WORKFLOW_REF in oidc.ALLOWED_WORKFLOW_REFS
    assert oidc.TEAM_EVENT_RECOVERABLE_REFRESH_WORKFLOW_REF.endswith(
        "/.github/workflows/wow-v17-team-event-recoverable-refresh.yml@refs/heads/main"
    )
    assert oidc.LIVE_CANARY_WORKFLOW_REFS.isdisjoint(
        {oidc.TEAM_EVENT_RECOVERABLE_REFRESH_WORKFLOW_REF}
    )
