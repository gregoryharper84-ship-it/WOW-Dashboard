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


def test_live_datadog_source_hold_is_separate_from_required_code_regression():
    wf = WF.read_text()
    assert "Log Reporter read-only security and regression tests" in wf
    assert "Redaction and failure-path tests" in wf
    assert "intake_exit=$?" in wf
    assert "WOW_LOG_INTAKE_SOURCE_UNAVAILABLE:" in wf
    assert "continue-on-error: true" not in wf
    assert "actions: write" not in wf
    assert "pull-requests: write" not in wf


def _receipt_gate_script():
    import textwrap
    wf = WF.read_text()
    marker = """          python - "$RUNNER_TEMP/wow-v17-log-intake.json" "$intake_exit" <<'PY'\n"""
    assert wf.count(marker) == 1
    script = wf.split(marker, 1)[1].split("\n          PY", 1)[0]
    return textwrap.dedent(script)


def test_datadog_receipt_gate_executes_against_valid_and_adversarial_cases(tmp_path):
    """An expected source outage must remain a typed hold, not fake NO_ACTION."""
    import json
    import subprocess
    import sys

    success = {
        "status": "NO_ACTION", "findings": [], "source": "DATADOG",
        "needs_durable_reporter_dedupe": True,
        "terminal_authority": "V17_TERMINAL_REDUCER", "can_execute": False,
    }
    candidates = {**success, "status": "CANDIDATES",
                  "findings": [{"typed_failure": "MODEL_SCORER_FAILED"}]}
    held = {"status": "SOURCE_UNAVAILABLE", "reason": "DATADOG_HTTP_FAILED",
            "source": "DATADOG", "can_execute": False}
    cases = [
        ("clean", 0, success, True),
        ("candidates", 0, candidates, True),
        ("typed_outage", 2, held, True),
        ("missing_receipt", 2, None, False),
        ("malformed_json", 2, "{", False),
        ("untyped_outage", 2, {**held, "reason": "PROVIDER_SAYS_PASS"}, False),
        ("false_success", 0, held, False),
        ("unexpected_failure", 1, held, False),
        ("wrong_provider", 2, {**held, "source": "UNTRUSTED"}, False),
        ("unsafe_execution", 2, {**held, "can_execute": True}, False),
        ("raw_response_leak", 2, {**held, "raw": "do not persist"}, False),
        ("empty_candidate_claim", 0, {**success, "status": "CANDIDATES"}, False),
        ("wrong_no_action_claim", 0, {**success, "findings": [{"x": 1}]}, False),
        ("invalid_success_receipt", 0, {**success, "terminal_authority": "UNKNOWN"}, False),
    ]
    script = _receipt_gate_script()
    for label, exit_code, body, expected_ok in cases:
        receipt = tmp_path / (label + ".json")
        if body is not None:
            receipt.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-c", script, str(receipt), str(exit_code)],
            capture_output=True, text=True, check=False,
        )
        assert (result.returncode == 0) is expected_ok, (label, result.stderr)
        if label == "typed_outage":
            assert "::warning::WOW_LOG_INTAKE_SOURCE_UNAVAILABLE:DATADOG_HTTP_FAILED" in result.stdout
            assert "NO_ACTION" not in result.stdout
