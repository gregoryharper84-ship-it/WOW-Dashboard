"""Fail-closed contract tests for the governed Claude fallback composite action."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ACTION = Path(__file__).resolve().parents[2] / ".github/actions/wow-claude-agent/action.yml"


def _action():
    return yaml.safe_load(ACTION.read_text())


def test_oauth_retries_missing_structured_output_even_after_success():
    steps = _action()["runs"]["steps"]
    api = next(step for step in steps if step.get("id") == "claude_api")
    oauth = next(step for step in steps if step.get("id") == "claude_oauth")
    assert api["continue-on-error"] is True
    assert oauth["continue-on-error"] is True
    condition = oauth["if"].replace("\n", " ")
    assert "inputs.claude_code_oauth_token != ''" in condition
    assert "inputs.anthropic_api_key == ''" in condition
    assert "steps.claude_api.outcome == 'failure'" in condition
    assert "steps.claude_api.outputs.structured_output == ''" in condition
    # Both providers must still use the same governed tool configuration.
    assert oauth["with"]["claude_args"] == api["with"]["claude_args"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required by Ubuntu Actions runners")
@pytest.mark.parametrize("api,oauth,expected,typed_error", [
    ("", "", False, "CLAUDE_STRUCTURED_OUTPUT_MISSING"),
    ("", '{"decision":"PASS"}', True, ""),
    ('{"decision":"PASS"}', "", True, ""),
    ("not-json", "", False, "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON"),
    ("[]", "", False, "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON"),
    ("null", "", False, "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON"),
])
def test_actual_result_gate_fails_closed(api, oauth, expected, typed_error):
    action = _action()
    step = next(item for item in action["runs"]["steps"]
                if item.get("name") == "Verify provider result exists")
    env = dict(os.environ, API_RESULT=api, OAUTH_RESULT=oauth,
               API_OUTCOME="success", OAUTH_OUTCOME="success")
    result = subprocess.run(["bash", "-c", step["run"]], env=env,
                            capture_output=True, text=True, timeout=10, check=False)
    assert (result.returncode == 0) is expected, result.stderr
    if typed_error:
        assert typed_error in result.stderr


def test_output_does_not_emit_secrets_or_relax_permission_profile():
    action = _action()
    text = ACTION.read_text()
    assert 'show_full_output: "false"' in text
    assert "permission_profile" in action["inputs"]
    assert "inputs.permission_profile" in text
    guard = next(item for item in action["runs"]["steps"]
                 if item.get("name") == "Verify provider result exists")["run"]
    assert "API_RESULT" in guard and "OAUTH_RESULT" in guard
    assert 'echo "$RESULT"' not in guard
