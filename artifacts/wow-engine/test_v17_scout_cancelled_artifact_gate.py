"""Guard lossless cancelled-writer recovery against source runs without discovery bytes.

These tests execute the exact Bash selector embedded in the workflow using a
local gh stub. No GitHub mutation, provider credentials, DB or network needed.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wow-v17-scout-persist-resume.yml"


def _selector() -> str:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    script = workflow["jobs"]["resume-persistence"]["steps"][0]["run"]
    match = re.search(
        r"(?ms)^resolve_oldest_cancelled_without_receipt\(\) \{\n.*?^\}",
        script,
    )
    assert match, "The source selector must remain independently testable."
    return "set -euo pipefail\n" + match.group(0) + "\nresolve_oldest_cancelled_without_receipt\n"


def _execute(tmp_path: Path, *, valid_source: str = "", broken_source: str = ""):
    gh = tmp_path / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import re
import sys

assert sys.argv[1] == "api"
url = sys.argv[2]
if "wow-v17-scout-brain-persist.yml/runs?" in url:
    if "page=1" not in url:
        raise SystemExit(33)
    print(json.dumps({"workflow_runs": [
        {"created_at": "2026-10-23T14:58:23Z", "display_title": "writer source=39974206880"},
        {"created_at": "2026-10-24T14:58:23Z", "display_title": "writer source=39974206881"}
    ]}))
elif "/actions/artifacts?name=wow-v17-scout-brain-persistence-" in url:
    print("0")
elif "/actions/runs/" in url and "/artifacts" in url:
    source_id = re.search(r"/actions/runs/([0-9]+)/artifacts", url).group(1)
    if source_id == os.environ.get("MOCK_BROKEN_SOURCE"):
        raise SystemExit(44)
    print("1" if source_id == os.environ.get("MOCK_VALID_SOURCE") else "0")
else:
    raise SystemExit(45)
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    summary = tmp_path / "summary.txt"
    summary.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", ""),
            "GITHUB_REPOSITORY": "gregoryharper84-ship-it/WOW-Dashboard",
            "GITHUB_STEP_SUMMARY": str(summary),
            "SCOUT_PERSIST_LOSSLESS_RECOVERY_EPOCH": "2026-10-03T04:44:53Z",
            "SCOUT_PERSIST_RECOVERY_MAX_PAGES": "20",
            "MOCK_VALID_SOURCE": valid_source,
            "MOCK_BROKEN_SOURCE": broken_source,
        }
    )
    result = subprocess.run(
        ["bash", "-c", _selector()],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=15,
    )
    return result, summary.read_text(encoding="utf-8")


def test_oldest_invalid_source_is_logged_then_next_valid_source_selected(tmp_path):
    result, summary = _execute(tmp_path, valid_source="39974206881")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "39974206881"
    assert (
        "SCOUT_DISCOVERY_ARTIFACT_UNOBTAINABLE source_run_id=39974206880"
        in summary
    )


def test_no_recoverable_payload_retains_hold_and_accounts_for_each_source(tmp_path):
    result, summary = _execute(tmp_path)
    assert result.returncode == 1, result.stderr
    assert result.stdout.strip() == ""
    for source in ("39974206880", "39974206881"):
        assert f"SCOUT_DISCOVERY_ARTIFACT_UNOBTAINABLE source_run_id={source}" in summary


def test_discovery_api_failure_fails_closed_not_skipped(tmp_path):
    result, summary = _execute(tmp_path, broken_source="39974206880")
    assert result.returncode == 2
    assert result.stdout.strip() == ""
    assert "39974206881" not in summary
