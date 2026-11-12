"""Negative tests for redacted Claude action provider-error observation (#1021)."""
from __future__ import annotations

import json
import os
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ENGINE = Path(__file__).resolve().parent
REPO = ENGINE.parents[1]
ACTION = REPO / ".github/actions/wow-claude-agent/action.yml"
SCRIPT = REPO / ".github/actions/wow-claude-agent/claude_execution_outcome_diagnostic.py"
SPEC = importlib.util.spec_from_file_location("_protected_claude_diagnostic_test", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
inspect_execution_file = MODULE.inspect_execution_file
SENTINEL = "sk-ant-unprintable-sentinel-only-for-testing"


def _file(tmp_path: Path, entries, *, ndjson=False):
    f = tmp_path / "execution.json"
    if ndjson:
        f.write_text("\n".join(json.dumps(x) for x in entries), encoding="utf-8")
    else:
        f.write_text(json.dumps(entries), encoding="utf-8")
    return str(f)


@pytest.mark.parametrize("status,expected", [
    (429, "PROVIDER_HTTP_429_QUOTA_OR_RATE_LIMIT"),
    (401, "PROVIDER_HTTP_401_AUTHENTICATION_REJECTED"),
    (403, "PROVIDER_HTTP_403_PERMISSION_OR_WORKSPACE_REJECTED"),
    (529, "PROVIDER_HTTP_529_PROVIDER_CAPACITY"),
    (500, "PROVIDER_HTTP_500_PROVIDER_SERVER_FAILURE"),
    (418, "PROVIDER_HTTP_418_HTTP_REJECTED"),
])
def test_sdk_integer_http_status_is_reported_as_safe_type(tmp_path, status, expected):
    event = {"type": "result", "subtype": "success", "is_error": True,
             "api_error_status": status, "result": SENTINEL + " quota details"}
    f = _file(tmp_path, [{"type": "system", "subtype": "init"}, event], ndjson=True)
    assert inspect_execution_file(f, runner_temp=str(tmp_path)) == expected


@pytest.mark.parametrize("malformed", ["429", True, 429.0, None, SENTINEL, [], {}])
def test_untrusted_http_status_is_never_coerced(tmp_path, malformed):
    f = _file(tmp_path, {"type": "result", "is_error": True,
                         "api_error_status": malformed, "result": SENTINEL})
    assert inspect_execution_file(f, runner_temp=str(tmp_path)) == (
        "SDK_RESULT_IS_ERROR_HTTP_UNAVAILABLE"
    )


def test_no_false_failure_report_for_successful_sdk_result(tmp_path):
    f = _file(tmp_path, {"type": "result", "is_error": False,
                         "api_error_status": 429, "result": SENTINEL})
    assert inspect_execution_file(f, runner_temp=str(tmp_path)) == (
        "SDK_RESULT_NOT_MARKED_ERROR"
    )


def test_denies_symlink_escape_even_with_numeric_429(tmp_path):
    outside = tmp_path.parent / "outside-claude-execution.json"
    outside.write_text(json.dumps({"type": "result", "is_error": True,
                                  "api_error_status": 429}), encoding="utf-8")
    sibling = tmp_path / "escaped.json"
    sibling.symlink_to(outside)
    try:
        assert inspect_execution_file(str(sibling), runner_temp=str(tmp_path)) == (
            "EXECUTION_FILE_NOT_TRUSTED"
        )
    finally:
        outside.unlink(missing_ok=True)


def test_oversize_missing_and_malformed_execution_files_fail_closed(tmp_path):
    f = tmp_path / "big.json"
    f.write_bytes(b"x" * (1_048_576 + 1))
    assert inspect_execution_file(str(f), runner_temp=str(tmp_path)) == (
        "EXECUTION_FILE_OVERSIZE"
    )
    assert inspect_execution_file("", runner_temp=str(tmp_path)) == (
        "EXECUTION_FILE_NOT_PROVIDED"
    )
    f.write_text("invalid raw model error " + SENTINEL)
    assert inspect_execution_file(str(f), runner_temp=str(tmp_path)) == (
        "SDK_RESULT_EVENT_MISSING"
    )


def test_execution_diagnostic_never_prints_result_secrets_or_file_path(tmp_path, monkeypatch):
    f = _file(tmp_path, {"type": "result", "is_error": True,
                         "api_error_status": 429, "result": SENTINEL})
    monkeypatch.setenv("CLAUDE_EXECUTION_FILE", f)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("CLAUDE_ATTEMPT", "API_KEY")
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                            text=True, timeout=10, check=False)
    assert result.returncode == 0
    assert "API_KEY:PROVIDER_HTTP_429_QUOTA_OR_RATE_LIMIT" in result.stdout
    assert SENTINEL not in result.stdout + result.stderr
    assert str(tmp_path) not in result.stdout + result.stderr


def test_untrusted_attempt_label_never_becomes_log_control_text(tmp_path, monkeypatch):
    f = _file(tmp_path, {"type": "result", "is_error": True,
                         "api_error_status": 429, "result": SENTINEL})
    monkeypatch.setenv("CLAUDE_EXECUTION_FILE", f)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("CLAUDE_ATTEMPT", "::error::INJECTED")
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                            text=True, timeout=10, check=False)
    assert result.returncode == 0
    assert "UNKNOWN:PROVIDER_HTTP_429_QUOTA_OR_RATE_LIMIT" in result.stdout
    assert "INJECTED" not in result.stdout


