"""Verify optional continuous-learning jobs emit durable BLOCKED receipts.

The observer remains isolated from protected-main required checks: an absent
credential is a truthful research failure but must not hold unrelated deploys.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/wow-v17-continuous-intelligence.yml"


def _learn_script() -> tuple[dict, str]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["learn"]["steps"]
    step = next(item for item in steps if item.get("id") == "work")
    command = step["run"]
    source = command.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    return step, source


def test_optional_observer_isolation_remains_intact() -> None:
    step, script = _learn_script()
    assert step["continue-on-error"] is True
    assert "LEARNING_CREDENTIAL_NOT_CONFIGURED" in script
    assert "LEARNING_ZERO_CAPTURE_NOT_VERIFIED" in script
    assert "V17_TERMINAL_REDUCER" in script or "V17_TERMINAL_REDUCER" in WORKFLOW.read_text()


def test_missing_credential_emits_durable_typed_receipt(tmp_path: Path) -> None:
    step, script = _learn_script()
    summary_path = tmp_path / "summary.md"
    env = {
        **os.environ,
        "WOW_ACTION_API_KEY": "",
        "WOW_CAN_EXECUTE": "false",
        "WOW_DRY_RUN_ONLY": "true",
        "WOW_ACTION_ORIGIN": "https://unreachable.invalid",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(summary_path),
    }
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, cwd=ROOT,
        text=True, capture_output=True, timeout=10, check=False,
    )
    assert result.returncode != 0
    assert "LEARNING_CREDENTIAL_NOT_CONFIGURED" in result.stderr
    receipt_path = tmp_path / "wow-v17-learning-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "BLOCKED"
    assert receipt["blocker"] == "LEARNING_CREDENTIAL_NOT_CONFIGURED"
    assert receipt["can_execute"] is False
    assert receipt["automatic_promotion_allowed"] is False
    assert "BLOCKED: LEARNING_CREDENTIAL_NOT_CONFIGURED" in summary_path.read_text()
    assert "Status: PASS" not in summary_path.read_text()


def test_bad_capture_count_writes_only_blocked_not_false_pass(tmp_path: Path) -> None:
    _, script = _learn_script()
    safe_result = {
        "schema_version": "WOW17_CONTINUOUS_INTELLIGENCE_V1",
        "status": "PASS", "can_execute": False,
        "automatic_promotion_allowed": False,
        "model_parameters_changed": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "authority": "ADVISORY_ONLY",
        "completed_stages": 4, "total_stages": 4,
        "stages": [
            {"stage": stage, "status": "PASS", "can_execute": False,
             "observation_n": ("invalid" if i == 0 else 1)}
            for i, stage in enumerate([
                "PROP_CAPTURE", "EVENT_CAPTURE", "COHORT_SUMMARY",
                "COMPOUNDING_MEMORY",
            ])
        ],
    }
    mock = (
        "import io, json, urllib.request\n"
        "class StubResponse(io.BytesIO): pass\n"
        f"_receipt = {repr(safe_result)}\n"
        "urllib.request.urlopen = lambda *a, **kw: "
        "StubResponse(json.dumps(_receipt).encode('utf-8'))\n"
    )
    summary = tmp_path / "summary.md"
    env = {
        **os.environ,
        "WOW_ACTION_API_KEY": "synthetic-test-only",
        "WOW_CAN_EXECUTE": "false", "WOW_DRY_RUN_ONLY": "true",
        "WOW_ACTION_ORIGIN": "https://synthetic.invalid",
        "RUNNER_TEMP": str(tmp_path), "GITHUB_STEP_SUMMARY": str(summary),
    }
    process = subprocess.run(
        [sys.executable, "-c", mock + script], env=env, cwd=ROOT,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert process.returncode != 0
    assert "LEARNING_CAPTURE_COUNT_INVALID" in process.stderr
    receipt = json.loads((tmp_path / "wow-v17-learning-receipt.json").read_text())
    assert receipt["status"] == "BLOCKED"
    assert receipt["blocker"] == "LEARNING_CAPTURE_COUNT_INVALID"
    assert receipt["can_execute"] is False
    assert "Status: PASS" not in summary.read_text()


def test_zero_capture_is_not_success(tmp_path: Path) -> None:
    _, script = _learn_script()
    result = {
        "schema_version": "WOW17_CONTINUOUS_INTELLIGENCE_V1",
        "status": "PASS", "can_execute": False,
        "automatic_promotion_allowed": False,
        "model_parameters_changed": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "authority": "ADVISORY_ONLY", "completed_stages": 4, "total_stages": 4,
        "stages": [
            {"stage": stage, "status": "PASS", "can_execute": False,
             "observation_n": 0}
            for stage in [
                "PROP_CAPTURE", "EVENT_CAPTURE", "COHORT_SUMMARY",
                "COMPOUNDING_MEMORY",
            ]
        ],
    }
    stub = (
        "import io, json, urllib.request\n"
        f"_data = {repr(result)}\n"
        "urllib.request.urlopen = lambda *a, **kw: io.BytesIO(json.dumps(_data).encode())\n"
    )
    env = {
        **os.environ, "WOW_ACTION_API_KEY": "synthetic-test-only",
        "WOW_CAN_EXECUTE": "false", "WOW_DRY_RUN_ONLY": "true",
        "WOW_ACTION_ORIGIN": "https://synthetic.invalid",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }
    process = subprocess.run(
        [sys.executable, "-c", stub + script], env=env, cwd=ROOT,
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert process.returncode != 0
    assert "LEARNING_ZERO_CAPTURE_NOT_VERIFIED" in process.stderr
    receipt = json.loads((tmp_path / "wow-v17-learning-receipt.json").read_text())
    assert receipt["status"] == "BLOCKED"
