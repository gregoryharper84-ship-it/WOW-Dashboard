#!/usr/bin/env python3
"""Replay one sanitized WOW Failure Capsule before production verification.

This is engineering-only CI/sandbox tooling. It never calls a sportsbook, never
produces sporting probability, and never grants execution authority.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from v17.failure_capsule import capsule_manifest_entry

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"


def _git_sha(repo_root: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _validate_target(target: str) -> str:
    path_text = target.split("::", 1)[0].strip()
    path = Path(path_text)
    if not path_text or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"FAILURE_CAPSULE_REPLAY_TARGET_UNSAFE:{target}")
    if not (path_text.startswith("tests/") or path_text.startswith("v17/") or path_text.endswith("_tests.py")):
        raise ValueError(f"FAILURE_CAPSULE_REPLAY_TARGET_OUTSIDE_TEST_SURFACE:{target}")
    return target


def _classify_codes(codes: list[int]) -> tuple[str, str | None]:
    if not codes:
        return "BLOCKED_WITH_EXACT_REASON", "FAILURE_CAPSULE_REPLAY_NO_RUNS"
    unique = set(codes)
    if any(code not in (0, 1) for code in unique):
        return "BLOCKED_WITH_EXACT_REASON", f"FAILURE_CAPSULE_REPLAY_INFRA_FAILURE:{sorted(unique)}"
    if len(unique) != 1:
        return "BLOCKED_WITH_EXACT_REASON", f"FAILURE_CAPSULE_REPLAY_FLAKY:{codes}"
    if codes[0] == 0:
        return "PREPRODUCTION_REPLAY_PASS", None
    return "PREPRODUCTION_REPLAY_FAIL", None


def run_replay(
    *,
    capsule_path: Path,
    engine_root: Path,
    stability_runs: int = 2,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    if stability_runs < 2:
        raise ValueError("stability_runs must be >= 2")
    capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
    manifest = capsule_manifest_entry(capsule)
    if manifest.get("status") != "REPRODUCTION_READY":
        return {
            "status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": manifest.get("blocker") or "FAILURE_CAPSULE_REPRODUCTION_NOT_READY",
            "fingerprint": capsule.get("fingerprint"),
            "git_sha": _git_sha(engine_root.parents[1]),
            "terminal_authority": TERMINAL_AUTHORITY,
            "can_execute": CAN_EXECUTE,
        }

    targets = [_validate_target(str(target)) for target in manifest.get("pytest") or []]
    codes: list[int] = []
    for _ in range(stability_runs):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", *targets],
                cwd=engine_root,
                check=False,
                timeout=timeout_seconds,
            )
            codes.append(proc.returncode)
        except subprocess.TimeoutExpired:
            return {
                "status": "BLOCKED_WITH_EXACT_REASON",
                "blocker": "FAILURE_CAPSULE_REPLAY_TIMEOUT",
                "fingerprint": capsule.get("fingerprint"),
                "pytest": targets,
                "git_sha": _git_sha(engine_root.parents[1]),
                "terminal_authority": TERMINAL_AUTHORITY,
                "can_execute": CAN_EXECUTE,
            }

    status, blocker = _classify_codes(codes)
    return {
        "status": status,
        "blocker": blocker,
        "fingerprint": capsule.get("fingerprint"),
        "pytest": targets,
        "return_codes": codes,
        "stability_runs": stability_runs,
        "git_sha": _git_sha(engine_root.parents[1]),
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay one WOW Failure Capsule safely in CI/sandbox")
    parser.add_argument("capsule")
    parser.add_argument("--engine-root", default=".")
    parser.add_argument("--stability-runs", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--receipt")
    args = parser.parse_args()

    result = run_replay(
        capsule_path=Path(args.capsule),
        engine_root=Path(args.engine_root).resolve(),
        stability_runs=args.stability_runs,
        timeout_seconds=args.timeout_seconds,
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.receipt:
        Path(args.receipt).write_text(payload + "\n", encoding="utf-8")
    return 0 if result["status"] == "PREPRODUCTION_REPLAY_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
