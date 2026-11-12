#!/usr/bin/env python3
"""Repo-wide scan: every PR-creating step must authenticate as the wow-engineering App.

A "PR-creating step" is a job step whose shell script contains `git push` or
`gh pr create`/`gh pr edit`. Each such step's resolved GH_TOKEN/GITHUB_TOKEN env value
must reference the wow-engineering installation token minted by
`.github/actions/wow-engineering-token` (conventionally bound to step id `eng_token`),
never the ambient `github.token`, a PAT, or any other secret. (#1550/#1663 item 1.)

Pure decision logic: parses on-disk workflow YAML, no network I/O, no GitHub API calls.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS_DIR = ".github/workflows"
MUTATION_MARKERS = ("git push", "gh pr create", "gh pr edit")
ENGINEERING_TOKEN_MARKER = "steps.eng_token.outputs.token"
TOKEN_ENV_KEYS = ("GH_TOKEN", "GITHUB_TOKEN")


class TokenAuditError(ValueError):
    pass


def _step_scripts(step: dict[str, Any]) -> list[str]:
    run = step.get("run")
    if isinstance(run, str):
        return [run]
    return []


def _is_mutation_step(step: dict[str, Any]) -> bool:
    return any(
        marker in script
        for script in _step_scripts(step)
        for marker in MUTATION_MARKERS
    )


def _resolved_token(step: dict[str, Any], job_env: dict[str, Any]) -> str | None:
    step_env = step.get("env") or {}
    for key in TOKEN_ENV_KEYS:
        if key in step_env:
            return str(step_env[key])
    for key in TOKEN_ENV_KEYS:
        if key in job_env:
            return str(job_env[key])
    return None


def scan_workflow(path: Path) -> list[dict[str, Any]]:
    """Return a list of violation records for one workflow file."""
    text = path.read_text()
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs") or {}
    violations: list[dict[str, Any]] = []
    for job_name, job in jobs.items():
        if not isinstance(job, dict):
            continue
        job_env = job.get("env") or {}
        for step in job.get("steps") or []:
            if not isinstance(step, dict) or not _is_mutation_step(step):
                continue
            token = _resolved_token(step, job_env)
            if token is not None and ENGINEERING_TOKEN_MARKER in token:
                continue
            violations.append(
                {
                    "workflow": str(path),
                    "job": job_name,
                    "step": step.get("name", "<unnamed step>"),
                    "resolved_token": token,
                }
            )
    return violations


def scan_repo(repo_root: Path) -> list[dict[str, Any]]:
    workflows_dir = repo_root / WORKFLOWS_DIR
    violations: list[dict[str, Any]] = []
    for path in sorted(workflows_dir.glob("*.yml")):
        violations.extend(scan_workflow(path))
    for path in sorted(workflows_dir.glob("*.yaml")):
        violations.extend(scan_workflow(path))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".", type=Path)
    args = parser.parse_args(argv)

    violations = scan_repo(args.repo_root.resolve())
    if violations:
        for v in violations:
            print(
                f"::error::PR_AUTHORING_TOKEN_NOT_ENGINEERING_APP "
                f"workflow={v['workflow']} job={v['job']} step={v['step']!r} "
                f"resolved_token={v['resolved_token']!r}",
                file=sys.stderr,
            )
        return 1
    print("PR_AUTHORING_TOKEN_AUDIT_PASS: every git-push / gh-pr-create step uses the wow-engineering App token.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
