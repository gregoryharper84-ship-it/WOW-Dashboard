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
    assert "intake_exit=0" in wf
    assert "|| intake_exit=$?" in wf
    assert "WOW_LOG_INTAKE_SOURCE_UNAVAILABLE:" in wf
    assert "continue-on-error: true" not in wf
    assert "actions: write" not in wf
    assert "pull-requests: write" not in wf


def _receipt_gate_script():
    import textwrap
    wf = WF.read_text()
    marker = """          python - "$RUNNER_TEMP/wow-v17-log-intake.json" "$intake_exit" "$RUNNER_TEMP/wow-v17-log-intake-validated.json" <<'PY'\n"""
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
    import hashlib
    from datetime import datetime, timezone
    def fingerprint(service, route, code):
        return hashlib.sha256(json.dumps(
            [service, route, code], separators=(",", ":")
        ).encode()).hexdigest()
    finding = {
        "source": "DATADOG", "subsystem": "BACKEND_RUNTIME",
        "service": "wow-governed-probability-engine", "failure_boundary": "FITTED_MODEL",
        "typed_failure": "MODEL_SCORER_FAILED", "severity": "P1",
        "fingerprint": fingerprint("wow-governed-probability-engine", "FITTED_MODEL", "MODEL_SCORER_FAILED"),
        "count": 1, "first_seen_utc": "2026-11-10T01:15:00Z",
        "last_seen_utc": "2026-11-10T01:16:00Z", "release_shas": [],
        "next_owner": "REPORTER_AGENT", "state": "CANDIDATE_UNVERIFIED",
        "can_execute": False,
    }
    success.update({
        "services": ["wow-agent-worker", "wow-governed-probability-engine"],
        "window_start_utc": "2026-11-10T01:00:00Z",
        "window_end_utc": "2026-11-10T03:00:00Z",
        "events_inspected": 1,
        "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS": True,
    })
    candidates = {**success, "status": "CANDIDATES", "findings": [finding]}
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
        ("success_raw_top_level", 0, {**success, "raw": "secret"}, False),
        ("candidate_raw_key", 0, {**candidates, "findings": [{**finding, "raw": "secret"}]}, False),
        ("candidate_nested_secret", 0, {**candidates, "findings": [
            {**finding, "release_shas": [{"secret": "value"}]}
        ]}, False),
        ("candidate_bad_failure_code", 0, {**candidates, "findings": [
            {**finding, "typed_failure": "SECRET"}
        ]}, False),
        ("candidate_bad_fingerprint", 0, {**candidates, "findings": [
            {**finding, "fingerprint": "0" * 64}
        ]}, False),
        ("candidate_bad_timestamp", 0, {**candidates, "findings": [
            {**finding, "first_seen_utc": "nope"}
        ]}, False),
        ("success_extra_fields", 0, {**success, "header": "Authorization Bearer secret"}, False),
        ("hold_nested_secret", 2, {**held, "reason": {"raw": "secret"}}, False),
    ]
    script = _receipt_gate_script()
    for label, exit_code, body, expected_ok in cases:
        receipt = tmp_path / (label + ".json")
        if body is not None:
            receipt.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
        validated = tmp_path / (label + "-validated.json")
        result = subprocess.run(
            [sys.executable, "-c", script, str(receipt), str(exit_code), str(validated)],
            capture_output=True, text=True, check=False,
        )
        assert (result.returncode == 0) is expected_ok, (label, result.stderr)
        assert validated.exists() is expected_ok, label
        if expected_ok:
            assert json.loads(validated.read_text()) == body
        if label == "typed_outage":
            assert "::warning::WOW_LOG_INTAKE_SOURCE_UNAVAILABLE:DATADOG_HTTP_FAILED" in result.stdout
            assert "NO_ACTION" not in result.stdout


def test_rejected_datadog_receipt_cannot_leak_into_summary_or_artifact():
    """Workflow may preserve ONLY the independently validated receipt."""
    wf = WF.read_text()
    assert 'id: intake' in wf
    assert "if: steps.intake.outcome == 'success'" in wf
    assert 'path: ${{ runner.temp }}/wow-v17-log-intake-validated.json' in wf
    summary = wf.split("- name: Record strictly redacted Reporter candidate summary", 1)[1]
    assert 'if [ -s "$RUNNER_TEMP/wow-v17-log-intake-validated.json" ]; then' in summary
    assert 'python - "$RUNNER_TEMP/wow-v17-log-intake-validated.json"' in summary
    assert 'python - "$RUNNER_TEMP/wow-v17-log-intake.json" >> "$GITHUB_STEP_SUMMARY"' not in summary
    assert 'path: ${{ runner.temp }}/wow-v17-log-intake.json' not in wf



def test_actions_bash_errexit_preserves_typed_source_outage(tmp_path):
    """Reproduce the actual Actions bash -e wrapper, not merely its Python gate."""
    import json
    import os
    import subprocess
    import textwrap

    source = WF.read_text()
    block = source.split("- name: Query Datadog bounded error window", 1)[1]
    script = textwrap.dedent(
        block.split("        run: |\n", 1)[1].split(
            "\n      - name: Record strictly redacted Reporter candidate summary", 1
        )[0]
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python_stub = bin_dir / "python"
    python_stub.write_text("""#!/usr/bin/env python3
import os
import pathlib
import sys

if len(sys.argv) > 1 and sys.argv[1].endswith("/log_incident_intake.py"):
    output = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
    if os.environ.get("FAKE_RECEIPT") != "MISSING":
        output.write_text(os.environ["FAKE_RECEIPT"], encoding="utf-8")
    sys.exit(int(os.environ["FAKE_EXIT"]))
os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
""", encoding="utf-8")
    python_stub.chmod(0o755)
    held = {"status": "SOURCE_UNAVAILABLE", "reason": "DATADOG_HTTP_FAILED",
            "source": "DATADOG", "can_execute": False}

    for label, exit_code, payload, valid in (
        ("outage", 2, json.dumps(held), True),
        ("unexpected", 1, json.dumps(held), False),
        ("missing", 2, "MISSING", False),
    ):
        runner = tmp_path / label
        runner.mkdir()
        env = {**os.environ,
               "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
               "RUNNER_TEMP": str(runner),
               "FAKE_EXIT": str(exit_code),
               "FAKE_RECEIPT": payload,
               "DD_SITE": "datadoghq.com"}
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
            cwd=ROOT, env=env, text=True, capture_output=True, check=False,
        )
        validated = runner / "wow-v17-log-intake-validated.json"
        assert (result.returncode == 0) is valid, (label, result.returncode, result.stderr)
        assert validated.exists() is valid, label
        if valid:
            assert json.loads(validated.read_text(encoding="utf-8")) == held
            assert "WOW_LOG_INTAKE_SOURCE_UNAVAILABLE:DATADOG_HTTP_FAILED" in result.stdout
