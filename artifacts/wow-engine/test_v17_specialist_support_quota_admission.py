"""Regression: scheduled specialist support cannot bypass the shared provider breaker."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-engineering-specialist-support.yml"
STEPS = yaml.safe_load(WORKFLOW.read_text())["jobs"]["support-active-closure"]["steps"]


def _step(name):
    return next(step for step in STEPS if step["name"] == name)


def _stamp(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def _record(run_id, conclusion, minutes_ago):
    return {"databaseId": run_id, "conclusion": conclusion, "updatedAt": _stamp(minutes_ago)}


def _run_preflight(tmp_path, *, primary=None, support=None, logs=None, key="test-only",
                   inventory_error=False):
    stub = tmp_path / "bin" / "gh"
    stub.parent.mkdir(parents=True)
    # Workflow admission invokes bare `python`; local hosts may only expose python3.
    python_shim = stub.parent / "python"
    if not python_shim.exists():
        python_shim.symlink_to(sys.executable)
    stub.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ["run", "list"]:
    if os.environ.get("FAKE_INVENTORY_ERROR") == "true":
        sys.exit(1)
    workflow = args[args.index("--workflow") + 1]
    print(os.environ["FAKE_PRIMARY"] if workflow == "wow-v17-chatgpt-engineering-worker.yml"
          else os.environ["FAKE_SUPPORT"])
elif args[:2] == ["run", "view"]:
    logs = json.loads(os.environ["FAKE_LOGS"])
    text = logs.get(args[2])
    if text is None:
        sys.exit(1)
    print(text)
else:
    sys.exit(2)
""")
    stub.chmod(0o755)
    outfile = tmp_path / "out"
    outfile.touch()
    summary = tmp_path / "summary"
    summary.touch()
    env = dict(os.environ)
    env.update({
        "PATH": str(stub.parent) + os.pathsep + env["PATH"],
        "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(outfile),
        "GITHUB_STEP_SUMMARY": str(summary),
        "OPENAI_API_KEY": key,
        "FAKE_PRIMARY": json.dumps(primary or []),
        "FAKE_SUPPORT": json.dumps(support or []),
        "FAKE_LOGS": json.dumps(logs or {}),
        "FAKE_INVENTORY_ERROR": str(inventory_error).lower(),
    })
    proc = subprocess.run(["bash", "-c", _step("Check shared OpenAI provider admission")["run"]],
                          cwd=ROOT, env=env, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in outfile.read_text().splitlines())


def test_provider_guard_precedes_any_paid_agent_and_receipt_is_typed():
    names = [step["name"] for step in STEPS]
    assert names.index("Check shared OpenAI provider admission") < names.index("Verify OpenAI authentication")
    assert names.index("Check shared OpenAI provider admission") < names.index("Specialist support subagent")
    paid = _step("Specialist support subagent")
    assert "steps.provider_admission.outputs.admit == 'true'" in paid["if"]
    assert "steps.target.outputs.action != 'NO_ACTION'" in paid["if"]
    assert "CIRCUIT_BREAKER" in _step("Check shared OpenAI provider admission")["run"]
    assert "engineering_provider_failover.py classify" in _step("Check shared OpenAI provider admission")["run"]
    assert "engineering_provider_failover.py circuit-breaker" in _step("Check shared OpenAI provider admission")["run"]
    assert "steps.provider_admission.outputs.reason" in _step("Record specialist support receipt")["run"]
    hold = _step("Surface quota-admission hold without invoking Codex")
    assert "steps.target.outputs.action != 'NO_ACTION'" in hold["if"]
    assert "exit 1" in hold["run"]
    assert "can_execute: false" in _step("Record specialist support receipt")["run"]


def test_primary_quota_failure_blocks_support_without_another_codex_call(tmp_path):
    result = _run_preflight(tmp_path, primary=[_record(101, "failure", 180)],
                            logs={"101": "ERROR: Quota exceeded. Check your plan and billing details."})
    assert result == {"admit": "false", "reason": "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED"}


def test_support_quota_failure_stays_blocked_despite_older_primary_success(tmp_path):
    result = _run_preflight(tmp_path, primary=[_record(201, "success", 90)],
                            support=[_record(202, "failure", 30)],
                            logs={"202": "ERROR: Quota exceeded. Check your plan and billing details."})
    assert result["admit"] == "false"
    assert result["reason"] == "CIRCUIT_BREAKER:OPENAI_API_QUOTA_EXCEEDED"


