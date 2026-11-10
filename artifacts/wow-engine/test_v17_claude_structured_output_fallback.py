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
    assert len([step for step in steps if step.get("id") == "claude_api"]) == 1
    assert len([step for step in steps if step.get("id") == "claude_oauth"]) == 1


def _render_claude_args(profile: str, schema: str, tmp_path: Path) -> str:
    config = next(step for step in _action()["runs"]["steps"] if step.get("id") == "config")
    output = tmp_path / "github_output.txt"
    output.unlink(missing_ok=True)
    env = dict(os.environ, PROFILE=profile, OUTPUT_SCHEMA=schema, GITHUB_OUTPUT=str(output))
    result = subprocess.run(["bash", "-c", config["run"]], env=env,
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    return output.read_text()


def test_both_auth_modes_pin_the_model_that_returned_structured_json(tmp_path):
    """API-key mode otherwise defaults to claude-opus-5-5, which returned no structured output."""
    schema = '{"type":"object","additionalProperties":false}'
    rendered = _render_claude_args(":read-only", schema, tmp_path)
    assert "--model claude-sonnet-5-5" in rendered
    assert "--max-turns 24" in rendered
    assert "Edit" not in rendered and "Write" not in rendered
    assert "--json-schema" in rendered
    assert schema in rendered
    workspace = _render_claude_args(":workspace", schema, tmp_path)
    assert "--model claude-sonnet-5-5" in workspace
    assert "--max-turns 40" in workspace
    assert "Edit,Read,Write" in workspace


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq required by Ubuntu Actions runners")
@pytest.mark.parametrize("api,oauth,api_outcome,oauth_outcome,expected,typed_error", [
    # Provider exits 0 and omits structured output. That is not an approval.
    ("", "", "success", "success", False, "CLAUDE_STRUCTURED_OUTPUT_MISSING"),
    # OAuth recovery after the API attempt returns no object.
    ("", '{"decision":"PASS"}', "failure", "success", True, ""),
    # API success is sufficient. OAuth must not be required.
    ('{"decision":"PASS"}', "", "success", "skipped", True, ""),
    ("not-json", "", "success", "skipped", False, "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON"),
    ("[]", "", "success", "skipped", False, "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON"),
    ("null", "", "success", "skipped", False, "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON"),
    # Freeform text that names an approval is still invalid JSON.
    ("decision: PASS approved", "", "success", "success", False, "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON"),
    # Both providers failed and neither returned an object.
    ("", "", "failure", "failure", False, "CLAUDE_STRUCTURED_OUTPUT_MISSING"),
])
def test_actual_result_gate_fails_closed(api, oauth, api_outcome, oauth_outcome, expected, typed_error):
    action = _action()
    step = next(item for item in action["runs"]["steps"]
                if item.get("name") == "Verify provider result exists")
    env = dict(os.environ, API_RESULT=api, OAUTH_RESULT=oauth,
               API_OUTCOME=api_outcome, OAUTH_OUTCOME=oauth_outcome)
    result = subprocess.run(["bash", "-c", step["run"]], env=env,
                            capture_output=True, text=True, timeout=10, check=False)
    output = result.stdout + result.stderr
    assert (result.returncode == 0) is expected, output
    assert f"api_outcome={api_outcome}" in output
    assert f"oauth_outcome={oauth_outcome}" in output
    assert "api_output_present=" in output and "oauth_output_present=" in output
    if api:
        assert api not in output
    if oauth:
        assert oauth not in output
    if typed_error:
        # GitHub Actions annotations (::error::) are written to stdout;
        # jq diagnostics, when present, are written to stderr.
        assert f"::error::{typed_error}" in output
    if api_outcome == "failure" and oauth_outcome == "failure":
        assert "CLAUDE_STRUCTURED_OUTPUT_MISSING" in output


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
    assert "model=claude-sonnet-5-5" in guard
    assert "api_output_present=" in guard


def test_verified_incident_lookup_has_only_narrow_read_only_claude_tool_lease(tmp_path):
    """The existing triage agent may query proven history without shell escalation."""
    lookup = "Bash(python artifacts/wow-engine/v17/engineering_lesson_retrieval.py:*)"
    for profile in (":read-only", ":workspace"):
        rendered = _render_claude_args(profile, '{"type":"object"}', tmp_path)
        assert rendered.count(lookup) == 1
        assert "Bash(python:*" not in rendered
        assert "Bash(bash:" not in rendered
        assert "Bash(sh:" not in rendered
        if profile == ":read-only":
            assert "Edit," not in rendered
            assert "Write," not in rendered
        else:
            assert "Edit,Read,Write" in rendered


def test_verified_lesson_tool_lease_is_not_arbitrary_python_or_shell():
    action = _action()
    config = next(x for x in action["runs"]["steps"] if x.get("id") == "config")
    script = config["run"]
    assert script.count("Bash(python artifacts/wow-engine/v17/engineering_lesson_retrieval.py:*)") == 2
    assert "permission_profile" in action["inputs"]
    assert "show_full_output: \"false\"" in ACTION.read_text()
