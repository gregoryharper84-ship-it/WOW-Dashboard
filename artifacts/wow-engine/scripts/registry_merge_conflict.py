"""Fail when a registry document conflicts with the base branch.

The failure and status registries are append-heavy documents: registering a new
code adds a row, and so does almost every other lane. Two lanes that add a row
in the same place conflict on the file even when their codes are unrelated, and
the branch goes ``dirty``. GitHub can then build no merge commit, so it runs no
checks at all: the pull request shows no failures while the last green run is
stale, and the conflict is only noticed on the next push.

This check makes that state loud. It compares the checked-out head against the
base branch with ``git merge-tree --write-tree``, which needs no worktree and
writes nothing outside the object database, and fails only when one of the
registry documents is among the conflicted paths. Conflicts in other files are
reported but do not fail the step; those are ordinary review problems, whereas a
conflicted registry document silently disables every check on the head.

It grants no execution, publication or model authority: it reads git objects
and prints the result.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Documents that carry registered codes and are rewritten by several lanes at
# once. A conflict in any of them stops GitHub from running checks on the head.
REGISTRY_DOCS = (
    "docs/failure_codes.md",
    "docs/nhl_status_registry.md",
    "docs/sport_status_registries.md",
)

_OID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ("git",) + args, cwd=ROOT, capture_output=True, text=True
    )
    if check and proc.returncode not in (0, 1):
        raise SystemExit(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc


def repo_root() -> Path:
    """The repository top level, so merge-tree paths can be compared literally."""
    return Path(_git("rev-parse", "--show-toplevel").stdout.strip()).resolve()


def documented_paths(docs: tuple[str, ...] = REGISTRY_DOCS) -> list[str]:
    """The registry documents as repository-root-relative paths, as git prints them."""
    top = repo_root()
    return [(ROOT / doc).resolve().relative_to(top).as_posix() for doc in docs]


def parse_merge_tree(stdout: str) -> list[str]:
    """The conflicted paths from ``git merge-tree --write-tree --name-only``.

    The first output line is the written tree object id; the remaining lines are
    the conflicted paths. Informational messages go to stderr, so they are not
    mixed in here.
    """
    lines = [line.strip() for line in stdout.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []
    return [line for line in lines[1:] if not _OID_RE.fullmatch(line)]


def conflicted_paths(base: str, head: str = "HEAD") -> list[str]:
    """Paths that conflict when ``head`` is merged with ``base``; empty if clean."""
    proc = _git("merge-tree", "--write-tree", "--name-only", base, head)
    if proc.returncode == 0:
        return []
    return parse_merge_tree(proc.stdout)


def registry_conflicts(
    paths: list[str], docs: tuple[str, ...] = REGISTRY_DOCS
) -> list[str]:
    """Which of ``paths`` are registry documents."""
    watched = set(documented_paths(docs))
    normalized = {path[2:] if path.startswith("./") else path for path in paths}
    return sorted(watched & normalized)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="base ref to merge")
    parser.add_argument("--head", default="HEAD", help="head ref to merge")
    args = parser.parse_args(argv)

    if _git("rev-parse", "--verify", "--quiet", args.base, check=False).returncode:
        print(
            f"::error::base ref {args.base} is not available; fetch it before running this check",
            file=sys.stderr,
        )
        return 2

    paths = conflicted_paths(args.base, args.head)
    conflicts = registry_conflicts(paths)
    if conflicts:
        print(
            f"::error::{', '.join(conflicts)} conflicts with {args.base}. "
            "GitHub cannot build a merge commit, so it runs no checks on this head."
        )
        print(
            "Merge the base branch into this branch, keep this branch's rows and "
            "carry the base branch's new rows into the same table, then push."
        )
        return 1

    others = sorted(set(paths) - set(conflicts))
    if others:
        print(
            f"note: {len(others)} other conflicted path(s), not registry documents: "
            + ", ".join(others)
        )
    print(f"no registry conflict between {args.head} and {args.base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
