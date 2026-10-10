"""Post-merge release verification must await governance *without* fail-opening.

The real release workflow's exact-head CI step is exercised with a deterministic
fake GitHub API. This does not approve/merge/deploy or publish a probability.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-release-production-verification-agent.yml"


def _exact_workflow_step():
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["release-verification"]["steps"]
    match = [s for s in steps if s.get("name") == "Verify required exact-head CI"]
    assert len(match) == 1
    return match[0]["run"]


def _run_with_fake_gh(tmp_path, *, scenario):
    assert shutil.which("jq"), "jq is required by the actual release CI step"
    tool = tmp_path / "bin"
    tool.mkdir()
    gh = tool / "gh"
    gh.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json
        import os
        import pathlib
        import sys

        api = " ".join(sys.argv[1:])
        if "/actions/runs?head_sha=" in api:
            names = [
                ("wow-verify", "wow-verify"),
                ("wow-engine-verify", "wow-engine-verify"),
                ("wow-v17-rapid-repair", "wow-v17-rapid-repair"),
                ("wow-v17-change-impact-gate", "wow-v17-change-impact-gate"),
                ("wow-v17-engineering-auditor-code-health", "wow-v17-engineering-auditor-code-health"),
                ("wow-v17-spread-forward-shadow", "wow-v17-spread-forward-shadow"),
            ]
            rows = [
                {"name": n, "path": ".github/workflows/" + p + ".yml",
                 "created_at": "2026-10-10T12:00:00Z",
                 "status": "completed",
                 "conclusion": "failure" if (os.environ["SCENARIO"] == "failed_required_ci" and i == 0) else "success"}
                for i, (n, p) in enumerate(names)
            ]
            print(json.dumps(rows))
        elif "/check-runs?per_page=" in api:
            count_path = pathlib.Path(os.environ["GATE_POLL_COUNT"])
            attempt = int(count_path.read_text()) + 1 if count_path.exists() else 1
            count_path.write_text(str(attempt))
            mode = os.environ["SCENARIO"]
            if mode == "missing_governance":
                print("[]")
                sys.exit(0)
            status = "completed" if (
                mode == "terminal_failed" or
                (mode == "eventual_pass" and attempt >= 2)
            ) else "in_progress"
            conclusion = ("failure" if mode == "terminal_failed" else "success") if status == "completed" else None
            print(json.dumps([{"name": "Trusted exact-head engineering governance",
                               "started_at": "2026-10-10T12:00:00Z",
                               "status": status, "conclusion": conclusion}]))
        else:
            print("Unexpected gh invocation", api, file=sys.stderr)
            sys.exit(2)
    """))
    gh.chmod(0o755)
    sleep = tool / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    env = dict(
        os.environ, SCENARIO=scenario,
        GATE_POLL_COUNT=str(tmp_path / "poll_count"),
        HEAD_SHA="a" * 40,
        GITHUB_REPOSITORY="gregoryharper84-ship-it/WOW-Dashboard",
        GH_TOKEN="fake-local-only",
        PATH=str(tool) + os.pathsep + os.environ["PATH"],
    )
    process = subprocess.run(
        ["bash", "-c", _exact_workflow_step()],
        env=env, capture_output=True, text=True, timeout=30,
    )
    counter = tmp_path / "poll_count"
    return process, int(counter.read_text()) if counter.exists() else 0


def test_release_observer_waits_for_success_on_exact_head(tmp_path):
    result, polls = _run_with_fake_gh(tmp_path, scenario="eventual_pass")
    assert result.returncode == 0, result.stderr
    assert polls == 2


def test_release_observer_fails_immediately_on_terminal_governance_failure(tmp_path):
    result, polls = _run_with_fake_gh(tmp_path, scenario="terminal_failed")
    assert result.returncode != 0
    assert "RELEASE_TRUSTED_GOVERNANCE_CHECK_NOT_GREEN: completed|failure" in result.stderr
    assert polls == 1


