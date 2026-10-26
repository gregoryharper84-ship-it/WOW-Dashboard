"""Fail-closed routing decisions for the protected-main governance dispatcher."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from agent_identity_policy import (
    QA_CHECK,
    RELEASE_CHECK,
    REQUIRED_REGRESSION_CHECKS,
    governance_trust_roots,
    is_trust_root,
)

GITHUB_ACTIONS_APP_ID = 15368
QA_APP_ID = 5245832
RELEASE_APP_ID = 5245853
CHANGE_IMPACT_CHECK = "WOW V17 change impact gate"
TRUSTED_GOVERNANCE_CHECK = "Trusted exact-head engineering governance"
QA_EVIDENCE_CHECKS = (*REQUIRED_REGRESSION_CHECKS, CHANGE_IMPACT_CHECK)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")

MAX_TARGETS_PER_RUN = 20
MAX_DISPATCHES_PER_RUN = 4
MAX_WIP = 4
MAX_API_PAGES = 10


def select_targets(pulls: list[dict[str, Any]], run_number: int, limit: int = MAX_TARGETS_PER_RUN) -> list[dict]:
    """Select a rotating bounded page, so a large open-PR queue cannot starve later PRs."""
    if not isinstance(run_number, int) or isinstance(run_number, bool) or run_number < 1:
        raise ValueError("CONTROLLER_RUN_NUMBER_INVALID")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("TARGET_LIMIT_INVALID")
    eligible = [
        pull
        for pull in pulls
        if isinstance(pull, dict)
        and isinstance(pull.get("number"), int)
        and not isinstance(pull.get("number"), bool)
        and pull.get("state") == "open"
        and pull.get("draft") is False
        and (pull.get("base") or {}).get("ref") == "main"
        and ((pull.get("base") or {}).get("repo") or {}).get("full_name")
        == "gregoryharper84-ship-it/WOW-Dashboard"
        and ((pull.get("head") or {}).get("repo") or {}).get("full_name")
        == "gregoryharper84-ship-it/WOW-Dashboard"
        and SHA_RE.fullmatch(str(((pull.get("head") or {}).get("sha") or "")))
    ]
    eligible.sort(key=lambda pull: pull["number"])
    if len(eligible) <= limit:
        return eligible
    start = ((run_number - 1) * limit) % len(eligible)
    return (eligible[start:] + eligible[:start])[:limit]


def latest_check(check_runs: list[dict[str, Any]], name: str, head_sha: str, app_id: int) -> dict | None:
    matches = [
        check
        for check in check_runs
        if isinstance(check, dict)
        and check.get("name") == name
        and check.get("head_sha") == head_sha
    ]
    latest = max(
        matches,
        key=lambda check: (
            str(check.get("started_at") or check.get("created_at") or check.get("completed_at") or ""),
            int(check.get("id") or 0),
        ),
        default=None,
    )
    if latest is None:
        return None
    return latest if str((latest.get("app") or {}).get("id")) == str(app_id) else {
        **latest,
        "_foreign_source": True,
    }


def _owner_approved_at_head(reviews: list[dict[str, Any]], owner_id: int, head_sha: str) -> bool:
    owner_reviews = [
        review
        for review in reviews
        if isinstance(review, dict)
        and (review.get("user") or {}).get("id") == owner_id
        and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}
    ]
    if not owner_reviews:
        return False
    latest = max(owner_reviews, key=lambda review: str(review.get("submitted_at") or ""))
    return latest.get("state") == "APPROVED" and latest.get("commit_id") == head_sha


def classify_target(
    *,
    repository: str,
    owner_id: int,
    owner_login: str,
    pull: dict[str, Any],
    file_records: list[dict[str, Any]],
    check_runs: list[dict[str, Any]],
    reviews: list[dict[str, Any]],
    source_review: str,
    available_roles: set[str],
    completed_role_runs: dict[str, str] | None = None,
    ancillary_ci_hold: str | None = None,
    zero_credit_mode: bool = False,
) -> dict[str, Any]:
    """Return one next allowlisted role or a typed HOLD/DONE decision."""
    findings: list[str] = []
    head = ((pull.get("head") or {}).get("sha") or "")
    if not SHA_RE.fullmatch(str(head)):
        findings.append("PR_HEAD_INVALID")
    if pull.get("state") != "open" or pull.get("merged_at") is not None:
        findings.append("PR_NOT_OPEN")
    if pull.get("draft") is not False:
        findings.append("PR_DRAFT_OR_DRAFT_UNKNOWN")
    if (pull.get("base") or {}).get("ref") != "main":
        findings.append("PR_BASE_NOT_MAIN")
    if ((pull.get("base") or {}).get("repo") or {}).get("full_name") != repository:
        findings.append("PR_BASE_REPOSITORY_MISMATCH")
    if ((pull.get("head") or {}).get("repo") or {}).get("full_name") != repository:
        findings.append("PR_HEAD_REPOSITORY_MISMATCH")
    if not isinstance(pull.get("number"), int) or isinstance(pull.get("number"), bool):
        findings.append("PR_NUMBER_INVALID")
    expected_files = pull.get("changed_files")
    if (
        not isinstance(file_records, list)
        or not file_records
        or not isinstance(expected_files, int)
        or isinstance(expected_files, bool)
        or len(file_records) != expected_files
    ):
        findings.append("PR_FILE_INVENTORY_INCOMPLETE")

    paths: list[str] = []
    for record in file_records:
        if not isinstance(record, dict) or not isinstance(record.get("filename"), str) or not record["filename"]:
            findings.append("PR_FILE_PATH_INVALID")
            continue
        paths.append(record["filename"])
        if record.get("status") == "renamed" or "previous_filename" in record:
            previous = record.get("previous_filename")
            if not isinstance(previous, str) or not previous:
                findings.append("PR_RENAME_ORIGIN_MISSING")
            else:
                paths.append(previous)
    if findings:
        return {"action": "HOLD", "findings": sorted(set(findings)), "head_sha": head}

    trust_roots = frozenset(governance_trust_roots(Path(__file__).resolve().parents[3]))
    protected = any(is_trust_root(path, trust_roots) for path in paths)
    if protected:
        author = pull.get("user") or {}
        if author.get("id") == owner_id or str(author.get("login", "")).casefold() == owner_login.casefold():
            return {"action": "HOLD", "findings": ["TRUST_ROOT_OWNER_AUTHORED"], "head_sha": head}
        if not _owner_approved_at_head(reviews, owner_id, head):
            return {"action": "HOLD", "findings": ["TRUST_ROOT_OWNER_APPROVAL_MISSING"], "head_sha": head}

    if not isinstance(zero_credit_mode, bool):
        return {"action": "HOLD", "findings": ["ZERO_CREDIT_POLICY_INVALID"], "head_sha": head}
    runs = completed_role_runs or {}
    for role, finding in (
        ("source_review", "SOURCE_REVIEW_WORKFLOW_UNAVAILABLE"),
        ("qa", "QA_WORKFLOW_UNAVAILABLE"),
        ("release", "RELEASE_WORKFLOW_UNAVAILABLE"),
    ):
        if role not in available_roles and not (zero_credit_mode and role == "source_review"):
            return {"action": "HOLD", "findings": [finding], "head_sha": head}

    checks = check_runs if isinstance(check_runs, list) else []
    for name in QA_EVIDENCE_CHECKS:
        check = latest_check(checks, name, head, GITHUB_ACTIONS_APP_ID)
        if check is None:
            return {"action": "HOLD", "findings": [f"REQUIRED_CHECK_MISSING:{name}"], "head_sha": head}
        if check.get("_foreign_source"):
            return {"action": "HOLD", "findings": [f"REQUIRED_CHECK_FOREIGN_SOURCE:{name}"], "head_sha": head}
        if check.get("status") != "completed":
            return {"action": "HOLD", "findings": [f"REQUIRED_CHECK_PENDING:{name}"], "head_sha": head}
        if check.get("conclusion") != "success":
            return {"action": "HOLD", "findings": [f"REQUIRED_CHECK_FAILED:{name}"], "head_sha": head}
    if not zero_credit_mode:
        if source_review not in {
            "PASS",
            "MISSING",
            "PENDING",
            "STALE",
            "REJECT",
            "INVALID",
            "ACTION_REQUIRED_ZERO_JOBS",
        }:
            return {"action": "HOLD", "findings": ["SOURCE_REVIEW_STATUS_INVALID"], "head_sha": head}
        if source_review in {"REJECT", "INVALID"}:
            return {"action": "HOLD", "findings": [f"SOURCE_REVIEW_{source_review}"], "head_sha": head}
        if source_review == "ACTION_REQUIRED_ZERO_JOBS":
            if runs.get("source_review") != "action_required":
                return {"action": "HOLD", "findings": ["SOURCE_REVIEW_BLOCKED_RUN_UNPROVEN"], "head_sha": head}
            return {"action": "DISPATCH", "role": "source_review", "findings": [], "head_sha": head}
        if source_review == "MISSING":
            if runs.get("source_review") in {"queued", "in_progress", "waiting", "pending", "requested"}:
                return {"action": "HOLD", "findings": ["SOURCE_REVIEW_PENDING"], "head_sha": head}
            if runs.get("source_review") is not None:
                return {"action": "HOLD", "findings": ["SOURCE_REVIEW_RUN_FAILED"], "head_sha": head}
            return {"action": "DISPATCH", "role": "source_review", "findings": [], "head_sha": head}
        if source_review == "PENDING":
            return {"action": "HOLD", "findings": ["SOURCE_REVIEW_PENDING"], "head_sha": head}
        if source_review == "STALE":
            if runs.get("source_review") != "success":
                return {"action": "HOLD", "findings": ["SOURCE_REVIEW_STALE_UNPROVEN"], "head_sha": head}
            return {"action": "DISPATCH", "role": "source_review", "findings": [], "head_sha": head}

    if ancillary_ci_hold:
        return {"action": "HOLD", "findings": [ancillary_ci_hold], "head_sha": head}
    if not protected and not zero_credit_mode:
        trusted = latest_check(checks, TRUSTED_GOVERNANCE_CHECK, head, GITHUB_ACTIONS_APP_ID)
        if trusted is None:
            return {
                "action": "HOLD",
                "findings": [f"TRUSTED_GOVERNANCE_CHECK_MISSING:{TRUSTED_GOVERNANCE_CHECK}"],
                "head_sha": head,
            }
        if trusted.get("_foreign_source"):
            return {
                "action": "HOLD",
                "findings": [f"TRUSTED_GOVERNANCE_CHECK_FOREIGN_SOURCE:{TRUSTED_GOVERNANCE_CHECK}"],
                "head_sha": head,
            }
        if trusted.get("status") != "completed":
            return {
                "action": "HOLD",
                "findings": [f"TRUSTED_GOVERNANCE_CHECK_PENDING:{TRUSTED_GOVERNANCE_CHECK}"],
                "head_sha": head,
            }
        if trusted.get("conclusion") != "success":
            return {
                "action": "HOLD",
                "findings": [f"TRUSTED_GOVERNANCE_CHECK_FAILED:{TRUSTED_GOVERNANCE_CHECK}"],
                "head_sha": head,
            }

    qa_check = latest_check(checks, QA_CHECK, head, QA_APP_ID)
    if qa_check is None:
        if runs.get("qa") is not None:
            return {"action": "HOLD", "findings": ["QA_WORKFLOW_RUN_FAILED"], "head_sha": head}
        return {"action": "DISPATCH", "role": "qa", "findings": [], "head_sha": head}
    if qa_check.get("_foreign_source"):
        return {"action": "HOLD", "findings": ["QA_CHECK_FOREIGN_SOURCE"], "head_sha": head}
    if qa_check.get("status") != "completed":
        return {"action": "HOLD", "findings": ["QA_CHECK_PENDING"], "head_sha": head}
    if qa_check.get("conclusion") != "success":
        return {"action": "HOLD", "findings": ["QA_CHECK_FAILED"], "head_sha": head}

    release_check = latest_check(checks, RELEASE_CHECK, head, RELEASE_APP_ID)
    if release_check is None:
        if runs.get("release") is not None:
            return {"action": "HOLD", "findings": ["RELEASE_WORKFLOW_RUN_FAILED"], "head_sha": head}
        return {"action": "DISPATCH", "role": "release", "findings": [], "head_sha": head}
    if release_check.get("_foreign_source"):
        return {"action": "HOLD", "findings": ["RELEASE_CHECK_FOREIGN_SOURCE"], "head_sha": head}
    if release_check.get("status") != "completed":
        return {"action": "HOLD", "findings": ["RELEASE_CHECK_PENDING"], "head_sha": head}
    if release_check.get("conclusion") != "success":
        return {"action": "HOLD", "findings": ["RELEASE_CHECK_FAILED"], "head_sha": head}
    return {"action": "DONE", "findings": [], "head_sha": head}


def run_name(role: str, pr_number: int, head_sha: str) -> str:
    if role == "source_review":
        return f"wow-v17-independent-source-review pr={pr_number} head={head_sha}"
    if role not in {"qa", "release"}:
        raise ValueError("DISPATCH_ROLE_NOT_ALLOWLISTED")
    name = "wow-v17-independent-qa-check" if role == "qa" else "wow-v17-release-authority-check"
    return f"{name} event=workflow_dispatch pr={pr_number} head={head_sha}"


def main() -> int:
    raise SystemExit(
        "Run the GitHub transport entrypoint: python artifacts/wow-engine/v17/governance_dispatcher_github.py"
    )


if __name__ == "__main__":
    main()
