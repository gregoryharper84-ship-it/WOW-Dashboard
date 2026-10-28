"""Run offline learning regressions from the existing WOW engine test collector.

This adds no workflow, LLM, network, MLflow server or production dependency.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "learning"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, text=True, capture_output=True, check=False,
    )


def test_learning_corpus_validates():
    result = _run("evaluate.py", "validate")
    assert result.returncode == 0, result.stderr
    doc = json.loads(result.stdout)
    assert doc["validated"] is True and doc["cases"] == 5


def test_learning_evaluator_focused_regressions():
    result = _run("-m", "unittest", "discover", "-s", "tests", "-v")
    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(r"Ran (\d+) tests", result.stderr)
    assert match and int(match.group(1)) >= 23, result.stderr


def test_challenge_reference_is_green():
    result = _run("challenges/run_challenge_tests.py", "--impl", "reference")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Ran 19 tests" in result.stderr


def test_challenge_starter_is_intentionally_red():
    result = _run("challenges/run_challenge_tests.py", "--impl", "starter")
    assert result.returncode != 0, "Deliberately broken starter was incorrectly green"
    assert "FAILED (failures=" in result.stderr
    assert "Ran 19 tests" in result.stderr