@pytest.mark.parametrize("scenario", ["never_completes", "missing_governance"])
def test_release_observer_never_accepts_absent_or_permanent_pending_gate(tmp_path, scenario):
    result, polls = _run_with_fake_gh(tmp_path, scenario=scenario)
    assert result.returncode != 0
    assert "RELEASE_TRUSTED_GOVERNANCE_CHECK_INCOMPLETE_AFTER_BOUNDED_WAIT" in result.stderr
    assert polls == 24


def test_release_observer_still_rejects_any_failed_required_workflow(tmp_path):
    result, polls = _run_with_fake_gh(tmp_path, scenario="failed_required_ci")
    assert result.returncode != 0
    assert "RELEASE_REQUIRED_WORKFLOW_NOT_GREEN" in result.stderr
    assert polls == 0



def _publish_step():
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["release-verification"]["steps"]
    match = [s for s in steps if s.get("name") == "Publish release verification receipt"]
    assert len(match) == 1
    return match[0]["run"]


def _publish_receipt(tmp_path, *, outcome, result):
    tool = tmp_path / "comment-tools"
    tool.mkdir()
    gh = tool / "gh"
    gh.write_text("#!/bin/sh\nset -eu\n[ \"$1\" = pr ] && [ \"$2\" = comment ] && [ \"$6\" = --body-file ]\ncp \"$7\" \"$GH_CAPTURE\"\n")
    gh.chmod(0o755)
    capture = tmp_path / "comment.md"
    summary = tmp_path / "summary.md"
    env = dict(
        os.environ,
        AGENT_OUTCOME=outcome,
        RESULT=result,
        GH_CAPTURE=str(capture),
        GH_TOKEN="test-token-no-network",
        GITHUB_REPOSITORY="gregoryharper84-ship-it/WOW-Dashboard",
        GITHUB_STEP_SUMMARY=str(summary),
        RUNNER_TEMP=str(tmp_path),
        PR_NUMBER="1555",
        HEAD_SHA="a"*40,
        MERGE_SHA="b"*40,
        PATH=str(tool)+os.pathsep+os.environ["PATH"],
    )
    process = subprocess.run(
        ["bash", "-c", _publish_step()],
        env=env, capture_output=True, text=True, timeout=15,
    )
    return process, capture.read_text() if capture.exists() else ""


