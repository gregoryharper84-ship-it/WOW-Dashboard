#!/usr/bin/env python3
"""Build and verify a time-limited exact-head independent-review receipt."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
WORKFLOW_NAME = "wow-v17-existing-pr-governance-review"
CERTIFICATION_MODE = "EXISTING_PR_REVIEW_EVIDENCE"
CANONICAL_REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
OWNER_LOGIN = "gregoryharper84-ship-it"
OWNER_ID = 285088163
FALLBACK_EXPIRES_UTC = "2026-11-01T00:00:00Z"
SHA = re.compile(r"^[a-f0-9]{40}$")

REQUIRED_WORKFLOWS = {
    "wow-verify": ".github/workflows/wow-verify.yml",
    "wow-engine-verify": ".github/workflows/wow-engine-verify.yml",
    "wow-v17-rapid-repair": ".github/workflows/wow-v17-rapid-repair.yml",
    "wow-v17-change-impact-gate": ".github/workflows/wow-v17-change-impact-gate.yml",
    "wow-v17-engineering-auditor-code-health": ".github/workflows/wow-v17-engineering-auditor-code-health.yml",
    "wow-v17-spread-forward-shadow": ".github/workflows/wow-v17-spread-forward-shadow.yml",
    "wow-v17-release-production-verification-agent": ".github/workflows/wow-v17-release-production-verification-agent.yml",
}
REQUIRED_CHECKS = {
    "WOW required-three regression": 15368,
    "WOW governed probability backend": 15368,
    "WOW additional required regression": 15368,
    "WOW Independent QA exact-head": 5245832,
    "WOW Release Authority exact-head": 5245853,
}
TRUST_ROOT_PREFIXES = (".github/", ".agents/")
PROTECTED_PATHS = frozenset(
    {
        "artifacts/wow-engine/v17/agent_identity_policy.py",
        "artifacts/wow-engine/v17/engineering_agent_team.py",
        "artifacts/wow-engine/v17/engineering_dispatch_manifest.json",
        "artifacts/wow-engine/v17/engineering_dispatch_queue.py",
        "artifacts/wow-engine/v17/engineering_incident_identity.py",
        "artifacts/wow-engine/v17/engineering_effectiveness.py",
        "artifacts/wow-engine/v17/engineering_provider_failover.py",
        "artifacts/wow-engine/v17/independent_release_checks.py",
        "artifacts/wow-engine/v17/persistent_worker_safety.py",
        "artifacts/wow-engine/requirements.txt",
        "artifacts/flask-scoring-api/requirements.txt",
        "render.yaml",
    }
)


class ExistingPrGovernanceReceiptError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExistingPrGovernanceReceiptError(message)


def _exact_keys(value: object, keys: set[str], name: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{name}_INVALID")
    _require(set(value) == keys, f"{name}_SCHEMA_INVALID")
    return value


def _positive_int(value: object, name: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool) and value > 0, f"{name}_INVALID")
    return value


def _timestamp(value: object, name: str) -> datetime:
    _require(isinstance(value, str) and bool(value), f"{name}_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExistingPrGovernanceReceiptError(f"{name}_INVALID") from exc
    _require(parsed.tzinfo is not None, f"{name}_TIMEZONE_MISSING")
    return parsed.astimezone(timezone.utc)


def _login(value: object, name: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{name}_INVALID")
    return value


def _reviewer_allowlist(value: object) -> list[dict[str, Any]]:
    _require(isinstance(value, list) and bool(value), "INDEPENDENT_REVIEWER_ALLOWLIST_MISSING")
    reviewers: list[dict[str, Any]] = []
    ids: set[int] = set()
    logins: set[str] = set()
    for item in value:
        item = _exact_keys(item, {"id", "login"}, "ALLOWLIST_ENTRY")
        reviewer_id = _positive_int(item.get("id"), "ALLOWLIST_REVIEWER_ID")
        login = _login(item.get("login"), "ALLOWLIST_REVIEWER_LOGIN")
        _require(
            reviewer_id != OWNER_ID
            and login.casefold() != OWNER_LOGIN.casefold()
            and not login.casefold().endswith("[bot]"),
            "ALLOWLIST_REVIEWER_NOT_INDEPENDENT_HUMAN",
        )
        _require(reviewer_id not in ids and login.casefold() not in logins, "ALLOWLIST_DUPLICATE_REVIEWER")
        ids.add(reviewer_id)
        logins.add(login.casefold())
        reviewers.append({"id": reviewer_id, "login": login})
    return reviewers


def _latest(records: Sequence[Mapping[str, Any]], *, name: str, expected_sha: str,
            app_id: int | None = None, path: str | None = None,
            time_field: str = "started_at") -> Mapping[str, Any] | None:
    matches = []
    for record in records:
        if record.get("head_sha") != expected_sha or record.get("name") != name:
            continue
        if path is not None and record.get("path") != path:
            continue
        if app_id is not None and (record.get("app") or {}).get("id") != app_id:
            continue
        started = _timestamp(record.get(time_field), "CHECK_STARTED_AT")
        record_id = _positive_int(record.get("id"), "CHECK_ID")
        matches.append((started, record_id, record))
    return max(matches, key=lambda item: (item[0], item[1]))[2] if matches else None


def _independent_approval(
    reviews: Sequence[Mapping[str, Any]],
    *,
    author_login: str,
    author_id: int,
    head_sha: str,
    allowlist: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    allowlist_by_id = {reviewer["id"]: reviewer["login"] for reviewer in allowlist}
    latest: dict[int, tuple[datetime, int, Mapping[str, Any]]] = {}
    for review in reviews:
        _require(isinstance(review, Mapping), "REVIEW_INVENTORY_INVALID")
        user = review.get("user")
        _require(isinstance(user, Mapping), "REVIEWER_IDENTITY_UNKNOWN")
        reviewer_id = _positive_int(user.get("id"), "REVIEWER_IDENTITY_UNKNOWN")
        reviewer_login = _login(user.get("login"), "REVIEWER_IDENTITY_UNKNOWN")
        _require(isinstance(user.get("type"), str) and user.get("type") in {"User", "Bot"},
                 "REVIEWER_IDENTITY_UNKNOWN")
        if (
            user.get("type") == "User"
            and reviewer_id not in allowlist_by_id
            and reviewer_id != author_id
            and reviewer_id != OWNER_ID
        ):
            raise ExistingPrGovernanceReceiptError("REVIEWER_IDENTITY_NOT_ALLOWLISTED")
        if reviewer_id in allowlist_by_id:
            _require(
                reviewer_login.casefold() == allowlist_by_id[reviewer_id].casefold(),
                "REVIEWER_IDENTITY_UNKNOWN",
            )
        submitted = _timestamp(review.get("submitted_at"), "REVIEW_SUBMITTED_AT")
        review_id = _positive_int(review.get("id"), "REVIEW_ID")
        _require(isinstance(review.get("state"), str), "REVIEW_STATE_INVALID")
        current = latest.get(reviewer_id)
        if current is None or (submitted, review_id) > current[:2]:
            latest[reviewer_id] = (submitted, review_id, review)

    for reviewer_id, expected_login in allowlist_by_id.items():
        entry = latest.get(reviewer_id)
        if entry is None:
            continue
        submitted, review_id, review = entry
        user = review["user"]
        reviewer_login = user["login"]
        if (
            reviewer_id == author_id
            or reviewer_login.casefold() == author_login.casefold()
            or reviewer_id == OWNER_ID
            or reviewer_login.casefold() == OWNER_LOGIN.casefold()
            or user.get("type") != "User"
            or reviewer_login.casefold() != expected_login.casefold()
            or review.get("state") != "APPROVED"
            or review.get("commit_id") != head_sha
        ):
            continue
        return {
            "state": "APPROVED",
            "reviewer": {"id": reviewer_id, "login": reviewer_login, "type": "User"},
            "review_id": review_id,
            "commit_id": head_sha,
            "submitted_at": submitted.isoformat().replace("+00:00", "Z"),
        }
    raise ExistingPrGovernanceReceiptError("EXACT_HEAD_INDEPENDENT_APPROVAL_MISSING")


def _validate_changed_files(files: object, expected_count: object) -> list[dict[str, Any]]:
    _require(isinstance(files, list) and bool(files), "DIFF_INVENTORY_MISSING")
    _require(isinstance(expected_count, int) and len(files) == expected_count, "DIFF_INVENTORY_MISMATCH")
    normalized = []
    filenames = set()
    for file in files:
        file = _exact_keys(file, {"filename", "previous_filename"}, "DIFF_FILE")
        paths = [file.get("filename")]
        if file.get("previous_filename") is not None:
            paths.append(file.get("previous_filename"))
        for path in paths:
            _require(
                isinstance(path, str)
                and bool(path)
                and not path.startswith("/")
                and ".." not in path.split("/")
                and "\\" not in path,
                "DIFF_PATH_INVALID",
            )
            _require(
                not path.startswith(TRUST_ROOT_PREFIXES) and path not in PROTECTED_PATHS,
                "TRUST_ROOT_CHANGE_NOT_CERTIFIABLE",
            )
        filename = file["filename"]
        _require(filename not in filenames, "DIFF_INVENTORY_DUPLICATE_PATH")
        filenames.add(filename)
        normalized.append({"filename": filename, "previous_filename": file.get("previous_filename")})
    return sorted(normalized, key=lambda item: item["filename"])


def build_receipt(
    evidence: Mapping[str, Any],
    *,
    expected_repository: str,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_pr_number: int,
    expected_incident_id: str,
    expected_workflow_run_id: str,
    expected_workflow_run_attempt: int,
    trusted_reviewers: object,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "CLOCK_NOT_TRUSTED")
    _require(clock.astimezone(timezone.utc) < _timestamp(FALLBACK_EXPIRES_UTC, "FALLBACK_EXPIRY"),
             "TEMPORARY_FALLBACK_EXPIRED")
    _require(expected_repository == CANONICAL_REPOSITORY, "REPOSITORY_IDENTITY_MISMATCH")
    _require(bool(SHA.fullmatch(expected_head_sha)), "EXPECTED_SHA_INVALID")
    _require(bool(expected_head_ref), "EXPECTED_HEAD_REF_MISSING")
    _positive_int(expected_pr_number, "EXPECTED_PR_NUMBER")
    _positive_int(expected_workflow_run_attempt, "WORKFLOW_RUN_ATTEMPT")
    _require(bool(re.fullmatch(r"[1-9][0-9]*", str(expected_workflow_run_id))), "WORKFLOW_RUN_ID_INVALID")
    _require(bool(re.fullmatch(r"[1-9][0-9]*", expected_incident_id)), "INCIDENT_ID_INVALID")
    _require(evidence.get("complete_inventory") is True, "REVIEW_OR_CI_INVENTORY_INCOMPLETE")

    allowlist = _reviewer_allowlist(trusted_reviewers)
    pr = evidence.get("pr")
    _require(isinstance(pr, Mapping), "PR_EVIDENCE_INVALID")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    _require(isinstance(head, Mapping) and isinstance(base, Mapping), "PR_EVIDENCE_INVALID")
    _require(isinstance(pr.get("user"), Mapping), "PR_AUTHOR_IDENTITY_UNKNOWN")
    _require(isinstance(head.get("repo"), Mapping) and isinstance(base.get("repo"), Mapping),
             "PR_REPOSITORY_IDENTITY_UNKNOWN")
    head_repo = head["repo"].get("full_name")
    base_repo = base["repo"].get("full_name")
    author = pr.get("user") or {}
    author_login = _login(author.get("login"), "PR_AUTHOR_IDENTITY_UNKNOWN")
    author_id = _positive_int(author.get("id"), "PR_AUTHOR_IDENTITY_UNKNOWN")
    _require(
        pr.get("number") == expected_pr_number
        and pr.get("state") == "open"
        and pr.get("draft") is False
        and not pr.get("merged_at")
        and base.get("ref") == "main"
        and base_repo == expected_repository
        and head_repo == expected_repository
        and head.get("sha") == expected_head_sha
        and head.get("ref") == expected_head_ref,
        "PR_SHA_REPOSITORY_STATE_OR_BASE_MISMATCH",
    )

    changed_files = _validate_changed_files(evidence.get("changed_files"), pr.get("changed_files"))
    reviews = evidence.get("reviews")
    check_runs = evidence.get("check_runs")
    workflow_runs = evidence.get("workflow_runs")
    _require(isinstance(reviews, list), "REVIEW_INVENTORY_INVALID")
    _require(isinstance(check_runs, list), "CHECK_INVENTORY_INVALID")
    _require(isinstance(workflow_runs, list), "WORKFLOW_INVENTORY_INVALID")
    review = _independent_approval(
        reviews,
        author_login=author_login,
        author_id=author_id,
        head_sha=expected_head_sha,
        allowlist=allowlist,
    )

    verified_workflows = []
    for name, path in REQUIRED_WORKFLOWS.items():
        run = _latest(
            workflow_runs, name=name, expected_sha=expected_head_sha, path=path, time_field="created_at"
        )
        _require(run is not None, f"REQUIRED_WORKFLOW_MISSING:{name}")
        _require(run.get("status") == "completed" and run.get("conclusion") == "success",
                 f"REQUIRED_WORKFLOW_NOT_GREEN:{name}")
        verified_workflows.append({"name": name, "path": path, "run_id": _positive_int(run.get("id"), "WORKFLOW_RUN_ID"),
                                   "head_sha": expected_head_sha, "status": "completed", "conclusion": "success"})

    verified_checks = {}
    for name, app_id in REQUIRED_CHECKS.items():
        check = _latest(check_runs, name=name, expected_sha=expected_head_sha, app_id=app_id)
        _require(check is not None, f"REQUIRED_CHECK_MISSING:{name}")
        _require(check.get("status") == "completed" and check.get("conclusion") == "success",
                 f"REQUIRED_CHECK_NOT_GREEN:{name}")
        verified_checks[name] = {
            "id": _positive_int(check.get("id"), "CHECK_ID"),
            "app_id": app_id,
            "head_sha": expected_head_sha,
            "status": "completed",
            "conclusion": "success",
        }

    return {
        "receipt_schema_version": 2,
        "workflow_name": WORKFLOW_NAME,
        "repository": expected_repository,
        "workflow_run_id": str(expected_workflow_run_id),
        "workflow_run_attempt": expected_workflow_run_attempt,
        "created_at_utc": clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "certification_mode": CERTIFICATION_MODE,
        "fallback_expires_utc": FALLBACK_EXPIRES_UTC,
        "target": {
            "pr_number": expected_pr_number,
            "incident_id": expected_incident_id,
            "author": {"login": author_login, "id": author_id},
            "base_ref": "main",
            "head_ref": expected_head_ref,
            "head_sha": expected_head_sha,
            "head_repository": expected_repository,
        },
        "trusted_reviewers": allowlist,
        "changed_files": changed_files,
        "independent_review": review,
        "required_workflows": verified_workflows,
        "required_checks": verified_checks,
        "evidence_state": "REVIEW_EVIDENCE_VERIFIED",
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def verify_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_repository: str,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_workflow_run_id: str,
    expected_workflow_name: str,
    expected_pr_number: int,
    now: datetime | None = None,
) -> None:
    keys = {
        "receipt_schema_version", "workflow_name", "repository", "workflow_run_id",
        "workflow_run_attempt", "created_at_utc", "certification_mode",
        "fallback_expires_utc", "target", "trusted_reviewers", "changed_files",
        "independent_review", "required_workflows", "required_checks", "evidence_state",
        "terminal_authority", "can_execute",
    }
    _exact_keys(receipt, keys, "RECEIPT")
    _require(receipt.get("receipt_schema_version") == 2, "RECEIPT_SCHEMA_VERSION_UNSUPPORTED")
    _require(expected_workflow_name == WORKFLOW_NAME and receipt.get("workflow_name") == WORKFLOW_NAME,
             "RECEIPT_WORKFLOW_IDENTITY_MISMATCH")
    _require(receipt.get("certification_mode") == CERTIFICATION_MODE, "CERTIFICATION_MODE_MISMATCH")
    _require(receipt.get("repository") == expected_repository == CANONICAL_REPOSITORY,
             "REPOSITORY_IDENTITY_MISMATCH")
    _require(
        bool(re.fullmatch(r"[1-9][0-9]*", str(receipt.get("workflow_run_id"))))
        and str(receipt.get("workflow_run_id")) == str(expected_workflow_run_id),
        "WORKFLOW_RUN_ID_MISMATCH",
    )
    _positive_int(receipt.get("workflow_run_attempt"), "WORKFLOW_RUN_ATTEMPT")
    created = _timestamp(receipt.get("created_at_utc"), "RECEIPT_CREATED_AT")
    expiry = _timestamp(receipt.get("fallback_expires_utc"), "FALLBACK_EXPIRY")
    _require(receipt.get("fallback_expires_utc") == FALLBACK_EXPIRES_UTC, "FALLBACK_EXPIRY_MISMATCH")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "CLOCK_NOT_TRUSTED")
    clock = clock.astimezone(timezone.utc)
    _require(created <= clock < expiry, "TEMPORARY_FALLBACK_EXPIRED_OR_FUTURE_RECEIPT")
    _require(bool(SHA.fullmatch(expected_head_sha)), "EXPECTED_SHA_INVALID")
    target = _exact_keys(
        receipt.get("target"),
        {"pr_number", "incident_id", "author", "base_ref", "head_ref", "head_sha", "head_repository"},
        "TARGET",
    )
    _require(
        _positive_int(target.get("pr_number"), "PR_NUMBER") == expected_pr_number,
        "RECEIPT_PR_NUMBER_MISMATCH",
    )
    _require(bool(re.fullmatch(r"[1-9][0-9]*", str(target.get("incident_id")))), "INCIDENT_ID_INVALID")
    author = _exact_keys(target.get("author"), {"login", "id"}, "PR_AUTHOR")
    _login(author.get("login"), "PR_AUTHOR_LOGIN")
    _positive_int(author.get("id"), "PR_AUTHOR_ID")
    _require(
        target.get("base_ref") == "main"
        and target.get("head_ref") == expected_head_ref
        and target.get("head_sha") == expected_head_sha
        and target.get("head_repository") == expected_repository,
        "RECEIPT_TARGET_MISMATCH",
    )

    allowlist = _reviewer_allowlist(receipt.get("trusted_reviewers"))
    review = _exact_keys(
        receipt.get("independent_review"),
        {"state", "reviewer", "review_id", "commit_id", "submitted_at"},
        "INDEPENDENT_REVIEW",
    )
    reviewer = _exact_keys(review.get("reviewer"), {"id", "login", "type"}, "REVIEWER")
    reviewer_id = _positive_int(reviewer.get("id"), "REVIEWER_ID")
    reviewer_login = _login(reviewer.get("login"), "REVIEWER_LOGIN")
    _require(
        any(item["id"] == reviewer_id and item["login"].casefold() == reviewer_login.casefold()
            for item in allowlist),
        "REVIEWER_NOT_ALLOWLISTED",
    )
    _require(
        reviewer_id != author["id"]
        and reviewer_login.casefold() != author["login"].casefold()
        and reviewer_id != OWNER_ID
        and reviewer_login.casefold() != OWNER_LOGIN.casefold()
        and reviewer.get("type") == "User"
        and review.get("state") == "APPROVED"
        and review.get("commit_id") == expected_head_sha,
        "INDEPENDENT_REVIEW_INVALID",
    )
    _positive_int(review.get("review_id"), "REVIEW_ID")
    _timestamp(review.get("submitted_at"), "REVIEW_SUBMITTED_AT")

    changed_files = receipt.get("changed_files")
    _require(isinstance(changed_files, list), "DIFF_INVENTORY_INVALID")
    files = _validate_changed_files(changed_files, len(changed_files))
    _require(files == receipt.get("changed_files"), "DIFF_INVENTORY_INVALID")

    workflows = receipt.get("required_workflows")
    _require(isinstance(workflows, list) and len(workflows) == len(REQUIRED_WORKFLOWS),
             "REQUIRED_WORKFLOW_INVENTORY_INVALID")
    workflow_by_name = {}
    for item in workflows:
        item = _exact_keys(item, {"name", "path", "run_id", "head_sha", "status", "conclusion"},
                           "REQUIRED_WORKFLOW")
        name = item.get("name")
        _require(name in REQUIRED_WORKFLOWS and name not in workflow_by_name, "REQUIRED_WORKFLOW_IDENTITY_INVALID")
        _require(item.get("path") == REQUIRED_WORKFLOWS[name], "REQUIRED_WORKFLOW_PATH_INVALID")
        _positive_int(item.get("run_id"), "WORKFLOW_RUN_ID")
        _require(item.get("head_sha") == expected_head_sha, "REQUIRED_WORKFLOW_HEAD_MISMATCH")
        _require(item.get("status") == "completed" and item.get("conclusion") == "success",
                 "REQUIRED_WORKFLOW_NOT_GREEN")
        workflow_by_name[name] = item
    _require(set(workflow_by_name) == set(REQUIRED_WORKFLOWS), "REQUIRED_WORKFLOW_INVENTORY_INCOMPLETE")

    checks = receipt.get("required_checks")
    _exact_keys(checks, set(REQUIRED_CHECKS), "REQUIRED_CHECKS")
    for name, app_id in REQUIRED_CHECKS.items():
        check = _exact_keys(checks.get(name), {"id", "app_id", "head_sha", "status", "conclusion"},
                            "REQUIRED_CHECK")
        _positive_int(check.get("id"), "CHECK_ID")
        _require(check.get("app_id") == app_id, f"REQUIRED_CHECK_APP_MISMATCH:{name}")
        _require(check.get("head_sha") == expected_head_sha, f"REQUIRED_CHECK_HEAD_MISMATCH:{name}")
        _require(check.get("status") == "completed" and check.get("conclusion") == "success",
                 f"REQUIRED_CHECK_NOT_GREEN:{name}")
    _require(receipt.get("evidence_state") == "REVIEW_EVIDENCE_VERIFIED",
             "REVIEW_EVIDENCE_NOT_VERIFIED")
    _require(receipt.get("terminal_authority") == TERMINAL_AUTHORITY, "TERMINAL_AUTHORITY_MISMATCH")
    _require(receipt.get("can_execute") is False, "CAN_EXECUTE_INVARIANT_VIOLATED")


def _event_pr_number(event_path: str, expected_head_sha: str) -> int:
    _require(bool(event_path), "PR_CONTEXT_MISSING")
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExistingPrGovernanceReceiptError("PR_CONTEXT_INVALID") from exc
    _require(isinstance(event, Mapping), "PR_CONTEXT_INVALID")
    pull_request = event.get("pull_request")
    if isinstance(pull_request, Mapping):
        return _positive_int(pull_request.get("number"), "PR_CONTEXT_NUMBER")
    workflow_run = event.get("workflow_run")
    if isinstance(workflow_run, Mapping):
        pull_requests = workflow_run.get("pull_requests")
        _require(isinstance(pull_requests, list), "PR_CONTEXT_INVALID")
        candidates = [
            item.get("number")
            for item in pull_requests
            if isinstance(item, Mapping)
            and isinstance(item.get("head"), Mapping)
            and item["head"].get("sha") == expected_head_sha
        ]
        _require(len(candidates) == 1, "PR_CONTEXT_AMBIGUOUS_OR_MISSING")
        return _positive_int(candidates[0], "PR_CONTEXT_NUMBER")
    inputs = event.get("inputs")
    if isinstance(inputs, Mapping):
        number = inputs.get("pr_number")
        _require(isinstance(number, (str, int)) and bool(re.fullmatch(r"[1-9][0-9]*", str(number))),
                 "PR_CONTEXT_NUMBER_INVALID")
        return int(number)
    raise ExistingPrGovernanceReceiptError("PR_CONTEXT_AMBIGUOUS_OR_MISSING")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--receipt", type=Path)
    mode.add_argument("--evidence", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trusted-reviewers-json")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--head-ref", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-name", required=True)
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--incident-id")
    parser.add_argument("--workflow-run-attempt", type=int)
    args = parser.parse_args(argv)

    try:
        if args.evidence:
            _require(args.output is not None, "RECEIPT_OUTPUT_MISSING")
            _require(args.pr_number is not None, "EXPECTED_PR_NUMBER_MISSING")
            _require(bool(args.incident_id), "EXPECTED_INCIDENT_ID_MISSING")
            _require(args.workflow_run_attempt is not None, "WORKFLOW_RUN_ATTEMPT_MISSING")
            _require(args.trusted_reviewers_json is not None, "INDEPENDENT_REVIEWER_ALLOWLIST_MISSING")
            try:
                allowlist = json.loads(args.trusted_reviewers_json)
            except json.JSONDecodeError as exc:
                raise ExistingPrGovernanceReceiptError("INDEPENDENT_REVIEWER_ALLOWLIST_INVALID") from exc
            receipt = build_receipt(
                json.loads(args.evidence.read_text(encoding="utf-8")),
                expected_repository=args.repository,
                expected_head_sha=args.head_sha,
                expected_head_ref=args.head_ref,
                expected_pr_number=args.pr_number,
                expected_incident_id=args.incident_id,
                expected_workflow_run_id=args.workflow_run_id,
                expected_workflow_run_attempt=args.workflow_run_attempt,
                trusted_reviewers=allowlist,
            )
            verify_receipt(
                receipt,
                expected_repository=args.repository,
                expected_head_sha=args.head_sha,
                expected_head_ref=args.head_ref,
                expected_workflow_run_id=args.workflow_run_id,
                expected_workflow_name=args.workflow_name,
                expected_pr_number=args.pr_number,
            )
            args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
            pr_number = args.pr_number or _event_pr_number(
                os.environ.get("GITHUB_EVENT_PATH", ""), args.head_sha
            )
            verify_receipt(
                receipt,
                expected_repository=args.repository,
                expected_head_sha=args.head_sha,
                expected_head_ref=args.head_ref,
                expected_workflow_run_id=args.workflow_run_id,
                expected_workflow_name=args.workflow_name,
                expected_pr_number=pr_number,
            )
    except ExistingPrGovernanceReceiptError as exc:
        print(json.dumps({"status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 1
    except (OSError, json.JSONDecodeError):
        print(json.dumps({"status": "HOLD", "reason": "EVIDENCE_OR_RECEIPT_IO_INVALID"}, sort_keys=True))
        return 1

    print(json.dumps({"status": "REVIEW_EVIDENCE_VERIFIED", "head_sha": args.head_sha,
                      "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
