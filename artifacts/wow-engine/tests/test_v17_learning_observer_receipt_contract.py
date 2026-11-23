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
