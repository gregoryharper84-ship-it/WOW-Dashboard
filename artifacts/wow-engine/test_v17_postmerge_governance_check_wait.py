"""Post-merge release verification must await governance *without* fail-opening.

The real release workflow's exact-head CI step is exercised with a deterministic
fake GitHub API. This does not approve/merge/deploy or publish a probability.
"""
from __future__ import annotations

import os
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
