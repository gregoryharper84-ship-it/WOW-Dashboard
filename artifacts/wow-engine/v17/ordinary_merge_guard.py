"""Diff guard for the ordinary-PR lane of the Morning-Green continuation.

The continuation workflow merges an ordinary PR only when the author opted in,
declared Class A or B, the five exact-head checks pass, the branch is current,
and this guard finds nothing in the exact-head diff that contradicts the
declared class.

The guard can only deny. It never classifies a change as Class C, and it never
authorizes trust-root, release, reviewer, model, calibration or migration
changes. Those stay with their existing human/independent paths. Paths are
matched conservatively; an over-denial falls back to an ordinary agent merge.

can_execute is always false.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from v17.engineering_effectiveness import BASE_REGRESSION, classify_change_impact

LANE = "ORDINARY"
ALLOWED_CLASSES = frozenset({"A", "B"})
MAX_FILES = 300  # GitHub compare returns at most 300 files; at the cap the list may be truncated.

TRUST_ROOT_PREFIXES = (".github/",)
TRUST_ROOT_FILES = frozenset({"render.yaml", "CLAUDE.md", "AGENTS.md", "CODEOWNERS"})

# Substrings matched against the lower-cased path. Over-denial is intended.
DENY_SUBSTRINGS = (
    # release / review / merge / deploy control surface
    "release", "deploy", "review", "merge", "ruleset", "governance", "journal",
    "recovery", "check_registry", "independent", "sirt", "attest", "publish",
    # credentials
    "auth", "secret", "credential", "token",
    # probability-producing and model surfaces (Class C lives here)
    "terminal", "reducer", "specialist", "calibrat", "model", "probabilit",
    "lower_bound", "threshold", "certif", "challenger", "promotion", "fitted",
    "artifact_registry", "can_execute",
    # persistence
    "migration", ".sql",
)
# Whole path tokens (split on non-alphanumerics) for short names.
DENY_TOKENS = frozenset({"qa", "ci"})

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_TEST_PATH = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)")


class GuardInputError(ValueError):
    """Input is missing or malformed; the caller must fail closed."""


def load_protected_patterns(path: Path) -> list[str]:
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if value and not value.startswith("#"):
            patterns.append(value)
    if not patterns:
        raise GuardInputError("protected-path manifest is empty")
    return patterns


def _paths(files: Iterable[dict[str, Any]]) -> list[str]:
    out: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("filename"), str) or not row["filename"].strip():
            raise GuardInputError("file row without a filename")
        out.add(row["filename"].strip())
        previous = row.get("previous_filename")
        if isinstance(previous, str) and previous.strip():
            out.add(previous.strip())
    return sorted(out)


def evaluate(
    *,
    files: list[dict[str, Any]],
    declared_class: str,
    protected_patterns: list[str],
    expected_head: str,
    compared_head: str,
) -> dict[str, Any]:
    reasons: list[str] = []
    if declared_class not in ALLOWED_CLASSES:
        reasons.append(f"DECLARED_CLASS_NOT_ORDINARY:{declared_class or 'missing'}")
    if not expected_head or expected_head != compared_head:
        reasons.append("DIFF_NOT_BOUND_TO_EXPECTED_HEAD")
    if not files:
        reasons.append("NO_DIFF")
    if len(files) >= MAX_FILES:
        reasons.append("DIFF_TOO_LARGE_OR_TRUNCATED")

    paths = _paths(files)
    for row in files:
        if row.get("status") == "removed" and _TEST_PATH.search(row["filename"].strip().lower()):
            reasons.append(f"TEST_REMOVAL_NEEDS_REVIEW:{row['filename'].strip()}")

    for path in paths:
        lower = path.lower()
        if path.startswith(TRUST_ROOT_PREFIXES) or Path(path).name in TRUST_ROOT_FILES:
            reasons.append(f"TRUST_ROOT_PATH:{path}")
            continue
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in protected_patterns):
            reasons.append(f"PROTECTED_PATH:{path}")
            continue
        hit = next((s for s in DENY_SUBSTRINGS if s in lower), None)
        if hit is None:
            hit = next((t for t in _TOKEN_SPLIT.split(lower) if t in DENY_TOKENS), None)
        if hit is not None:
            reasons.append(f"SENSITIVE_NAME:{hit}:{path}")

    impact = classify_change_impact(paths)
    for gate in impact["required_gates"]:
        if gate != BASE_REGRESSION:
            reasons.append(f"IMPACT_GATE:{gate}")

    return {
        "lane": LANE,
        "eligible": not reasons,
        "declared_class": declared_class,
        "expected_head": expected_head,
        "changed_paths": paths,
        "reasons": reasons,
        "can_execute": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--compare-json", required=True, help="GitHub compare API response for main...HEAD_SHA")
    parser.add_argument("--protected-paths", required=True)
    parser.add_argument("--declared-class", required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    try:
        compare = json.loads(Path(args.compare_json).read_text(encoding="utf-8"))
        if not isinstance(compare, dict) or not isinstance(compare.get("files"), list):
            raise GuardInputError("compare response has no files list")
        head = compare.get("commits", [{}])[-1].get("sha", "") if compare.get("commits") else ""
        result = evaluate(
            files=compare["files"],
            declared_class=args.declared_class.strip(),
            protected_patterns=load_protected_patterns(Path(args.protected_paths)),
            expected_head=args.expected_head.strip(),
            compared_head=head,
        )
    except (OSError, json.JSONDecodeError, GuardInputError, AttributeError, IndexError) as exc:
        print(json.dumps({"lane": LANE, "eligible": False, "reasons": [f"GUARD_INPUT_INVALID:{exc}"], "can_execute": False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["eligible"] else 3


if __name__ == "__main__":
    sys.exit(main())