def test_composite_diagnostic_runs_before_existing_result_gate_for_both_providers():
    document = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    steps = document["runs"]["steps"]
    by_name = {step["name"]: step for step in steps}
    gate = "Verify provider result exists"
    api = "Classify API-key provider failure without provider text"
    oauth = "Classify OAuth provider failure without provider text"
    assert steps.index(by_name[api]) < steps.index(by_name["Run governed Claude agent with OAuth"])
    assert steps.index(by_name[oauth]) < steps.index(by_name[gate])
    api_condition = by_name[api]["if"]
    oauth_condition = by_name[oauth]["if"]
    for condition, key in ((api_condition, "claude_api"), (oauth_condition, "claude_oauth")):
        assert "always()" in condition
        assert f"steps.{key}.outcome == 'failure'" in condition
        # The observed SDK error returns action outcome=success with empty
        # structured_output; it must still run the sanitized diagnostic.
        assert f"steps.{key}.outcome == 'success'" in condition
        assert f"steps.{key}.outputs.structured_output == ''" in condition
        assert "outcome == 'skipped'" not in condition
    assert by_name[api]["env"]["CLAUDE_EXECUTION_FILE"] == (
        "${{ steps.claude_api.outputs.execution_file }}"
    )
    assert by_name[oauth]["env"]["CLAUDE_EXECUTION_FILE"] == (
        "${{ steps.claude_oauth.outputs.execution_file }}"
    )
    assert by_name[api]["run"] == by_name[oauth]["run"] == (
        "python3 .github/actions/wow-claude-agent/claude_execution_outcome_diagnostic.py"
    )
    assert "CLAUDE_STRUCTURED_OUTPUT_MISSING" in by_name[gate]["run"]
    assert "CLAUDE_STRUCTURED_OUTPUT_INVALID_JSON" in by_name[gate]["run"]
    assert by_name[gate]["if"] == "always()"
    assert ACTION.read_text().count('show_full_output: "false"') == 2

def test_diagnostic_helper_is_a_protected_governance_action():
    assert SCRIPT.is_file()
    assert not (ENGINE / "v17/claude_execution_outcome_diagnostic.py").exists()
    from v17.agent_identity_policy import is_trust_root, _codeowners_owners
    assert is_trust_root(".github/actions/wow-claude-agent/claude_execution_outcome_diagnostic.py", frozenset())
    for owners in (REPO / ".github/CODEOWNERS",
                   ENGINE / "v17/agent_identity/CODEOWNERS.proposed"):
        assert "@gregoryharper84-ship-it" in _codeowners_owners(
            owners.read_text(), ".github/actions/wow-claude-agent/claude_execution_outcome_diagnostic.py"
        )


def test_helper_only_change_requires_trust_root_review():
    spec = importlib.util.spec_from_file_location(
        "_claude_protected_diff_verifier",
        REPO / ".github/scripts/verify_existing_pr_governance_receipt.py",
    )
    assert spec is not None and spec.loader is not None
    verifier = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = verifier
    spec.loader.exec_module(verifier)
    for paths in (
        [{"filename": ".github/actions/wow-claude-agent/claude_execution_outcome_diagnostic.py",
          "previous_filename": None}],
        [{"filename": "docs/moved-helper.py",
          "previous_filename": ".github/actions/wow-claude-agent/claude_execution_outcome_diagnostic.py"}],
    ):
        with pytest.raises(verifier.ExistingPrGovernanceReceiptError,
                           match="TRUST_ROOT_CHANGE_NOT_CERTIFIABLE"):
            verifier._validate_protected_diff(paths, repo_root=REPO)


@pytest.mark.parametrize("provider,clearing_step,attempt_step", [
    ("api", "Clear API-key execution transcript before invocation",
     "Run governed Claude agent with API key"),
    ("oauth", "Clear OAuth execution transcript before invocation",
     "Run governed Claude agent with OAuth"),
])
def test_each_attempt_clears_stale_shared_execution_evidence(
    tmp_path, provider, clearing_step, attempt_step
):
    steps = yaml.safe_load(ACTION.read_text())["runs"]["steps"]
    by_name = {step["name"]: step for step in steps}
    clear = by_name[clearing_step]
    attempt = by_name[attempt_step]
    assert steps.index(clear) + 1 == steps.index(attempt)
    assert clear["if"] == attempt["if"]
    shared = tmp_path / "claude-execution-output.json"
    shared.write_text(json.dumps({
        "type": "result", "is_error": True, "api_error_status": 429,
        "result": SENTINEL,
    }))
    assert inspect_execution_file(str(shared), runner_temp=str(tmp_path)).startswith(
        "PROVIDER_HTTP_429"
    )
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", clear["run"]],
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Simulated provider setup error occurs before the pinned action writes.
    assert not shared.exists()
    assert not inspect_execution_file(str(shared), runner_temp=str(tmp_path)).startswith(
        "PROVIDER_HTTP_429"
    )


def test_missing_runner_temp_fails_closed_before_provider_attempt(tmp_path):
    steps = yaml.safe_load(ACTION.read_text())["runs"]["steps"]
    clear = next(s for s in steps
                 if s["name"] == "Clear OAuth execution transcript before invocation")
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", clear["run"]],
        env={**os.environ, "RUNNER_TEMP": ""},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode != 0
    assert "CLAUDE_RUNNER_TEMP_MISSING" in result.stdout
