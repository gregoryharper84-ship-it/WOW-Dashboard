"""Opt-in headless Gemini AMRT admission and typed results; no betting or repo writes."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

TRUE = "true"
MODEL_AUTHORITY = "V17_TERMINAL_REDUCER"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_INCIDENT = re.compile(r"^[1-9][0-9]*$")
_LEASE = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
_BLOCKED_PATHS = (
    ".github/", ".agents/", ".codex/", ".git/", ".gemini/", "GEMINI.md", "scripts/",
    "artifacts/wow-engine/WOW_V17_CUSTOM_GPT_INSTRUCTIONS.txt",
    "artifacts/wow-engine/v17/engineering_gemini_headless.py",
)
_EXPLICIT_CODES = (
    ("GEMINI_API_QUOTA_EXHAUSTED", ("resource_exhausted", "quota exceeded", "insufficient quota", "resource exhausted")),
    ("GEMINI_API_RATE_LIMITED", ("rate limit", "429", "too many requests")),
    ("GEMINI_API_AUTH_FAILED", ("invalid api key", "api_key_invalid", "permission_denied", "unauthenticated", "403")),
    ("GEMINI_API_UPSTREAM_UNAVAILABLE", ("503", "502", "unavailable", "internal error")),
)


def admit(enabled: str, spend_confirmed: str, auto_enabled: str, mode: str) -> dict[str, Any]:
    """No API call is admitted without all explicit flags; default routing unchanged."""
    if enabled != TRUE:
        code = "GEMINI_ENGINEERING_DISABLED"
    elif spend_confirmed != TRUE:
        code = "GEMINI_SPEND_CAP_UNCONFIRMED"
    elif mode == "auto" and auto_enabled != TRUE:
        code = "GEMINI_AUTO_ROUTING_DISABLED"
    elif mode not in {"auto", "manual"}:
        code = "GEMINI_ADMISSION_MODE_INVALID"
    else:
        code = "ADMITTED"
    return {"status": "ADMITTED" if code == "ADMITTED" else "HELD", "code": code,
            "provider": "gemini", "can_execute": False, "terminal_authority": MODEL_AUTHORITY}


def select_incident(
    manifest: dict[str, Any], issues: list[dict[str, Any]],
    *, target_incident: str = "", requested_lease: str = "GLOBAL",
) -> dict[str, Any]:
    """Existing Conductor manifest + GitHub OPEN issues remain authoritative.

    This selects *research/isolated patch work only*. It never acquires a writer
    lease or grants shared-branch write/merge authority.
    """
    try:
        from v17.engineering_dispatch_queue import build_queue, standard_conflicts_with_rapid
    except ImportError:  # direct script execution from GitHub Actions
        from engineering_dispatch_queue import build_queue, standard_conflicts_with_rapid

    if not _LEASE.fullmatch(requested_lease):
        raise ValueError("GEMINI_LEASE_GROUP_INVALID")
    if target_incident and not _INCIDENT.fullmatch(target_incident):
        raise ValueError("GEMINI_INCIDENT_ID_INVALID")
    queue = build_queue(manifest, issues)
    records = sorted(
        (r for r in queue["records"] if r["work_stream"] == "RESTORATION"),
        key=lambda r: (0 if r["severity"] == "P0" else 1, r["priority_rank"], r["issue_number"]),
    )
    if target_incident:
        records = [r for r in records if r["incident_id"] == target_incident]
    elif requested_lease != "GLOBAL":
        records = [r for r in records if r["lease_group"] == requested_lease]
    if not records:
        return {"status": "NO_ACTION", "code": "GEMINI_NO_ELIGIBLE_EXISTING_INCIDENT", "can_execute": False}
    record = records[0]
    group = record["lease_group"] or "GLOBAL"
    if target_incident and group != requested_lease:
        raise ValueError("GEMINI_INCIDENT_LEASE_GROUP_MISMATCH")
    if record["execution_lane"] == "STANDARD":
        blockers = standard_conflicts_with_rapid(queue, record["incident_id"])
        if blockers:
            return {"status": "NO_ACTION", "code": "GEMINI_STANDARD_CONFLICTS_WITH_P0", "can_execute": False}
    return {
        "status": "SELECTED", "code": "EXISTING_CONDUCTOR_RESEARCH_ONLY",
        "incident_id": record["incident_id"], "issue_number": record["issue_number"],
        "title": record["title"][:200], "lease_group": group,
        "severity": record["severity"], "execution_lane": record["execution_lane"],
        "writer_lease_granted": False, "github_branch_write_allowed": False,
        "can_execute": False, "terminal_authority": MODEL_AUTHORITY,
    }


def classify_cli(exit_code: int, stderr: str, *, timed_out: bool = False,
                 response: Any = None) -> dict[str, Any]:
    """Classify one bounded attempt without persisting log contents or secrets."""
    value = (stderr or "").lower()
    if timed_out:
        code = "GEMINI_CLI_TIMEOUT"
    elif exit_code == 0:
        code = ("GEMINI_CLI_COMPLETE" if isinstance(response, dict)
                and isinstance(response.get("response"), str)
                and bool(response["response"].strip()) and not response.get("error")
                else "GEMINI_CLI_RESPONSE_MALFORMED")
    elif exit_code == 53:
        code = "GEMINI_CLI_TURN_LIMIT"
    elif exit_code == 42:
        code = "GEMINI_CLI_INPUT_INVALID"
    else:
        code = next((kind for kind, needles in _EXPLICIT_CODES
                     if any(needle in value for needle in needles)), "GEMINI_CLI_FAILED")
    return {"status": "COMPLETED" if code == "GEMINI_CLI_COMPLETE" else "HELD",
            "code": code, "retry_allowed": False, "can_execute": False,
            "terminal_authority": MODEL_AUTHORITY}


def inspect_changed_paths(paths: list[str]) -> dict[str, Any]:
    """Reject trust-root modifications from a generated candidate, not silently apply."""
    if not paths or len(paths) > 100 or any(not isinstance(p, str) for p in paths):
        return {"status": "HELD", "code": "GEMINI_PATCH_EMPTY_OR_UNBOUNDED", "can_execute": False}
    for path in paths:
        name = path.strip().replace("\\", "/")
        if (name != path or not name or name.startswith("/") or ".." in name.split("/")
                or any(name.startswith(prefix) for prefix in _BLOCKED_PATHS)
                or not (name.startswith("artifacts/wow-engine/v17/") and name.endswith(".py")
                        and name != "artifacts/wow-engine/v17/engineering_gemini_headless.py"
                        or name.startswith("artifacts/wow-engine/tests/")
                        and Path(name).name.startswith("test_") and name.endswith(".py"))):
            return {"status": "HELD", "code": "GEMINI_PATCH_PROTECTED_OR_INVALID_PATH",
                    "rejected_path": name[:200], "can_execute": False}
    return {"status": "CANDIDATE_ONLY", "code": "GEMINI_ISOLATED_PATCH_UNREVIEWED",
            "files": len(paths), "can_execute": False}


def candidate_test_paths(paths: list[str]) -> list[str]:
    """Require real regression tests changed alongside the code candidate."""
    allowed = sorted(
        p for p in paths
        if p.startswith("artifacts/wow-engine/tests/")
        and p.endswith(".py") and Path(p).name.startswith("test_")
    )
    if not allowed or len(allowed) > 10:
        raise ValueError("GEMINI_CHANGED_REGRESSION_TEST_REQUIRED")
    return allowed


def verify_candidate(worktree: Path, paths: list[str]) -> dict[str, Any]:
    """Execute edited tests in isolated worktree; fail closed on any failure.

    No Gemini API key or GitHub write token is present at this step. Do not
    turn test stdout/stderr into a pass receipt; return process evidence only.
    """
    inspected = inspect_changed_paths(paths)
    if inspected["status"] != "CANDIDATE_ONLY":
        return {"status": "HELD", "code": inspected["code"], "can_execute": False}
    try:
        tests = candidate_test_paths(paths)
    except ValueError:
        return {"status": "HELD", "code": "GEMINI_CHANGED_REGRESSION_TEST_REQUIRED",
                "can_execute": False}
    if not worktree.is_dir() or not all((worktree / p).is_file() for p in tests):
        return {"status": "HELD", "code": "GEMINI_CHANGED_TEST_PATH_MISSING", "can_execute": False}
    try:
        run = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--disable-warnings", "--maxfail=1", *tests],
            cwd=worktree, timeout=300, capture_output=True, text=True, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "HELD", "code": "GEMINI_REGRESSION_TIMEOUT", "can_execute": False}
    except OSError:
        return {"status": "HELD", "code": "GEMINI_REGRESSION_RUNNER_UNAVAILABLE", "can_execute": False}
    return {
        "status": "TESTED_CANDIDATE" if run.returncode == 0 else "HELD",
        "code": "GEMINI_REGRESSION_PASS" if run.returncode == 0 else "GEMINI_REGRESSION_FAILED",
        "test_files": tests, "exit_code": run.returncode,
        "can_execute": False, "terminal_authority": MODEL_AUTHORITY,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    admission = sub.add_parser("admit")
    admission.add_argument("--enabled", required=True)
    admission.add_argument("--spend-confirmed", required=True)
    admission.add_argument("--auto-enabled", required=True)
    admission.add_argument("--mode", choices=["manual", "auto"], required=True)
    selection = sub.add_parser("select")
    selection.add_argument("--manifest", required=True)
    selection.add_argument("--issues", required=True)
    selection.add_argument("--target", default="")
    selection.add_argument("--lease-group", default="GLOBAL")
    classify = sub.add_parser("classify")
    classify.add_argument("--exit-code", type=int, required=True)
    classify.add_argument("--stderr-file", required=True)
    classify.add_argument("--response-file", required=True)
    classify.add_argument("--timed-out", action="store_true")
    changed = sub.add_parser("inspect-patch")
    changed.add_argument("--files-json", required=True)
    verify = sub.add_parser("verify-candidate")
    verify.add_argument("--files-json", required=True)
    verify.add_argument("--worktree", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "admit":
            result = admit(args.enabled, args.spend_confirmed, args.auto_enabled, args.mode)
        elif args.action == "select":
            result = select_incident(
                json.loads(Path(args.manifest).read_text(encoding="utf-8")),
                json.loads(Path(args.issues).read_text(encoding="utf-8")),
                target_incident=args.target, requested_lease=args.lease_group,
            )
        elif args.action == "classify":
            try:
                response = json.loads(Path(args.response_file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                response = None
            result = classify_cli(args.exit_code, Path(args.stderr_file).read_text(encoding="utf-8"),
                                  timed_out=args.timed_out, response=response)
        elif args.action == "inspect-patch":
            result = inspect_changed_paths(json.loads(Path(args.files_json).read_text(encoding="utf-8")))
        else:
            result = verify_candidate(Path(args.worktree),
                json.loads(Path(args.files_json).read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Deliberately exclude exception text; upstream API inputs may contain secrets.
        result = {"status": "HELD", "code": "GEMINI_ADMISSION_OR_EVIDENCE_INVALID",
                  "error_type": type(exc).__name__, "can_execute": False,
                  "terminal_authority": MODEL_AUTHORITY}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"SELECTED", "ADMITTED", "NO_ACTION", "CANDIDATE_ONLY", "COMPLETED", "TESTED_CANDIDATE"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
