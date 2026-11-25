"""Deterministic QA review-path selector (scoped-review redesign, PR 1).

Chooses exactly one review path per PR from exact-head evidence. There is no
operator flag: nothing here reads an environment variable or a repository variable.

* ``deterministic_class_a``: zero-credit deterministic QA. Only when EVERY changed
  file is on the protected-main Class A allowlist AND passes the content checks below.
* ``scoped_independent``: everything else, including every error and every
  unreadable input. The selector only ever fails toward more review, never less.

The allowlist and trust-root set are supplied by the caller from the protected-main
checkout. This module never reads policy from the PR head, so a PR cannot widen its
own eligibility. A selection is routing evidence only: it carries no merge, deployment
or probability authority. ``can_execute`` is always false.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import quote

SELECTOR_VERSION = "1"
PATH_DETERMINISTIC = "deterministic_class_a"
PATH_SCOPED = "scoped_independent"
MODE_FOR_PATH = {PATH_DETERMINISTIC: "zero_credit", PATH_SCOPED: "independent"}
MAX_FILES = 100
MAX_PAGES = 30
PER_PAGE = 100
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_OPERATIONS = frozenset({"added", "modified", "changed"})

Fetch = Callable[[str], tuple[int, Any]]

# A file that gates or exercises behavior. Removing or neutralizing a check in one
# is test weakening, so such a file may only qualify when it strictly adds coverage.
_TEST_PATH_RE = re.compile(
    r"(^|/)(test_[^/]*|[^/]*_test|run_challenge_tests)\.py$|(^|/)tests?/"
)
_ASSERTION_REMOVED_RE = re.compile(
    r"^-\s*(?:async\s+def\s+test_|def\s+test_|assert\b|self\.assert|with\s+self\.assertRaises"
    r"|with\s+pytest\.raises|pytest\.raises|raise\s+AssertionError)"
)
_SKIP_ADDED_RE = re.compile(
    r"^\+.*(?:pytest\.mark\.(?:skip|xfail)|unittest\.(?:skip|expectedFailure)|@skip(?:If|Unless)?\b"
    r"|pytest\.skip\(|\.skipTest\(|\bSkipTest\b|pytest\.importorskip|xfail\()"
)
_TRIVIAL_ASSERT_ADDED_RE = re.compile(r"^\+\s*assert\s+(?:True|1|not\s+False)\s*(?:,.*)?$")
# Deterministic QA performs no semantic review, so code that reaches outside the
# process cannot qualify. A false positive only routes the PR to independent review.
_RISKY_ADDED_RE = re.compile(
    r"^\+.*(?:\bsubprocess\b|\bos\.system\b|\bos\.popen\b|\beval\s*\(|\bexec\s*\(|__import__"
    r"|\bsocket\b|\brequests\b|\burllib\b|\bhttp\.client\b|\bctypes\b|\bpickle\b|\bmarshal\b"
    r"|\bshutil\.rmtree\b|\bos\.remove\b|\bos\.unlink\b|\bos\.environ\b|\bgetenv\b"
    r"|\bimportlib\b|\bcompile\s*\()"
)


def _content_lines(patch: str) -> list[str]:
    """Added/removed content lines of a unified-diff fragment from the files API.

    The files API returns hunks only (no ``---``/``+++`` file headers), so a line that
    begins ``--`` or ``++`` is genuine content and is kept.
    """
    return [ln for ln in patch.splitlines() if ln[:1] in {"+", "-"}]


def evaluate_file(
    entry: Mapping[str, Any],
    *,
    allowlist: frozenset[str],
    content_type: str | None,
    trust_root_fn: Callable[[str], bool],
) -> list[str]:
    """Typed reasons this single file cannot take the deterministic path (empty = eligible)."""
    filename = entry.get("filename")
    if not isinstance(filename, str) or not filename:
        return ["FILE_RECORD_INVALID"]
    reasons: list[str] = []
    previous = entry.get("previous_filename")
    for candidate in (filename, previous):
        if isinstance(candidate, str) and candidate and trust_root_fn(candidate):
            reasons.append("TRUST_ROOT_PATH")
    if filename not in allowlist:
        reasons.append("PATH_NOT_ALLOWLISTED")
    status = entry.get("status")
    if status not in ALLOWED_OPERATIONS:
        reasons.append(f"OPERATION_NOT_ALLOWED:{status}")
    if previous is not None or "previous_filename" in entry:
        reasons.append("RENAME_OR_COPY")
    if content_type is None:
        reasons.append("CONTENT_TYPE_UNREADABLE")
    elif content_type != "file":
        # symlink, submodule and directory entries are never ordinary reviewed text.
        reasons.append(f"CONTENT_TYPE_NOT_REGULAR_FILE:{content_type}")
    patch = entry.get("patch")
    if not isinstance(patch, str) or not patch:
        reasons.append("PATCH_UNAVAILABLE")
        return sorted(set(reasons))
    lines = _content_lines(patch)
    if _TEST_PATH_RE.search(filename):
        if any(_ASSERTION_REMOVED_RE.match(ln) for ln in lines):
            reasons.append("TEST_ASSERTION_REMOVED")
        if any(_SKIP_ADDED_RE.match(ln) for ln in lines):
            reasons.append("TEST_SKIP_ADDED")
        if any(_TRIVIAL_ASSERT_ADDED_RE.match(ln) for ln in lines):
            reasons.append("TRIVIAL_ASSERT_ADDED")
    if filename.endswith(".json") and any(ln.startswith("-") for ln in lines):
        # Expected-value data may only grow. Editing or deleting a case can weaken a check.
        reasons.append("DATA_LINES_REMOVED")
    if filename.endswith(".py") and any(_RISKY_ADDED_RE.match(ln) for ln in lines):
        reasons.append("RISKY_CALL_ADDED")
    return sorted(set(reasons))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def selector_source_sha256() -> str:
    """Digest of this module's executed source: every classification rule above."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def policy_sha256(allowlist: frozenset[str], *, source_sha256: str | None = None) -> str:
    """Hash of the policy actually applied: the selector implementation plus the main-branch allowlist.

    Binding the source digest (not only a version string) means editing any classification
    rule changes the advertised fingerprint without a manual version bump.
    """
    return _sha256_json({
        "selector_version": SELECTOR_VERSION,
        "selector_source_sha256": source_sha256 if source_sha256 is not None else selector_source_sha256(),
        "class_a_allowlist": sorted(allowlist),
    })


