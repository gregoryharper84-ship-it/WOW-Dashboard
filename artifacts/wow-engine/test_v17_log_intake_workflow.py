"""No log content can grant dispatch, mutation, betting or merge authority."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "wow-v17-log-incident-intake.yml"
MODULE = ROOT / "artifacts" / "wow-engine" / "v17" / "log_incident_intake.py"


def test_workflow_reuses_approved_intake_without_parallel_writer():
    wf = WF.read_text()
    assert "contents: read" in wf
    assert "issues: write" not in wf
    assert "pull-requests: write" not in wf
    assert "actions: write" not in wf
    assert "engineering-provider-dispatcher" not in wf
    assert "persist-credentials: false" in wf
    assert "cancel-in-progress: false" in wf


def test_only_explicitly_configured_scheduled_ingest_runs():
    wf = WF.read_text()
    assert "WOW_V17_LOG_INTAKE_ENABLED == 'true'" in wf
    assert "WOW_DATADOG_LOG_READ_API_KEY" in wf
    assert "WOW_DATADOG_LOG_READ_APP_KEY" in wf
    assert "--hours 2" in wf
    assert "if: always()" in wf
    assert "WOW V17 log intake" in wf


def test_runtime_cannot_mutate_reducer_probability_or_wager_state():
    data = MODULE.read_text()
    for invariant in ("custom_gpt_identity=WOW_BETTING_ENGINE", "runtime_generation=V17_ACTIVE",
                      "terminal_authority=V17_TERMINAL_REDUCER", "can_execute=false",
                      "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true"):
        assert invariant in data
    assert "def fetch_datadog(" in data
    assert "def classify(" in data
