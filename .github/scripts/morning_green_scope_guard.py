#!/usr/bin/env python3
"""Fail-closed scope verifier for WOW Morning-Green autonomous PRs.

Pure decision logic: no network I/O. The caller supplies the current PR body,
changed-file payload, expected/current head SHAs, and protected-path manifest.
"""
from __future__ import annotations
import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path
from typing import Any

ALLOWED_RISKS = {"R0", "R1", "R2-restorative"}
SCOPE_FENCE = "morning-green-scope"
WORKFLOW_PREFIX = ".github/workflows/"
WORKFLOW_SUFFIXES = (".yml", ".yaml")
WORKFLOW_PRIVILEGE_TOKENS = (
    "permissions: write-all",
    "contents: write",
    "pull-requests: write",
    "actions: write",
    "checks: write",
    "deployments: write",
    "id-token: write",
    "packages: write",
    "security-events: write",
    "statuses: write",
    "gh pr merge",
)

class ScopeGuardError(ValueError):
    pass

def extract_scope_block(body: str) -> str:
    pattern = re.compile(
        rf"```{re.escape(SCOPE_FENCE)}[ \t]*\r?\n(.*?)\r?\n```",
        re.DOTALL,
    )
    matches = pattern.findall(body or "")
    if len(matches) != 1:
        raise ScopeGuardError(
            f"expected exactly one fenced ```{SCOPE_FENCE} block; found {len(matches)}"
        )
    return matches[0]

def _clean_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        raise ScopeGuardError("empty scalar value")
    if value[0:1] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            raise ScopeGuardError("unterminated quoted scalar")
        value = value[1:-1]
    if "\\n" in value or "\\r" in value:
        raise ScopeGuardError("multiline scalar not allowed")
    return value

def validate_exact_repo_path(path: str, field: str) -> None:
    if not path or path.startswith("/") or path.startswith("./"):
        raise ScopeGuardError(f"{field}: invalid repository-relative path {path!r}")
    if ".." in Path(path).parts:
        raise ScopeGuardError(f"{field}: parent traversal is not allowed: {path!r}")
    if any(ch in path for ch in "*?[]{}"):
        raise ScopeGuardError(f"{field}: glob syntax is not allowed: {path!r}")

