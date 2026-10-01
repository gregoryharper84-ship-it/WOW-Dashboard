"""Bounded deterministic regression bisect support for WOW Rapid Repair.

Engineering tooling only. This module never reads betting markets, never produces
sporting probabilities, and never grants execution authority.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Sequence

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MAX_DEFAULT_COMMITS = 64


class BisectBlocked(RuntimeError):
    """Raised when a deterministic bisect cannot be performed safely."""


def ordered_revisions(repo_root: Path, good_sha: str, bad_sha: str, *, max_commits: int = MAX_DEFAULT_COMMITS) -> list[str]:
    if max_commits < 2:
        raise ValueError("max_commits must be >= 2")
    proc = subprocess.run(
        ["git", "rev-list", "--ancestry-path", "--reverse", f"{good_sha}..{bad_sha}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    descendants = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    revisions = [good_sha, *descendants]
    if not descendants or descendants[-1] != bad_sha:
        raise BisectBlocked("BISECT_BAD_SHA_NOT_REACHABLE_FROM_GOOD_SHA")
    if len(revisions) > max_commits:
        raise BisectBlocked(f"BISECT_RANGE_TOO_LARGE:{len(revisions)}>{max_commits}")
    return revisions


def _classify_pytest_codes(codes: Sequence[int]) -> bool:
    """Return True for deterministic failure, False for deterministic pass."""
    if not codes:
        raise BisectBlocked("BISECT_NO_STABILITY_RUNS")
    normalized = set(codes)
    if any(code not in (0, 1) for code in normalized):
        raise BisectBlocked(f"BISECT_PYTEST_INFRA_FAILURE:{sorted(normalized)}")
    if len(normalized) != 1:
        raise BisectBlocked(f"BISECT_FLAKY_REPRODUCTION:{list(codes)}")
    return codes[0] == 1


def evaluate_revision(
    repo_root: Path,
    revision: str,
    pytest_targets: Sequence[str],
    *,
    stability_runs: int = 2,
    timeout_seconds: int = 180,
    engine_relative_path: str = "artifacts/wow-engine",
) -> bool:
    """Evaluate one revision in an isolated git worktree.

    True means the deterministic reproduction fails at that revision. False
    means it passes. Flaky or infrastructure-error outcomes are blockers.
    """
    if not pytest_targets:
        raise BisectBlocked("BISECT_PYTEST_TARGETS_REQUIRED")
    if stability_runs < 2:
        raise ValueError("stability_runs must be >= 2")

    with tempfile.TemporaryDirectory(prefix="wow-bisect-") as temp_dir:
        worktree = Path(temp_dir) / "repo"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), revision],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            engine_root = worktree / engine_relative_path
            if not engine_root.exists():
                raise BisectBlocked("BISECT_ENGINE_ROOT_MISSING_AT_REVISION")
            codes: list[int] = []
            for _ in range(stability_runs):
                try:
                    proc = subprocess.run(
                        [sys.executable, "-m", "pytest", "-q", "--tb=short", *pytest_targets],
                        cwd=engine_root,
                        timeout=timeout_seconds,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise BisectBlocked(f"BISECT_REPRODUCTION_TIMEOUT:{revision}") from exc
                codes.append(proc.returncode)
            return _classify_pytest_codes(codes)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
            )


def find_first_bad_revision(
    revisions: Sequence[str],
    is_bad: Callable[[str], bool],
) -> dict[str, object]:
    """Binary-search an ordered known-good -> known-bad revision sequence."""
    ordered = list(revisions)
    if len(ordered) < 2:
        raise BisectBlocked("BISECT_REQUIRES_GOOD_AND_BAD_REVISION")
    if is_bad(ordered[0]):
        raise BisectBlocked("BISECT_GOOD_SHA_REPRODUCES_FAILURE")
    if not is_bad(ordered[-1]):
        raise BisectBlocked("BISECT_BAD_SHA_DOES_NOT_REPRODUCE_FAILURE")

    low = 0
    high = len(ordered) - 1
    probes: list[str] = [ordered[0], ordered[-1]]
    while high - low > 1:
        mid = (low + high) // 2
        revision = ordered[mid]
        probes.append(revision)
        if is_bad(revision):
            high = mid
        else:
            low = mid

    return {
        "status": "BISECT_COMPLETE",
        "good_sha": ordered[low],
        "first_bad_sha": ordered[high],
        "range_size": len(ordered),
        "probes": probes,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": CAN_EXECUTE,
    }


def run_bisect(
    repo_root: Path,
    good_sha: str,
    bad_sha: str,
    pytest_targets: Sequence[str],
    *,
    max_commits: int = MAX_DEFAULT_COMMITS,
    stability_runs: int = 2,
    timeout_seconds: int = 180,
) -> dict[str, object]:
    revisions = ordered_revisions(repo_root, good_sha, bad_sha, max_commits=max_commits)

    cache: dict[str, bool] = {}

    def _is_bad(revision: str) -> bool:
        if revision not in cache:
            cache[revision] = evaluate_revision(
                repo_root,
                revision,
                pytest_targets,
                stability_runs=stability_runs,
                timeout_seconds=timeout_seconds,
            )
        return cache[revision]

    result = find_first_bad_revision(revisions, _is_bad)
    result["pytest_targets"] = list(pytest_targets)
    result["stability_runs"] = stability_runs
    return result
