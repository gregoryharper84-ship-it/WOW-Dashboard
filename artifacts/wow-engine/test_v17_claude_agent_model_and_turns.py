"""Executed regression for #1021: the Claude agent action's config step.

Runs the real "Configure governed Claude tool lease" script and checks the
API-key and OAuth argument blocks: per-step model on the API path only, a
read-only turn cap of 40, overrides validated, and the OAuth path unchanged.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ACTION = ROOT / ".github/actions/wow-claude-agent/action.yml"
WORKER = ROOT / ".github/workflows/wow-v17-claude-engineering-worker.yml"


def _config(tmp_path: Path, **env: str) -> tuple[int, dict[str, str], str]:
    step = next(s for s in yaml.safe_load(ACTION.read_text())["runs"]["steps"] if s.get("id") == "config")
    out = tmp_path / "out"
    out.write_text("")
    full = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(out), "OUTPUT_SCHEMA": '{"type":"object"}',
            "PROFILE": ":read-only", "MODEL": "", "MAX_TURNS": ""}
    full.update(env)
    proc = subprocess.run(["bash", "-c", step["run"]], env=full, capture_output=True, text=True)
    blocks, name, buf = {}, None, []
    for line in out.read_text().splitlines():
        if name is None and line.endswith("<<EOF"):
            name, buf = line[:-5], []
        elif line == "EOF" and name:
            blocks[name] = "\n".join(buf)
            name = None
        elif name:
            buf.append(line)
    return proc.returncode, blocks, proc.stdout


def test_model_applies_to_api_path_only(tmp_path):
    rc, b, _ = _config(tmp_path, MODEL="claude-opus-5-5")
    assert rc == 0
    assert "--model claude-opus-5-5" in b["claude_args"]
    assert "--model" not in b["oauth_claude_args"]


def test_read_only_turn_cap_raised_to_40(tmp_path):
    rc, b, _ = _config(tmp_path)
    assert rc == 0
    assert "--max-turns 40" in b["claude_args"] and "--max-turns 40" in b["oauth_claude_args"]
    assert "--model" not in b["claude_args"]


def test_workspace_profile_unchanged_and_override(tmp_path):
    rc, b, _ = _config(tmp_path, PROFILE=":workspace")
    assert "--max-turns 40" in b["claude_args"] and "Edit" in b["claude_args"]
    rc, b, _ = _config(tmp_path, MAX_TURNS="60")
    assert rc == 0 and "--max-turns 60" in b["oauth_claude_args"]


@pytest.mark.parametrize("env,code", [({"MODEL": "opus; rm -rf /"}, "CLAUDE_MODEL_INVALID"),
                                      ({"MAX_TURNS": "0"}, "CLAUDE_MAX_TURNS_INVALID"),
                                      ({"MAX_TURNS": "40 --dangerous"}, "CLAUDE_MAX_TURNS_INVALID")])
def test_invalid_overrides_fail_closed(tmp_path, env, code):
    rc, _, stdout = _config(tmp_path, **env)
    assert rc != 0 and code in stdout


def test_worker_pins_opus_only_for_specialist_and_implementation():
    pinned = {}
    for job in yaml.safe_load(WORKER.read_text())["jobs"].values():
        for step in job.get("steps", []):
            if step.get("uses") == "./.github/actions/wow-claude-agent":
                pinned[step["name"]] = step["with"].get("model")
    assert pinned["Specialist diagnostic subagent"] == "claude-opus-5-5"
    assert pinned["Implementation agent"] == "claude-opus-5-5"
    others = {k: v for k, v in pinned.items() if k not in {"Specialist diagnostic subagent", "Implementation agent"}}
    assert others and set(others.values()) == {"claude-sonnet-5-5"}


def test_oauth_args_equal_api_args_except_model(tmp_path):
    rc, b, _ = _config(tmp_path, MODEL="claude-opus-5-5", PROFILE=":workspace")
    api = [l for l in b["claude_args"].splitlines() if not l.startswith("--model")]
    assert rc == 0 and api == b["oauth_claude_args"].splitlines()