def parse_scope_block(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_list: str | None = None
    allowed_keys = {"risk_class", "allowed_files", "protected_files"}
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise ScopeGuardError(f"line {lineno}: tabs are not allowed")
        stripped = raw.strip()
        if stripped.startswith("- "):
            if current_list not in {"allowed_files", "protected_files"}:
                raise ScopeGuardError(f"line {lineno}: list item without active list key")
            result[current_list].append(_clean_scalar(stripped[2:]))
            continue
        if raw[:1].isspace():
            raise ScopeGuardError(f"line {lineno}: unsupported indentation")
        if ":" not in stripped:
            raise ScopeGuardError(f"line {lineno}: expected key: value")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key not in allowed_keys:
            raise ScopeGuardError(f"line {lineno}: unsupported key {key!r}")
        if key in result:
            raise ScopeGuardError(f"line {lineno}: duplicate key {key!r}")
        if key in {"allowed_files", "protected_files"}:
            if value == "":
                result[key] = []
                current_list = key
            elif value == "[]":
                result[key] = []
                current_list = None
            else:
                raise ScopeGuardError(f"line {lineno}: {key} must be a block list or []")
        else:
            result[key] = _clean_scalar(value)
            current_list = None
    missing = {"risk_class", "allowed_files", "protected_files"} - set(result)
    if missing:
        raise ScopeGuardError(f"missing required scope keys: {', '.join(sorted(missing))}")
    if result["risk_class"] not in ALLOWED_RISKS:
        raise ScopeGuardError(f"unsupported risk_class {result['risk_class']!r}")
    if not result["allowed_files"]:
        raise ScopeGuardError("allowed_files must contain at least one exact path")
    for list_key in ("allowed_files", "protected_files"):
        values = result[list_key]
        if len(values) != len(set(values)):
            raise ScopeGuardError(f"{list_key} contains duplicate paths")
        for path in values:
            validate_exact_repo_path(path, list_key)
    return result

def load_protected_patterns(path: Path) -> list[str]:
    patterns: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith("/"):
            raise ScopeGuardError(f"protected manifest line {lineno}: absolute path not allowed")
        patterns.append(value)
    if not patterns:
        raise ScopeGuardError("protected-path manifest is empty")
    return patterns

def is_protected(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)

def added_patch_lines(patch: str) -> list[str]:
    lines: list[str] = []
    for line in (patch or "").splitlines():
        if line.startswith("+++") or not line.startswith("+"):
            continue
        lines.append(line[1:].strip().lower())
    return lines

def is_workflow(path: str) -> bool:
    return path.startswith(WORKFLOW_PREFIX) and path.endswith(WORKFLOW_SUFFIXES)

def verify_workflow_patch(file_row: dict[str, Any]) -> None:
    filename = str(file_row.get("filename") or "")
    if not is_workflow(filename):
        return
    patch = file_row.get("patch")
    if not isinstance(patch, str) or not patch:
        raise ScopeGuardError(
            f"workflow change {filename!r} has no inspectable patch; autonomous merge denied"
        )
    for line in added_patch_lines(patch):
        compact = re.sub(r"\s+", " ", line)
        for token in WORKFLOW_PRIVILEGE_TOKENS:
            if token in compact:
                raise ScopeGuardError(
                    f"workflow change {filename!r} adds privileged control token {token!r}"
                )

def verify_scope(*, body: str, marker_risk: str, expected_head: str, actual_head: str,
                 files: list[dict[str, Any]], protected_patterns: list[str]) -> dict[str, Any]:
    if marker_risk not in ALLOWED_RISKS:
        raise ScopeGuardError(f"unsupported Morning-Green risk marker {marker_risk!r}")
    if not expected_head or expected_head != actual_head:
        raise ScopeGuardError(
            f"PR head changed after protected-check verification: expected={expected_head!r} actual={actual_head!r}"
        )
    scope = parse_scope_block(extract_scope_block(body))
    if scope["risk_class"] != marker_risk:
        raise ScopeGuardError(
            f"scope risk_class {scope['risk_class']!r} disagrees with Morning-Green-Risk {marker_risk!r}"
        )
    allowed = set(scope["allowed_files"])
    declared_protected = set(scope["protected_files"])
    for path in sorted(allowed):
        if path in declared_protected:
            raise ScopeGuardError(f"allowed_files also declares protected file {path!r}")
        if is_protected(path, protected_patterns):
            raise ScopeGuardError(f"allowed_files includes centrally protected path {path!r}")
    changed: set[str] = set()
    if not isinstance(files, list):
        raise ScopeGuardError("changed-files payload must be a JSON list")
    for row in files:
        if not isinstance(row, dict):
            raise ScopeGuardError("changed-files payload contains a non-object row")
        filename = str(row.get("filename") or "").strip()
        if not filename:
            raise ScopeGuardError("changed-files payload contains a row without filename")
        validate_exact_repo_path(filename, "changed_files")
        changed.add(filename)
        previous = str(row.get("previous_filename") or "").strip()
        check_paths = [filename]
        if previous:
            validate_exact_repo_path(previous, "previous_filename")
            check_paths.append(previous)
        for path in check_paths:
            if path in declared_protected:
                raise ScopeGuardError(f"PR touches body-declared protected path {path!r}")
            if is_protected(path, protected_patterns):
                raise ScopeGuardError(f"PR touches centrally protected path {path!r}")
        verify_workflow_patch(row)
    undeclared = sorted(changed - allowed)
    if undeclared:
        raise ScopeGuardError("PR changed files outside allowed_files: " + ", ".join(undeclared))
    return {
        "ok": True,
        "risk_class": marker_risk,
        "head_sha": actual_head,
        "changed_files": sorted(changed),
        "allowed_files": sorted(allowed),
        "declared_protected_files": sorted(declared_protected),
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--files-json", required=True, type=Path)
    parser.add_argument("--protected-paths", required=True, type=Path)
    parser.add_argument("--risk", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--actual-head", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_scope(
            body=args.body_file.read_text(encoding="utf-8"),
            marker_risk=args.risk,
            expected_head=args.expected_head,
            actual_head=args.actual_head,
            files=json.loads(args.files_json.read_text(encoding="utf-8")),
            protected_patterns=load_protected_patterns(args.protected_paths),
        )
    except (OSError, json.JSONDecodeError, ScopeGuardError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0

if __name__ == "__main__":
    sys.exit(main())