def load_class_a_allowlist(repo_root: Path) -> frozenset[str]:
    """The one canonical Class A allowlist, read from the protected-main checkout."""
    script = Path(repo_root) / ".github/scripts/verify_existing_pr_governance_receipt.py"
    spec = importlib.util.spec_from_file_location("_qa_selector_class_a_policy", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("CLASS_A_ALLOWLIST_UNLOADABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return frozenset(module.CLASS_A_EXACT_PATHS)


def diff_sha256(files: list[Mapping[str, Any]]) -> str:
    """Hash of the reviewed diff inventory: path, operation, blob sha and patch digest."""
    rows = []
    for entry in files:
        patch = entry.get("patch")
        rows.append({
            "filename": entry.get("filename"),
            "previous_filename": entry.get("previous_filename"),
            "status": entry.get("status"),
            "sha": entry.get("sha"),
            "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest() if isinstance(patch, str) else None,
        })
    rows.sort(key=lambda r: (str(r["filename"]), str(r["previous_filename"])))
    return _sha256_json(rows)


def select_review_path(
    files: list[Mapping[str, Any]],
    *,
    allowlist: frozenset[str],
    content_types: Mapping[str, str | None],
    trust_root_fn: Callable[[str], bool],
    declared_changed_files: int | None = None,
    inventory_errors: Iterable[str] = (),
) -> dict[str, Any]:
    """Pure selection. Deterministic only if no reason exists; otherwise scoped independent."""
    reasons: list[str] = [f"INVENTORY_ERROR:{e}" for e in inventory_errors]
    if not files:
        reasons.append("FILES_EMPTY")
    if len(files) > MAX_FILES:
        reasons.append("TOO_MANY_FILES")
    if declared_changed_files is not None and (
        not isinstance(declared_changed_files, int) or isinstance(declared_changed_files, bool)
        or len(files) < declared_changed_files
    ):
        reasons.append("FILES_TRUNCATED")
    for entry in files:
        name = entry.get("filename") if isinstance(entry, Mapping) else None
        ctype = content_types.get(name) if isinstance(name, str) else None
        for reason in evaluate_file(
            entry if isinstance(entry, Mapping) else {},
            allowlist=allowlist, content_type=ctype, trust_root_fn=trust_root_fn,
        ):
            reasons.append(f"{name}:{reason}")
    path = PATH_SCOPED if reasons else PATH_DETERMINISTIC
    return {
        "selector_version": SELECTOR_VERSION,
        "path": path,
        "mode": MODE_FOR_PATH[path],
        "reasons": sorted(set(reasons)),
        "diff_sha256": diff_sha256([e for e in files if isinstance(e, Mapping)]),
        "policy_sha256": policy_sha256(allowlist),
        "can_execute": False,
    }


def fetch_content_types(repo: str, head_sha: str, paths: Iterable[str], fetch: Fetch) -> dict[str, str | None]:
    """Object type at the exact head for each path. Unreadable or ambiguous -> None."""
    out: dict[str, str | None] = {}
    for path in paths:
        status, body = fetch(f"repos/{repo}/contents/{quote(path)}?ref={head_sha}")
        out[path] = body.get("type") if status == 200 and isinstance(body, dict) and isinstance(body.get("type"), str) else None
    return out


def select_for_pr(
    repo: str,
    pr_number: int,
    expected_head: str,
    *,
    fetch: Fetch,
    allowlist: frozenset[str],
    trust_root_fn: Callable[[str], bool],
) -> dict[str, Any]:
    """Exact-head selection for one PR. Never raises: any failure selects scoped review."""
    errors: list[str] = []
    status, pr = fetch(f"repos/{repo}/pulls/{pr_number}")
    if status != 200 or not isinstance(pr, dict):
        errors.append(f"PULL_REQUEST_UNREADABLE:{status}")
        pr = {}
    head = (pr.get("head") or {}).get("sha")
    base = (pr.get("base") or {}).get("sha")
    if not isinstance(head, str) or not SHA_RE.match(head or "") or not SHA_RE.match(expected_head or ""):
        errors.append("HEAD_INVALID")
    elif head != expected_head:
        errors.append("HEAD_STALE")
    files: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        status, chunk = fetch(f"repos/{repo}/pulls/{pr_number}/files?per_page={PER_PAGE}&page={page}")
        if status != 200 or not isinstance(chunk, list):
            errors.append(f"FILES_UNREADABLE:{status}")
            break
        files.extend(e for e in chunk if isinstance(e, dict))
        if len(chunk) < PER_PAGE:
            break
    else:
        errors.append("FILES_PAGE_LIMIT")
    content_types: dict[str, str | None] = {}
    eligible_so_far = (
        not errors and files and len(files) <= MAX_FILES
        and all(isinstance(e.get("filename"), str) and e["filename"] in allowlist for e in files)
    )
    if eligible_so_far:
        # Object types are only needed (and only fetched) when the allowlist already holds.
        content_types = fetch_content_types(repo, str(head), [e["filename"] for e in files], fetch)
    declared = pr.get("changed_files")
    result = select_review_path(
        files, allowlist=allowlist, content_types=content_types, trust_root_fn=trust_root_fn,
        declared_changed_files=declared if isinstance(declared, int) else None,
        inventory_errors=errors,
    )
    if declared is not None and not isinstance(declared, int):
        result["reasons"] = sorted({*result["reasons"], "FILES_TRUNCATED"})
        result.update(path=PATH_SCOPED, mode=MODE_FOR_PATH[PATH_SCOPED])
    result.update(
        pr_number=pr_number,
        head_sha=head if isinstance(head, str) else None,
        base_sha=base if isinstance(base, str) else None,
        expected_head_sha=expected_head,
    )
    return result
