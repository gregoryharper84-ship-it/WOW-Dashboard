"""No log content can grant dispatch, mutation, betting or merge authority.

Datadog was retired by the owner on 2026-11-22 (#1021). The workflow keeps only
its pull_request offline security/regression job. The consumer side of the
retired artifact contract (Reporter provenance, artifact and redaction checks)
stays fail-closed and is covered by test_v17_engineering_log_reporter.py and
test_v17_log_reporter_artifact_security.py.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows" / "wow-v17-log-incident-intake.yml"
MODULE = ROOT / "artifacts" / "wow-engine" / "v17" / "log_incident_intake.py"


def test_retired_datadog_has_no_unattended_or_manual_source_calls():
    """When the source is retired, only PR-local security tests may execute."""
    wf = WF.read_text()
    assert "\n  schedule:\n" not in wf
    assert "\n  workflow_dispatch:\n" not in wf
    assert "  pull_request:\n" in wf
    assert "if: github.event_name == 'pull_request'" in wf
    assert "github.event_name != 'pull_request'" not in wf


def test_workflow_reuses_approved_intake_without_parallel_writer():
    wf = WF.read_text()
    assert "contents: read" in wf
    assert "issues: write" not in wf
    assert "pull-requests: write" not in wf
    assert "actions: write" not in wf
    assert "engineering-provider-dispatcher" not in wf
    assert "persist-credentials: false" in wf
    assert "cancel-in-progress: false" in wf


def test_runtime_cannot_mutate_reducer_probability_or_wager_state():
    data = MODULE.read_text()
    for invariant in ("custom_gpt_identity=WOW_BETTING_ENGINE", "runtime_generation=V17_ACTIVE",
                      "terminal_authority=V17_TERMINAL_REDUCER", "can_execute=false",
                      "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true"):
        assert invariant in data
    assert "def fetch_datadog(" in data
    assert "def classify(" in data


def test_offline_regression_job_is_a_real_failing_check():
    wf = WF.read_text()
    assert "Log Reporter read-only security and regression tests" in wf
    assert "set -euo pipefail" in wf
    assert "continue-on-error: true" not in wf
    for suite in ("test_v17_log_incident_intake.py", "test_v17_log_intake_workflow.py",
                  "test_v17_log_reporter_handoff.py", "test_v17_engineering_log_reporter.py",
                  "test_v17_log_reporter_integration.py",
                  "test_v17_log_reporter_artifact_security.py"):
        assert "artifacts/wow-engine/" + suite in wf, suite


def test_retired_datadog_producer_job_and_credentials_are_removed():
    """#1021: the retired source has no producer job, opt-in path or credentials.

    Only the pull_request offline security/regression job may remain. A
    repository variable must not be able to re-enable Datadog collection.
    """
    wf = WF.read_text()
    jobs = wf.split("\njobs:\n", 1)[1]
    job_ids = re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", jobs, re.MULTILINE)
    assert job_ids == ["verify-source"], job_ids
    assert "secrets." not in wf
    assert "WOW_V17_LOG_INTAKE_ENABLED" not in wf
    assert "WOW_DATADOG" not in wf
    assert "DD_API_KEY" not in wf
    assert "upload-artifact" not in wf
    assert "log_incident_intake.py --hours" not in wf
    assert "\\n#" not in wf