def test_newer_primary_success_clears_only_previous_support_breaker(tmp_path):
    result = _run_preflight(tmp_path, primary=[_record(301, "success", 10)],
                            support=[_record(302, "failure", 50)],
                            logs={"302": "ERROR: Quota exceeded. Check your plan and billing details."})
    assert result == {"admit": "true", "reason": "PROVIDER_ADMISSION_CLEAR"}


def test_temporary_rate_limit_observes_shared_cooldown(tmp_path):
    result = _run_preflight(tmp_path, primary=[_record(401, "failure", 5)],
                            logs={"401": "HTTP 429: rate limit exceeded"})
    assert result == {"admit": "false", "reason": "CIRCUIT_BREAKER:OPENAI_API_RATE_LIMITED"}


def test_no_failure_or_missing_key_or_unverified_inventory(tmp_path):
    healthy = _run_preflight(tmp_path / "healthy", primary=[_record(501, "success", 5)])
    assert healthy == {"admit": "true", "reason": "PROVIDER_ADMISSION_CLEAR"}
    # Separate temporary directories because the fake gh executable is created once.
    missing = _run_preflight(tmp_path / "missing", key="")
    assert missing == {"admit": "false", "reason": "OPENAI_API_KEY_MISSING"}
    inventory = _run_preflight(tmp_path / "inventory", inventory_error=True)
    assert inventory == {"admit": "false", "reason": "PRIMARY_WORKER_INVENTORY_UNVERIFIED"}


def test_actions_script_echo_of_key_required_does_not_trip_primary_breaker(tmp_path):
    """GitHub Actions dumps step scripts into --log-failed output.

    Live ChatGPT worker failures (e.g. PROVIDER_WORKER_INVENTORY_UNVERIFIED)
    still contain the unevaluated line
    `echo "::error::OPENAI_API_KEY is required..."`. Matching that script echo
    as OPENAI_API_KEY_MISSING permanently blocked specialist support even when
    the secret was present and the real failure was non-provider.
    """
    script_echo_log = "\n".join([
        "##[group]Run Verify OpenAI authentication",
        'echo "::error::OPENAI_API_KEY is required for the ChatGPT engineering worker."',
        "##[endgroup]",
        "##[error]Process completed with exit code 1.",
    ])
    result = _run_preflight(
        tmp_path,
        primary=[_record(601, "failure", 40)],
        logs={"601": script_echo_log},
        key="present-test-key",
    )
    assert result == {"admit": "true", "reason": "PROVIDER_ADMISSION_CLEAR"}


def test_emitted_key_required_error_still_trips_breaker(tmp_path):
    emitted = "\n".join([
        "##[group]Run Verify OpenAI authentication",
        'echo "::error::OPENAI_API_KEY is required for the ChatGPT engineering worker."',
        "##[endgroup]",
        "##[error]OPENAI_API_KEY is required for the ChatGPT engineering worker.",
    ])
    result = _run_preflight(
        tmp_path,
        primary=[_record(701, "failure", 40)],
        logs={"701": emitted},
        key="present-test-key",
    )
    assert result == {"admit": "false", "reason": "CIRCUIT_BREAKER:OPENAI_API_KEY_MISSING"}


def test_specialist_hold_error_does_not_self_classify_as_quota(tmp_path):
    """The Surface hold step always contains the word 'Quota exceeded' in its
    script source. Only an emitted quota error may keep the breaker open.
    """
    hold_log = "\n".join([
        "##[group]Run Surface quota-admission hold without invoking Codex",
        'if [[ "$PROVIDER_REASON" == *OPENAI_API_QUOTA_EXCEEDED* ]]; then',
        '  echo "::error::Quota exceeded: BLOCKED_WITH_EXACT_REASON:${PROVIDER_REASON}; Codex not invoked"',
        "else",
        '  echo "::error::BLOCKED_WITH_EXACT_REASON:${PROVIDER_REASON}; Codex not invoked"',
        "fi",
        "##[endgroup]",
        "##[error]BLOCKED_WITH_EXACT_REASON:CIRCUIT_BREAKER:OPENAI_API_KEY_MISSING; Codex not invoked",
    ])
    result = _run_preflight(
        tmp_path,
        primary=[_record(801, "success", 90)],
        support=[_record(802, "failure", 10)],
        logs={"802": hold_log},
        key="present-test-key",
    )
    assert result == {"admit": "true", "reason": "PROVIDER_ADMISSION_CLEAR"}