def test_failed_independent_agent_gets_typed_nonapproval_receipt(tmp_path):
    result, receipt = _publish_receipt(tmp_path, outcome="failure", result="")
    assert result.returncode == 0, result.stderr
    data = json.loads(receipt.split("~~~json\n", 1)[1].split("\n~~~", 1)[0])
    assert data["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert data["blocker"] == "RELEASE_OBSERVABILITY_AGENT_FAILED"
    assert data["acceptance"] == "NOT_VERIFIED"
    assert data["production_sha"] == ""
    assert data["can_execute"] is False
    assert "governed_pr_head_sha: `" + "a"*40 + "`" in receipt
    # continue-on-error is solely for posting the receipt, not for green CI.
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["release-verification"]["steps"]
    agent = next(s for s in steps if s.get("id") == "release_agent")
    gate = next(s for s in steps if s.get("name") == "Fail closed if release-observability agent failed")
    assert agent["continue-on-error"] is True
    assert "steps.release_agent.outcome == 'failure'" in gate["if"]
    assert "exit 1" in gate["run"]
    assert "always()" in next(s for s in steps if s.get("name") == "Publish release verification receipt")["if"]


def test_pending_agent_receipt_is_persisted_without_promotion(tmp_path):
    payload = {
        "status": "PENDING", "main_sha": "b"*40,
        "production_sha": "", "acceptance": "NOT_VERIFIED",
        "reconciliation": "LIVE_REVISION_UNKNOWN",
        "blocker": "DEPLOYED_SHA_UNAVAILABLE",
        "next_action": "Collect independent exact-deployment evidence.",
    }
    result, receipt = _publish_receipt(tmp_path, outcome="success", result=json.dumps(payload))
    assert result.returncode == 0, result.stderr
    assert '"status": "PENDING"' in receipt
    assert "can_execute=false" in receipt


@pytest.mark.parametrize("bad_result", ["{}", "not-json", '{"status":"PRODUCTION_VERIFIED"}'])
def test_malformed_model_output_must_not_publish_success_receipt(tmp_path, bad_result):
    result, receipt = _publish_receipt(tmp_path, outcome="success", result=bad_result)
    assert result.returncode != 0
    assert "RELEASE_OBSERVABILITY_RESULT_SCHEMA_INVALID" in result.stderr
    assert receipt == ""



def _governance_denial_step():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["release-verification"]["steps"]
    step = next(s for s in steps if s.get("name") == "Record exact-head governance denial without approval")
    assert "failure()" in step["if"]
    assert step["continue-on-error"] is True  # original gate failure persists
    return step["run"]


def _run_denial_receipt(tmp_path, *, gate_status, conclusion):
    """Exercise the workflow shell itself with deterministic GitHub API results."""
    tools_dir = tmp_path / "mockbin"
    tools_dir.mkdir()
    gh = tools_dir / "gh"
    gh.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json
        import os
        import pathlib
        import sys
        args = sys.argv[1:]
        if args[0] == "api":
            state = os.environ["CHECK_STATUS"]
            if state == "MISSING":
                print("[]")
            else:
                verdict = os.environ["CHECK_CONCLUSION"]
                print(json.dumps([{"name": "Trusted exact-head engineering governance",
                                   "status": state,
                                   "conclusion": None if verdict == "NONE" else verdict,
                                   "details_url": "https://github.com/example/governance",
                                   "started_at": "2026-10-10T12:00:00Z"}]))
        elif args[:2] == ["pr", "comment"]:
            pathlib.Path(os.environ["COMMENT_CAPTURE"]).write_text(
                pathlib.Path(args[args.index("--body-file") + 1]).read_text()
            )
        else:
            sys.exit(5)
    """))
    gh.chmod(0o755)
    capture = tmp_path / "comment.txt"
    summary = tmp_path / "summary.txt"
    env = dict(
        os.environ,
        CHECK_STATUS=gate_status,
        CHECK_CONCLUSION=conclusion,
        COMMENT_CAPTURE=str(capture),
        GITHUB_REPOSITORY="gregoryharper84-ship-it/WOW-Dashboard",
        PR_NUMBER="1596",
        HEAD_SHA="a"*40,
        MERGE_SHA="b"*40,
        GH_TOKEN="test-only",
        RUNNER_TEMP=str(tmp_path),
        GITHUB_STEP_SUMMARY=str(summary),
        PATH=str(tools_dir)+os.pathsep+os.environ["PATH"],
    )
    result = subprocess.run(
        ["bash", "-c", _governance_denial_step()],
        env=env, text=True, capture_output=True, timeout=15,
    )
    return result, capture.read_text() if capture.exists() else ""


@pytest.mark.parametrize("status,conclusion", [
    ("completed", "failure"),
    ("completed", "cancelled"),
    ("in_progress", "NONE"),
    ("MISSING", "NONE"),
])
def test_governance_denial_is_typed_not_approved(tmp_path, status, conclusion):
    result, comment = _run_denial_receipt(
        tmp_path, gate_status=status, conclusion=conclusion
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(comment.split("~~~json\n", 1)[1].split("\n~~~", 1)[0])
    assert payload["status"] == "BLOCKED_WITH_EXACT_REASON"
    assert payload["blocker"] == "RELEASE_TRUSTED_GOVERNANCE_CHECK_NOT_GREEN"
    assert payload["acceptance"] == "NOT_VERIFIED"
    assert payload["probability_publishable"] is False
    assert payload["can_execute"] is False
    assert payload["governance_check_status"] == status
    assert payload["governance_check_conclusion"] == conclusion


def test_governance_diagnostic_refuses_to_mislabel_success_as_hold(tmp_path):
    result, comment = _run_denial_receipt(
        tmp_path, gate_status="completed", conclusion="success"
    )
    assert result.returncode != 0
    assert "RELEASE_GOVERNANCE_DIAGNOSTIC_NO_DENIAL" in result.stderr
    assert comment == ""
