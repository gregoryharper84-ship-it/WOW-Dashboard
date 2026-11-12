"""P0 #2422: optional main-scoped observers must not gate Render checksPass.

GitHub attaches every job's check run to the workflow-run head SHA, which for
``schedule``/``workflow_run`` events is the protected ``main`` tip. Render's
``checksPass`` trigger refuses to deploy while any check run on that SHA is
``failure``, so an autonomous/optional observer that ends red blocks deploys of
already-merged commits it never touched.

``jobs.<id>.continue-on-error`` does *not* fix this: the job's check run still
concludes ``failure`` (the workflow run merely reports success). Only a step
whose final conclusion is ``success`` keeps the job check green. These tests
therefore pin step-level isolation for the optional observers and require that
their authentic typed reasons remain in the durable step summary.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import tempfile
import time

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"

# Optional, autonomous jobs that never carry merge/deploy authority and whose
# check runs land on the protected-main SHA they did not produce.
OPTIONAL_MAIN_SCOPED_JOBS: dict[str, tuple[str, ...]] = {
    "wow-v17-scout-persist-resume.yml": ("resume-persistence",),
    "wow-v17-action-origin-keepalive.yml": ("keepalive",),
    "kalshi-weather-external-wake.yml": ("heartbeat",),
    "wow-v17-golden-product-acceptance.yml": ("product-acceptance",),
    "wow-v17-nightly-engineering-scan.yml": ("nightly-engineering-scan",),
    "wow-v17-lean-production-readback.yml": ("production-readback",),
    "wow-v17-main-verification-reconciler.yml": ("reconcile",),
    "wow-v17-engineering-auto-repair.yml": (
        "discover",
        "adoption-readonly-preflight",
        "adopt-reviewed-owner-source",
        "route-hold-findings",
    ),
}


def _workflow(name: str) -> dict:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(document, dict), name
    return document


def test_optional_main_scoped_observers_cannot_fail_a_main_commit_check() -> None:
    for name, jobs in OPTIONAL_MAIN_SCOPED_JOBS.items():
        document = _workflow(name)
        for job in jobs:
            steps = document["jobs"][job]["steps"]
            assert steps, f"{name}:{job} has no steps"
            exposed = [
                step.get("name")
                for step in steps
                if step.get("continue-on-error") is not True
            ]
            assert not exposed, (
                f"{name}:{job} can still publish a failing check on protected main: {exposed}"
            )


def test_optional_observers_keep_authentic_typed_reasons() -> None:
    """Isolation must not drop the typed HOLD/FAIL reason or its durability."""
    for name, jobs in OPTIONAL_MAIN_SCOPED_JOBS.items():
        document = _workflow(name)
        for job in jobs:
            scripts = "\n".join(
                str(step.get("run", "")) for step in document["jobs"][job]["steps"]
            )
            assert "GITHUB_STEP_SUMMARY" in scripts, (
                f"{name}:{job} no longer records its outcome in the durable step summary"
            )
            typed = ("::error::", "_HOLD", "_INCOMPLETE", "_UNAVAILABLE", "_UNCONFIRMED")
            assert any(marker in scripts for marker in typed), (
                f"{name}:{job} no longer emits a typed reason"
            )


def test_scout_recovery_never_acts_on_an_unresolved_source() -> None:
    """Isolation must not let later steps dispatch a writer with no source id."""
    document = _workflow("wow-v17-scout-persist-resume.yml")
    steps = document["jobs"]["resume-persistence"]["steps"]
    dependent = [
        "Verify recoverable discovery artifact exists",
        "Capture persistence receipt baseline",
        "Allow native workflow_run persistence to win first",
        "Check for existing persistence receipt",
        "Detect same-source persistence writer state",
        "Explicitly resume Scout persistence",
        "Verify persistence receipt appears",
    ]
    by_name = {step["name"]: step for step in steps}
    for name in dependent:
        condition = str(by_name[name].get("if", ""))
        assert "steps.source.outputs.run_id != ''" in condition, (
            f"{name} can run without a resolved source run id"
        )


def test_required_merge_gates_are_not_blanket_suppressed() -> None:
    """Isolation is limited to optional observers; merge gates stay fail-closed."""
    for gate in ("wow-verify.yml", "wow-engine-verify.yml"):
        document = _workflow(gate)
        for job, spec in document["jobs"].items():
            steps = spec.get("steps") or []
            if not steps:
                continue
            assert any(step.get("continue-on-error") is not True for step in steps), (
                f"{gate}:{job} fully suppresses failures and can no longer gate a merge"
            )


def test_keepalive_requires_successful_curl_exit_and_http_status() -> None:
    """An HTTP 200 with truncated transfer must not emit a false-green receipt."""
    steps = _workflow("wow-v17-action-origin-keepalive.yml")["jobs"]["keepalive"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Keep V17 Action origin warm")
    assert 'curl_status=0' in script
    assert '|| curl_status=$?' in script
    assert 'if [ "$curl_status" -ne 0 ] || [ "$code" != "200" ]; then' in script
    assert "ACTION_ORIGIN_KEEPALIVE_UNREACHABLE" in script


def test_weather_wake_probe_has_global_deadline_and_fail_receipt() -> None:
    """Slow trickle responses must not consume the entire bounded job window."""
    job = _workflow("kalshi-weather-external-wake.yml")["jobs"]["heartbeat"]
    scripts = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert job["timeout-minutes"] == 8
    assert "WAKE_HEARTBEAT_DEADLINE_EPOCH" in scripts
    assert "timeout --signal=TERM --kill-after=5s" in scripts
    assert "deadline_exceeded=true" in scripts
    assert "KALSHI_WEATHER_WAKE_PROBE_FAILED" in scripts


def test_golden_contract_failure_survives_optional_step_isolation() -> None:
    """Receipt and summary must disclose the raw pytest failure outcome."""
    job = _workflow("wow-v17-golden-product-acceptance.yml")["jobs"]["product-acceptance"]
    steps = job["steps"]
    contracts = next(step for step in steps if step.get("id") == "contract_tests")
    assert contracts["continue-on-error"] is True
    for name in ("Enforce false-green boundary", "Record product-acceptance receipt"):
        step = next(item for item in steps if item.get("name") == name)
        assert step["env"]["CONTRACT_TEST_OUTCOME"] == "${{ steps.contract_tests.outcome }}"
        assert "CONTRACT_TEST_OUTCOME" in step["run"]
    receipt = next(item["run"] for item in steps if item.get("name") == "Record product-acceptance receipt")
    assert "contract_tests_outcome:$contract_tests_outcome" in receipt


def test_observer_audit_does_not_attribute_postfix_counts_to_base_sha() -> None:
    text = (
        ROOT
        / "artifacts/wow-engine/v17/engineering-fixes"
        / "AUDIT-2026-11-11-001__main-scoped-check-exposure.md"
    ).read_text(encoding="utf-8")
    assert "Post-fix worktree count" in text
    assert "not** a measurement" in text
    assert "Historical unisolated-optional-job candidate inventory" in text


def _run_isolated_bash(
    script: str,
    *,
    prefix: str = "",
    extra_env: dict[str, str] | None = None,
    stall_python: bool = False,
) -> dict[str, object]:
    """Exercise the actual workflow shell, not just check that tokens exist."""
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        summary = directory / "summary.md"
        outputs = directory / "outputs"
        env = os.environ.copy()
        env.update(
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_OUTPUT=str(outputs),
            RUNNER_TEMP=str(directory),
        )
        if stall_python:
            fakebin = directory / "fakebin"
            fakebin.mkdir()
            executable = fakebin / "python3"
            executable.write_text("#!/bin/sh\nexec sleep 30\n", encoding="utf-8")
            executable.chmod(0o755)
            env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        env.update(extra_env or {})
        started = time.monotonic()
        completed = subprocess.run(
            ["bash", "-c", prefix + "\n" + script],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
        receipt = directory / "golden-product-acceptance.json"
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "summary": summary.read_text(encoding="utf-8") if summary.exists() else "",
            "outputs": outputs.read_text(encoding="utf-8") if outputs.exists() else "",
            "receipt": receipt.read_text(encoding="utf-8") if receipt.exists() else "",
            "elapsed": time.monotonic() - started,
        }


def test_keepalive_http_200_with_curl_transport_error_is_not_ok() -> None:
    """A truncated HTTP 200 is a transport failure, never a green heartbeat."""
    steps = _workflow("wow-v17-action-origin-keepalive.yml")["jobs"]["keepalive"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Keep V17 Action origin warm")
    result = _run_isolated_bash(
        script,
        prefix="curl() { printf '200'; return 18; }",
    )
    assert result["returncode"] != 0
    assert "ACTION_ORIGIN_KEEPALIVE_UNREACHABLE" in result["summary"]
    assert "curl_exit=18" in result["summary"]
    assert "ACTION_ORIGIN_KEEPALIVE_OK" not in result["summary"]


def test_weather_stalled_probe_is_bounded_and_emits_failure_receipt() -> None:
    """A stalled Python process must not block until job-level timeout."""
    steps = _workflow("kalshi-weather-external-wake.yml")["jobs"]["heartbeat"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Wake API, verify DB-read and shadow collector flags")
    result = _run_isolated_bash(
        script,
        stall_python=True,
        extra_env={"WAKE_HEARTBEAT_DEADLINE_EPOCH": str(int(time.time()) + 3)},
    )
    assert result["returncode"] != 0
    assert result["elapsed"] < 8
    assert "KALSHI_WEATHER_WAKE_PROBE_FAILED" in result["summary"]
    assert "KALSHI_WEATHER_WAKE_PROBE_OK" not in result["summary"]


def test_golden_health_command_failure_is_typed_and_nonpublishable() -> None:
    steps = _workflow("wow-v17-golden-product-acceptance.yml")["jobs"]["product-acceptance"]["steps"]
    script = next(step["run"] for step in steps if step.get("id") == "health")
    result = _run_isolated_bash(script, prefix="python() { return 23; }")
    assert result["returncode"] != 0
    assert "status=GOLDEN_ACCEPTANCE_HEALTH_READ_UNAVAILABLE" in result["outputs"]
    assert "GOLDEN_ACCEPTANCE_HEALTH_READ_UNAVAILABLE" in result["summary"]


def test_golden_receipt_uses_checked_out_sha_not_dispatch_sha() -> None:
    steps = _workflow("wow-v17-golden-product-acceptance.yml")["jobs"]["product-acceptance"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Record product-acceptance receipt")
    result = _run_isolated_bash(
        script,
        extra_env={
            "PRODUCT_STATUS": "FAIL",
            "HEALTH_READ_OUTCOME": "success",
            "BACKEND_STATUS": "FAIL",
            "CONTRACT_TEST_OUTCOME": "failure",
            "CHECKOUT_SHA_OUTCOME": "success",
            "CHECKED_OUT_SHA": "a" * 40,
            "GITHUB_SHA": "f" * 40,
        },
    )
    assert result["returncode"] == 0, result["stderr"]
    receipt = json.loads(str(result["receipt"]))
    assert receipt["sha"] == "a" * 40
    assert receipt["sha"] != "f" * 40
    assert receipt["contract_tests_outcome"] == "failure"
    assert receipt["health_read_outcome"] == "success"
    assert receipt["can_execute"] is False


def test_golden_receipt_invalid_checkout_sha_is_explicitly_unverified() -> None:
    steps = _workflow("wow-v17-golden-product-acceptance.yml")["jobs"]["product-acceptance"]["steps"]
    script = next(step["run"] for step in steps if step.get("name") == "Record product-acceptance receipt")
    result = _run_isolated_bash(
        script,
        extra_env={
            "PRODUCT_STATUS": "",
            "HEALTH_READ_OUTCOME": "failure",
            "BACKEND_STATUS": "FAIL",
            "CONTRACT_TEST_OUTCOME": "failure",
            "CHECKOUT_SHA_OUTCOME": "failure",
            "CHECKED_OUT_SHA": "invalid",
            "GITHUB_SHA": "f" * 40,
        },
    )
    assert result["returncode"] == 0, result["stderr"]
    receipt = json.loads(str(result["receipt"]))
    assert receipt["sha"] == "UNVERIFIED"
    assert receipt["user_journey_status"] == "GOLDEN_ACCEPTANCE_HEALTH_READ_UNAVAILABLE"
    assert receipt["checkout_sha_outcome"] == "failure"
    assert "GOLDEN_ACCEPTANCE_CHECKOUT_SHA_UNVERIFIED" in result["summary"]
