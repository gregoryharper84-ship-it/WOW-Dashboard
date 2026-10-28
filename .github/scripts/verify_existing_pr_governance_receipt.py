#!/usr/bin/env python3
"""Build and verify a time-limited exact-head independent-review receipt."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

_source_review_spec = importlib.util.spec_from_file_location(
    "_independent_source_review_receipt", Path(__file__).with_name("independent_source_review_receipt.py")
)
_source_review_module = importlib.util.module_from_spec(_source_review_spec)
assert _source_review_spec and _source_review_spec.loader
_source_review_spec.loader.exec_module(_source_review_module)

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
WORKFLOW_NAME = "wow-v17-existing-pr-governance-review"
CERTIFICATION_MODE = "EXISTING_PR_REVIEW_EVIDENCE"
BOOTSTRAP_MODE = "TRUST_ROOT_BOOTSTRAP"
ORDINARY_MODE = "ORDINARY_ENGINEERING"
DETERMINISTIC_MODE = "CLASS_A_DETERMINISTIC"
DETERMINISTIC_ASSURANCE = "ZERO_CREDIT_DETERMINISTIC_ONLY_NO_SEMANTIC_REVIEW"
DETERMINISTIC_CHECKS = {
    "WOW Independent QA exact-head": 5245832,
    "WOW Release Authority exact-head": 5245853,
}
# Exact initial canary inventory, not a recursive directory allowlist.
# New paths require a separately reviewed policy update. This cannot approve
# arbitrary source inserted underneath the training directory.
CLASS_A_EXACT_PATHS = frozenset({
    "artifacts/wow-engine/learning/AGENT_TRAINING.md",
    "artifacts/wow-engine/learning/README.md",
    "artifacts/wow-engine/learning/challenges/reference.py",
    "artifacts/wow-engine/learning/challenges/run_challenge_tests.py",
    "artifacts/wow-engine/learning/challenges/starter.py",
    "artifacts/wow-engine/learning/challenges/test_contracts.py",
    "artifacts/wow-engine/learning/data/cases.json",
    "artifacts/wow-engine/learning/evaluate.py",
    "artifacts/wow-engine/learning/tests/test_evaluate.py",
    "artifacts/wow-engine/test_v17_triple_d_learning_smoke.py",
})
CANONICAL_REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
OWNER_LOGIN = "gregoryharper84-ship-it"
OWNER_ID = 285088163
REVIEW_RECEIPT_MAX_AGE = timedelta(minutes=30)
# Temporary Claude-provider substitution: independently authorized renewal required.
FALLBACK_PROVIDER_SUNSET_UTC = "2026-11-01T00:00:00Z"
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
}
# Post-governance authority. These are not prerequisites of an ordinary receipt.
# Ruleset 24907422 pins QA and Release to these integration ids.
BOOTSTRAP_CHECKS = {
    **REQUIRED_CHECKS,
    "WOW V17 change impact gate": 15368,
    "WOW Independent QA exact-head": 5245832,
    "WOW Release Authority exact-head": 5245853,
}
TRUST_ROOT_PREFIXES = (".github/", ".agents/", ".claude/")
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


def _latest_required_workflow(
    records: Sequence[Mapping[str, Any]], *, name: str, path: str, expected_sha: str,
    expected_repository: str,
) -> Mapping[str, Any] | None:
    matches = []
    for record in records:
        if record.get("head_sha") != expected_sha or record.get("name") != name or record.get("path") != path:
            continue
        if record.get("conclusion") == "action_required":
            _require(
                record.get("status") == "completed"
                and record.get("event") == "pull_request"
                and record.get("pre_job_zero_jobs_verified") is True,
                "REQUIRED_WORKFLOW_ZERO_JOB_SHADOW_UNVERIFIED",
            )
            _require(
                isinstance(record.get("repository"), Mapping)
                and record["repository"].get("full_name") == expected_repository
                and isinstance(record.get("head_repository"), Mapping)
                and record["head_repository"].get("full_name") == expected_repository,
                "REQUIRED_WORKFLOW_ZERO_JOB_REPOSITORY_MISMATCH",
            )
            _positive_int(record.get("id"), "WORKFLOW_RUN_ID")
            continue
        started = _timestamp(record.get("created_at"), "WORKFLOW_CREATED_AT")
        run_id = _positive_int(record.get("id"), "WORKFLOW_RUN_ID")
        matches.append((started, run_id, record))
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
    allowed_statuses = {"added", "changed", "copied", "modified", "removed", "renamed", "unchanged"}
    for file in files:
        file = _exact_keys(file, {"filename", "previous_filename", "status"}, "DIFF_FILE")
        status = file.get("status")
        _require(status in allowed_statuses, "DIFF_STATUS_INVALID")
        paths = [file.get("filename")]
        previous_filename = file.get("previous_filename")
        if status in {"renamed", "copied"}:
            _require(isinstance(previous_filename, str) and bool(previous_filename),
                     "DIFF_PREVIOUS_FILENAME_MISSING")
            paths.append(previous_filename)
        elif previous_filename is not None:
            paths.append(previous_filename)
        for path in paths:
            canonical = PurePosixPath(path) if isinstance(path, str) else None
            _require(
                isinstance(path, str)
                and bool(path)
                and not path.startswith("/")
                and ".." not in path.split("/")
                and path != "."
                and "\\" not in path
                and str(canonical) == path
                and not path.startswith("./"),
                "DIFF_PATH_INVALID",
            )
        if status in {"renamed", "copied"}:
            _require(previous_filename != file["filename"], "DIFF_PROVENANCE_INVALID")
        filename = file["filename"]
        _require(filename not in filenames, "DIFF_INVENTORY_DUPLICATE_PATH")
        filenames.add(filename)
        normalized.append(
            {"filename": filename, "previous_filename": previous_filename, "status": status}
        )
    return sorted(normalized, key=lambda item: item["filename"])


def _protected_paths(repo_root: Path) -> set[str]:
    policy_path = repo_root / "artifacts/wow-engine/v17/agent_identity_policy.py"
    _require(policy_path.is_file(), "GOVERNANCE_TRUST_ROOT_POLICY_MISSING")
    try:
        spec = importlib.util.spec_from_file_location("_wow_agent_identity_policy", policy_path)
        _require(spec is not None and spec.loader is not None,
                 "GOVERNANCE_TRUST_ROOT_POLICY_UNLOADABLE")
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        roots = policy.governance_trust_roots(repo_root)
        declared = policy.trust_root_paths(repo_root)
    except ExistingPrGovernanceReceiptError:
        raise
    except Exception as exc:
        raise ExistingPrGovernanceReceiptError("GOVERNANCE_TRUST_ROOT_POLICY_FAILED") from exc
    _require(isinstance(roots, list) and isinstance(declared, list),
             "GOVERNANCE_TRUST_ROOT_POLICY_INVALID")
    return set(roots) | set(declared) | set(PROTECTED_PATHS)


def _validate_class_a_files(files: Sequence[Mapping[str, Any]], *, repo_root: Path) -> None:
    """Fail closed unless every changed and former path is preapproved, non-governance training source."""
    _require(bool(files), "CLASS_A_DIFF_EMPTY")
    _validate_protected_diff(files, repo_root=repo_root)
    for file in files:
        _require(file.get("status") in {"added", "modified", "changed"},
                 "CLASS_A_CHANGE_TYPE_NOT_ALLOWED")
        _require(file.get("previous_filename") is None, "CLASS_A_RENAME_OR_COPY_NOT_ALLOWED")
        path = file["filename"]
        _require(path in CLASS_A_EXACT_PATHS,
                 "CLASS_A_PATH_NOT_ALLOWLISTED")


def _deterministic_check(record: Mapping[str, Any], *, name: str) -> None:
    summary = (record.get("output") or {}).get("summary")
    _require(isinstance(summary, str) and summary.startswith(
        "ZERO-CREDIT DETERMINISTIC QA ONLY; no semantic/adversarial source review performed."
    ), f"CLASS_A_ASSURANCE_LABEL_MISSING:{name}")
    started = _timestamp(record.get("started_at"), "CLASS_A_CHECK_STARTED_AT")
    completed = _timestamp(record.get("completed_at"), "CLASS_A_CHECK_COMPLETED_AT")
    _require(completed >= started, f"CLASS_A_CHECK_TIME_INVALID:{name}")


def _validate_automated_review(
    artifact: object,
    run: Mapping[str, Any] | None,
    *,
    expected_repository: str,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_pr_number: int,
    now: datetime | None = None,
) -> str:
    _require(isinstance(artifact, Mapping), "SOURCE_REVIEW_ARTIFACT_INVALID")
    _require(isinstance(run, Mapping), "SOURCE_REVIEW_RUN_METADATA_MISSING")
    try:
        _source_review_module.verify_artifact(
            artifact,
            run=run,
            expected_repository=expected_repository,
            expected_pr_number=expected_pr_number,
            expected_head_sha=expected_head_sha,
            expected_head_ref=expected_head_ref,
            now=now,
        )
    except _source_review_module.SourceReviewError as exc:
        raise ExistingPrGovernanceReceiptError(str(exc)) from exc
    return hashlib.sha256(
        (json.dumps(artifact, indent=2, sort_keys=True) + "\n").encode("utf-8")
    ).hexdigest()


def _validate_protected_diff(files: Sequence[Mapping[str, Any]], *, repo_root: Path) -> None:
    protected = _protected_paths(repo_root)
    for file in files:
        for path in (file["filename"], file.get("previous_filename")):
            if path is not None:
                _require(
                    not path.startswith(TRUST_ROOT_PREFIXES) and path not in protected,
                    "TRUST_ROOT_CHANGE_NOT_CERTIFIABLE",
                )


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
    trusted_reviewers: object | None = None,
    automated_review: object | None = None,
    source_review_run: Mapping[str, Any] | None = None,
    source_review_artifact_id: int | None = None,
    deterministic_class_a: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "CLOCK_NOT_TRUSTED")
    if automated_review is None and not deterministic_class_a:
        _require(
            clock.astimezone(timezone.utc) < _timestamp(FALLBACK_PROVIDER_SUNSET_UTC, "PROVIDER_SUNSET"),
            "TEMPORARY_FALLBACK_PROVIDER_SUNSET",
        )
    _require(expected_repository == CANONICAL_REPOSITORY, "REPOSITORY_IDENTITY_MISMATCH")
    _require(bool(SHA.fullmatch(expected_head_sha)), "EXPECTED_SHA_INVALID")
    _require(bool(expected_head_ref), "EXPECTED_HEAD_REF_MISSING")
    _positive_int(expected_pr_number, "EXPECTED_PR_NUMBER")
    _positive_int(expected_workflow_run_attempt, "WORKFLOW_RUN_ATTEMPT")
    _require(bool(re.fullmatch(r"[1-9][0-9]*", str(expected_workflow_run_id))), "WORKFLOW_RUN_ID_INVALID")
    _require(bool(re.fullmatch(r"[1-9][0-9]*", expected_incident_id)), "INCIDENT_ID_INVALID")
    _require(evidence.get("complete_inventory") is True, "REVIEW_OR_CI_INVENTORY_INCOMPLETE")

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
    _validate_protected_diff(changed_files, repo_root=Path.cwd())
    reviews = evidence.get("reviews")
    check_runs = evidence.get("check_runs")
    workflow_runs = evidence.get("workflow_runs")
    _require(isinstance(reviews, list), "REVIEW_INVENTORY_INVALID")
    _require(isinstance(check_runs, list), "CHECK_INVENTORY_INVALID")
    _require(isinstance(workflow_runs, list), "WORKFLOW_INVENTORY_INVALID")
    if deterministic_class_a:
        _require(automated_review is None and trusted_reviewers is None,
                 "CLASS_A_CANNOT_IMPERSONATE_INDEPENDENT_REVIEW")
        _validate_class_a_files(changed_files, repo_root=Path.cwd())
        review = None
        allowlist = []
        receipt_schema_version = 4
    elif automated_review is None:
        allowlist = _reviewer_allowlist(trusted_reviewers)
        review = _independent_approval(
            reviews,
            author_login=author_login,
            author_id=author_id,
            head_sha=expected_head_sha,
            allowlist=allowlist,
        )
        receipt_schema_version = 2
    else:
        _require(trusted_reviewers is None, "SOURCE_REVIEW_AND_HUMAN_ALLOWLIST_CONFLICT")
        artifact_digest = _validate_automated_review(
            automated_review,
            source_review_run,
            expected_repository=expected_repository,
            expected_head_sha=expected_head_sha,
            expected_head_ref=expected_head_ref,
            expected_pr_number=expected_pr_number,
            now=clock,
        )
        _require(
            (automated_review.get("target") or {}).get("author")
            == {"id": author_id, "login": author_login},
            "SOURCE_REVIEW_AUTHOR_MISMATCH",
        )
        reviewed_paths = (automated_review.get("diff") or {}).get("file_paths")
        _require(
            isinstance(reviewed_paths, list)
            and reviewed_paths == sorted(file["filename"] for file in changed_files),
            "SOURCE_REVIEW_DIFF_INVENTORY_MISMATCH",
        )
        _require(source_review_artifact_id is not None, "SOURCE_REVIEW_ARTIFACT_ID_MISSING")
        review = {
            "type": "AUTOMATED_SOURCE_REVIEW",
            "artifact_id": _positive_int(source_review_artifact_id, "SOURCE_REVIEW_ARTIFACT_ID_INVALID"),
            "artifact_sha256": artifact_digest,
            "artifact": dict(automated_review),
        }
        allowlist = []
        receipt_schema_version = 3

    verified_workflows = []
    for name, path in REQUIRED_WORKFLOWS.items():
        run = _latest_required_workflow(
            workflow_runs,
            name=name,
            path=path,
            expected_sha=expected_head_sha,
            expected_repository=expected_repository,
        )
        _require(run is not None, f"REQUIRED_WORKFLOW_MISSING:{name}")
        _require(run.get("status") == "completed" and run.get("conclusion") == "success",
                 f"REQUIRED_WORKFLOW_NOT_GREEN:{name}")
        verified_workflows.append({"name": name, "path": path, "run_id": _positive_int(run.get("id"), "WORKFLOW_RUN_ID"),
                                   "head_sha": expected_head_sha, "status": "completed", "conclusion": "success"})

    required_checks = {**REQUIRED_CHECKS, **DETERMINISTIC_CHECKS} if deterministic_class_a else REQUIRED_CHECKS
    verified_checks = {}
    for name, app_id in required_checks.items():
        check = _latest(check_runs, name=name, expected_sha=expected_head_sha, app_id=app_id)
        _require(check is not None, f"REQUIRED_CHECK_MISSING:{name}")
        _require(check.get("status") == "completed" and check.get("conclusion") == "success",
                 f"REQUIRED_CHECK_NOT_GREEN:{name}")
        if name in DETERMINISTIC_CHECKS:
            _deterministic_check(check, name=name)
        verified_checks[name] = {
            "id": _positive_int(check.get("id"), "CHECK_ID"),
            "app_id": app_id,
            "head_sha": expected_head_sha,
            "status": "completed",
            "conclusion": "success",
        }

    receipt = {
        "receipt_schema_version": receipt_schema_version,
        "workflow_name": WORKFLOW_NAME,
        "repository": expected_repository,
        "workflow_run_id": str(expected_workflow_run_id),
        "workflow_run_attempt": expected_workflow_run_attempt,
        "created_at_utc": clock.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "certification_mode": DETERMINISTIC_MODE if deterministic_class_a else CERTIFICATION_MODE,
        "fallback_expires_utc": (
            clock.astimezone(timezone.utc) + REVIEW_RECEIPT_MAX_AGE
        ).isoformat().replace("+00:00", "Z"),
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
        "evidence_state": "CLASS_A_DETERMINISTIC_VERIFIED" if deterministic_class_a else "REVIEW_EVIDENCE_VERIFIED",
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }
    if deterministic_class_a:
        receipt["assurance"] = DETERMINISTIC_ASSURANCE
    return receipt


def verify_receipt(
    receipt: Mapping[str, Any],
    *,
    expected_repository: str,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_workflow_run_id: str,
    expected_workflow_name: str,
    expected_pr_number: int,
    live_reviews: Sequence[Mapping[str, Any]],
    source_review_run: Mapping[str, Any] | None = None,
    live_checks: Sequence[Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> None:
    keys = {
        "receipt_schema_version", "workflow_name", "repository", "workflow_run_id",
        "workflow_run_attempt", "created_at_utc", "certification_mode",
        "fallback_expires_utc", "target", "trusted_reviewers", "changed_files",
        "independent_review", "required_workflows", "required_checks", "evidence_state",
        "terminal_authority", "can_execute",
    }
    schema_version = receipt.get("receipt_schema_version")
    _require(schema_version in {2, 3, 4}, "RECEIPT_SCHEMA_VERSION_UNSUPPORTED")
    if schema_version == 4:
        keys.add("assurance")
    _exact_keys(receipt, keys, "RECEIPT")
    _require(expected_workflow_name == WORKFLOW_NAME and receipt.get("workflow_name") == WORKFLOW_NAME,
             "RECEIPT_WORKFLOW_IDENTITY_MISMATCH")
    _require(receipt.get("certification_mode") == (DETERMINISTIC_MODE if schema_version == 4 else CERTIFICATION_MODE),
             "CERTIFICATION_MODE_MISMATCH")
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
    _require(expiry == created + REVIEW_RECEIPT_MAX_AGE, "FALLBACK_EXPIRY_MISMATCH")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "CLOCK_NOT_TRUSTED")
    clock = clock.astimezone(timezone.utc)
    if schema_version == 2:
        _require(
            clock < _timestamp(FALLBACK_PROVIDER_SUNSET_UTC, "PROVIDER_SUNSET"),
            "TEMPORARY_FALLBACK_PROVIDER_SUNSET",
        )
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
    changed_files = receipt.get("changed_files")
    _require(isinstance(changed_files, list), "DIFF_INVENTORY_INVALID")
    files = _validate_changed_files(changed_files, len(changed_files))
    _require(files == receipt.get("changed_files"), "DIFF_INVENTORY_INVALID")
    _validate_protected_diff(files, repo_root=Path.cwd())

    review = receipt.get("independent_review")
    if schema_version == 4:
        _require(receipt.get("assurance") == DETERMINISTIC_ASSURANCE,
                 "CLASS_A_ASSURANCE_IDENTITY_MISMATCH")
        _validate_class_a_files(files, repo_root=Path.cwd())
        _require(review is None and receipt.get("trusted_reviewers") == [],
                 "CLASS_A_MUST_NOT_CLAIM_INDEPENDENT_REVIEW")
    elif schema_version == 2:
        allowlist = _reviewer_allowlist(receipt.get("trusted_reviewers"))
        review = _exact_keys(
            review,
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
    elif schema_version == 3:
        _require(receipt.get("trusted_reviewers") == [], "AUTOMATED_REVIEW_MUST_NOT_CARRY_HUMAN_ALLOWLIST")
        review = _exact_keys(
            review,
            {"type", "artifact_id", "artifact_sha256", "artifact"},
            "AUTOMATED_INDEPENDENT_REVIEW",
        )
        _require(review.get("type") == "AUTOMATED_SOURCE_REVIEW", "AUTOMATED_REVIEW_TYPE_INVALID")
        _positive_int(review.get("artifact_id"), "SOURCE_REVIEW_ARTIFACT_ID_INVALID")
        digest = review.get("artifact_sha256")
        _require(isinstance(digest, str) and bool(re.fullmatch(r"[a-f0-9]{64}", digest)),
                 "SOURCE_REVIEW_ARTIFACT_DIGEST_INVALID")
        artifact_bytes = (json.dumps(review.get("artifact"), indent=2, sort_keys=True) + "\n").encode("utf-8")
        _require(hashlib.sha256(artifact_bytes).hexdigest() == digest,
                 "SOURCE_REVIEW_ARTIFACT_DIGEST_MISMATCH")
        _validate_automated_review(
            review.get("artifact"),
            source_review_run,
            expected_repository=expected_repository,
            expected_head_sha=expected_head_sha,
            expected_head_ref=expected_head_ref,
            expected_pr_number=expected_pr_number,
            now=clock,
        )
        reviewed_paths = (review["artifact"].get("diff") or {}).get("file_paths")
        _require(
            isinstance(reviewed_paths, list)
            and reviewed_paths == sorted(file["filename"] for file in files),
            "SOURCE_REVIEW_DIFF_INVENTORY_MISMATCH",
        )
        _require(
            (review["artifact"].get("target") or {}).get("author") == dict(author),
            "SOURCE_REVIEW_AUTHOR_MISMATCH",
        )

    _require(isinstance(live_reviews, Sequence) and not isinstance(live_reviews, (str, bytes)),
             "LIVE_REVIEW_INVENTORY_INVALID")
    if schema_version == 2:
        live_approval = _independent_approval(
            live_reviews,
            author_login=author["login"],
            author_id=author["id"],
            head_sha=expected_head_sha,
            allowlist=allowlist,
        )
        _require(
            live_approval["reviewer"]["id"] == reviewer_id
            and live_approval["reviewer"]["login"].casefold() == reviewer_login.casefold()
            and live_approval["review_id"] == review.get("review_id"),
            "INDEPENDENT_REVIEW_WITHDRAWN_OR_CHANGED",
        )

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
    required_checks = {**REQUIRED_CHECKS, **DETERMINISTIC_CHECKS} if schema_version == 4 else REQUIRED_CHECKS
    _exact_keys(checks, set(required_checks), "REQUIRED_CHECKS")
    if schema_version == 4:
        _require(isinstance(live_checks, Sequence) and not isinstance(live_checks, (str, bytes)),
                 "CLASS_A_LIVE_CHECKS_MISSING")
    for name, app_id in required_checks.items():
        check = _exact_keys(checks.get(name), {"id", "app_id", "head_sha", "status", "conclusion"},
                            "REQUIRED_CHECK")
        _positive_int(check.get("id"), "CHECK_ID")
        _require(check.get("app_id") == app_id, f"REQUIRED_CHECK_APP_MISMATCH:{name}")
        _require(check.get("head_sha") == expected_head_sha, f"REQUIRED_CHECK_HEAD_MISMATCH:{name}")
        _require(check.get("status") == "completed" and check.get("conclusion") == "success",
                 f"REQUIRED_CHECK_NOT_GREEN:{name}")
        if schema_version == 4:
            live = _latest(live_checks, name=name, expected_sha=expected_head_sha, app_id=app_id)
            _require(live is not None, f"CLASS_A_LIVE_CHECK_MISSING_OR_FOREIGN:{name}")
            _require(live.get("status") == "completed" and live.get("conclusion") == "success",
                     f"CLASS_A_LIVE_CHECK_NOT_GREEN:{name}")
            _require(check.get("id") == live.get("id"), f"CLASS_A_LIVE_CHECK_REPLACED:{name}")
            if name in DETERMINISTIC_CHECKS:
                _deterministic_check(live, name=name)
    _require(receipt.get("evidence_state") == ("CLASS_A_DETERMINISTIC_VERIFIED" if schema_version == 4 else "REVIEW_EVIDENCE_VERIFIED"),
             "REVIEW_EVIDENCE_NOT_VERIFIED")
    _require(receipt.get("terminal_authority") == TERMINAL_AUTHORITY, "TERMINAL_AUTHORITY_MISMATCH")
    _require(receipt.get("can_execute") is False, "CAN_EXECUTE_INVARIANT_VIOLATED")


def _path_is_trust_root(path: str, protected: set[str]) -> bool:
    return path.startswith(TRUST_ROOT_PREFIXES) or path in protected


def classify_changed_files(files: object, *, repo_root: Path, expected_count: object) -> str:
    normalized = _validate_changed_files(files, expected_count)
    protected = _protected_paths(repo_root)
    trust = False
    for item in normalized:
        for path in (item["filename"], item.get("previous_filename")):
            if path is not None and _path_is_trust_root(path, protected):
                trust = True
    return BOOTSTRAP_MODE if trust else ORDINARY_MODE


def _owner_approval(reviews: Sequence[Mapping[str, Any]], *, head_sha: str) -> dict[str, Any]:
    latest: tuple[datetime, int, Mapping[str, Any]] | None = None
    for review in reviews:
        _require(isinstance(review, Mapping), "REVIEW_INVENTORY_INVALID")
        user = review.get("user")
        _require(isinstance(user, Mapping), "REVIEWER_IDENTITY_UNKNOWN")
        if user.get("id") != OWNER_ID:
            continue
        reviewer_login = _login(user.get("login"), "REVIEWER_IDENTITY_UNKNOWN")
        _require(reviewer_login.casefold() == OWNER_LOGIN.casefold(), "REVIEWER_IDENTITY_UNKNOWN")
        _require(user.get("type") == "User", "TRUST_ROOT_OWNER_REVIEW_NOT_HUMAN")
        submitted = _timestamp(review.get("submitted_at"), "REVIEW_SUBMITTED_AT")
        review_id = _positive_int(review.get("id"), "REVIEW_ID")
        _require(isinstance(review.get("state"), str), "REVIEW_STATE_INVALID")
        if latest is None or (submitted, review_id) > latest[:2]:
            latest = (submitted, review_id, review)
    _require(latest is not None, "TRUST_ROOT_OWNER_APPROVAL_MISSING")
    assert latest is not None
    submitted, review_id, review = latest
    _require(
        review.get("state") == "APPROVED" and review.get("commit_id") == head_sha,
        "TRUST_ROOT_OWNER_APPROVAL_MISSING",
    )
    return {
        "state": "APPROVED",
        "reviewer": {"id": OWNER_ID, "login": OWNER_LOGIN, "type": "User"},
        "review_id": review_id,
        "commit_id": head_sha,
        "submitted_at": submitted.isoformat().replace("+00:00", "Z"),
    }


def _verified_named_check(
    check_runs: Sequence[Mapping[str, Any]], *, name: str, app_id: int, head_sha: str,
) -> dict[str, Any]:
    check = _latest(check_runs, name=name, expected_sha=head_sha, app_id=app_id)
    _require(check is not None, f"REQUIRED_CHECK_MISSING:{name}")
    assert check is not None
    _require(
        check.get("status") == "completed" and check.get("conclusion") == "success",
        f"REQUIRED_CHECK_NOT_GREEN:{name}",
    )
    return {
        "id": _positive_int(check.get("id"), "CHECK_ID"),
        "app_id": app_id,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "success",
    }


def build_trust_root_bootstrap(
    evidence: Mapping[str, Any],
    *,
    expected_repository: str,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_pr_number: int,
    expected_incident_id: str,
    expected_workflow_run_id: str,
    expected_workflow_run_attempt: int,
    repo_root: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "CLOCK_NOT_TRUSTED")
    clock = clock.astimezone(timezone.utc)
    _require(expected_repository == CANONICAL_REPOSITORY, "REPOSITORY_IDENTITY_MISMATCH")
    _require(bool(SHA.fullmatch(expected_head_sha)), "EXPECTED_SHA_INVALID")
    _require(bool(expected_head_ref), "EXPECTED_HEAD_REF_MISSING")
    _positive_int(expected_pr_number, "EXPECTED_PR_NUMBER")
    _positive_int(expected_workflow_run_attempt, "WORKFLOW_RUN_ATTEMPT")
    _require(bool(re.fullmatch(r"[1-9][0-9]*", str(expected_workflow_run_id))), "WORKFLOW_RUN_ID_INVALID")
    _require(bool(re.fullmatch(r"[1-9][0-9]*", expected_incident_id)), "INCIDENT_ID_INVALID")
    _require(evidence.get("complete_inventory") is True, "REVIEW_OR_CI_INVENTORY_INCOMPLETE")
    pr = evidence.get("pr")
    _require(isinstance(pr, Mapping), "PR_EVIDENCE_INVALID")
    changed = _validate_changed_files(evidence.get("changed_files"), pr.get("changed_files"))
    _require(
        classify_changed_files(changed, repo_root=repo_root, expected_count=len(changed)) == BOOTSTRAP_MODE,
        "TRUST_ROOT_REQUIRED",
    )
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    _require(isinstance(head, Mapping) and isinstance(base, Mapping), "PR_EVIDENCE_INVALID")
    _require(isinstance(pr.get("user"), Mapping), "PR_AUTHOR_IDENTITY_UNKNOWN")
    _require(isinstance(head.get("repo"), Mapping) and isinstance(base.get("repo"), Mapping),
             "PR_REPOSITORY_IDENTITY_UNKNOWN")
    author = pr.get("user") or {}
    author_login = _login(author.get("login"), "PR_AUTHOR_IDENTITY_UNKNOWN")
    author_id = _positive_int(author.get("id"), "PR_AUTHOR_IDENTITY_UNKNOWN")
    _require(
        author_id != OWNER_ID and author_login.casefold() != OWNER_LOGIN.casefold(),
        "TRUST_ROOT_OWNER_AUTHORED",
    )
    _require(
        pr.get("number") == expected_pr_number
        and pr.get("state") == "open"
        and pr.get("draft") is False
        and not pr.get("merged_at")
        and base.get("ref") == "main"
        and base["repo"].get("full_name") == expected_repository
        and head["repo"].get("full_name") == expected_repository
        and head.get("sha") == expected_head_sha
        and head.get("ref") == expected_head_ref,
        "PR_SHA_REPOSITORY_STATE_OR_BASE_MISMATCH",
    )
    reviews = evidence.get("reviews")
    check_runs = evidence.get("check_runs")
    _require(isinstance(reviews, list), "REVIEW_INVENTORY_INVALID")
    _require(isinstance(check_runs, list), "CHECK_INVENTORY_INVALID")
    owner_review = _owner_approval(reviews, head_sha=expected_head_sha)
    verified = {
        name: _verified_named_check(check_runs, name=name, app_id=app_id, head_sha=expected_head_sha)
        for name, app_id in BOOTSTRAP_CHECKS.items()
    }
    return {
        "receipt_schema_version": 2,
        "workflow_name": WORKFLOW_NAME,
        "repository": expected_repository,
        "workflow_run_id": str(expected_workflow_run_id),
        "workflow_run_attempt": expected_workflow_run_attempt,
        "created_at_utc": clock.isoformat().replace("+00:00", "Z"),
        "certification_mode": BOOTSTRAP_MODE,
        "fallback_expires_utc": (clock + REVIEW_RECEIPT_MAX_AGE).isoformat().replace("+00:00", "Z"),
        "target": {
            "pr_number": expected_pr_number,
            "incident_id": expected_incident_id,
            "author": {"login": author_login, "id": author_id},
            "base_ref": "main",
            "head_ref": expected_head_ref,
            "head_sha": expected_head_sha,
            "head_repository": expected_repository,
        },
        "changed_files": changed,
        "owner_review": owner_review,
        "required_checks": verified,
        "evidence_state": "TRUST_ROOT_BOOTSTRAP_VERIFIED",
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def verify_trust_root_bootstrap(
    receipt: Mapping[str, Any],
    *,
    expected_repository: str,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_workflow_run_id: str,
    expected_workflow_name: str,
    expected_pr_number: int,
    live_reviews: Sequence[Mapping[str, Any]],
    live_checks: Sequence[Mapping[str, Any]],
    repo_root: Path,
    now: datetime | None = None,
) -> None:
    keys = {
        "receipt_schema_version", "workflow_name", "repository", "workflow_run_id",
        "workflow_run_attempt", "created_at_utc", "certification_mode",
        "fallback_expires_utc", "target", "changed_files", "owner_review",
        "required_checks", "evidence_state", "terminal_authority", "can_execute",
    }
    _exact_keys(receipt, keys, "RECEIPT")
    _require(receipt.get("receipt_schema_version") == 2, "RECEIPT_SCHEMA_VERSION_UNSUPPORTED")
    _require(receipt.get("certification_mode") == BOOTSTRAP_MODE, "CERTIFICATION_MODE_MISMATCH")
    _require(receipt.get("evidence_state") == "TRUST_ROOT_BOOTSTRAP_VERIFIED",
             "TRUST_ROOT_BOOTSTRAP_NOT_VERIFIED")
    _require("independent_review" not in receipt and "trusted_reviewers" not in receipt,
             "BOOTSTRAP_MUST_NOT_CLAIM_INDEPENDENT_REVIEW")
    _require(expected_workflow_name == WORKFLOW_NAME and receipt.get("workflow_name") == WORKFLOW_NAME,
             "RECEIPT_WORKFLOW_IDENTITY_MISMATCH")
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
    _require(expiry == created + REVIEW_RECEIPT_MAX_AGE, "FALLBACK_EXPIRY_MISMATCH")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "CLOCK_NOT_TRUSTED")
    clock = clock.astimezone(timezone.utc)
    _require(created <= clock < expiry, "TEMPORARY_FALLBACK_EXPIRED_OR_FUTURE_RECEIPT")
    target = _exact_keys(
        receipt.get("target"),
        {"pr_number", "incident_id", "author", "base_ref", "head_ref", "head_sha", "head_repository"},
        "TARGET",
    )
    _require(_positive_int(target.get("pr_number"), "PR_NUMBER") == expected_pr_number,
             "RECEIPT_PR_NUMBER_MISMATCH")
    author = _exact_keys(target.get("author"), {"login", "id"}, "PR_AUTHOR")
    author_id = _positive_int(author.get("id"), "PR_AUTHOR_ID")
    author_login = _login(author.get("login"), "PR_AUTHOR_LOGIN")
    _require(author_id != OWNER_ID and author_login.casefold() != OWNER_LOGIN.casefold(),
             "TRUST_ROOT_OWNER_AUTHORED")
    _require(
        target.get("base_ref") == "main"
        and target.get("head_ref") == expected_head_ref
        and target.get("head_sha") == expected_head_sha
        and target.get("head_repository") == expected_repository,
        "RECEIPT_TARGET_MISMATCH",
    )
    changed = receipt.get("changed_files")
    _require(isinstance(changed, list), "DIFF_INVENTORY_INVALID")
    _require(
        classify_changed_files(changed, repo_root=repo_root, expected_count=len(changed)) == BOOTSTRAP_MODE,
        "TRUST_ROOT_REQUIRED",
    )
    _require(isinstance(live_reviews, Sequence) and not isinstance(live_reviews, (str, bytes)),
             "LIVE_REVIEW_INVENTORY_INVALID")
    _require(isinstance(live_checks, Sequence) and not isinstance(live_checks, (str, bytes)),
             "LIVE_CHECK_INVENTORY_INVALID")
    live_owner = _owner_approval(live_reviews, head_sha=expected_head_sha)
    recorded = _exact_keys(
        receipt.get("owner_review"),
        {"state", "reviewer", "review_id", "commit_id", "submitted_at"},
        "OWNER_REVIEW",
    )
    _require(
        recorded.get("review_id") == live_owner["review_id"]
        and recorded.get("commit_id") == expected_head_sha
        and recorded.get("state") == "APPROVED",
        "TRUST_ROOT_OWNER_APPROVAL_WITHDRAWN_OR_CHANGED",
    )
    checks = receipt.get("required_checks")
    _exact_keys(checks, set(BOOTSTRAP_CHECKS), "REQUIRED_CHECKS")
    for name, app_id in BOOTSTRAP_CHECKS.items():
        recorded_check = _exact_keys(
            checks.get(name), {"id", "app_id", "head_sha", "status", "conclusion"}, "REQUIRED_CHECK",
        )
        live_check = _verified_named_check(live_checks, name=name, app_id=app_id, head_sha=expected_head_sha)
        _require(recorded_check.get("id") == live_check["id"], f"REQUIRED_CHECK_SUPERSEDED:{name}")
        _require(recorded_check.get("app_id") == app_id, f"REQUIRED_CHECK_APP_MISMATCH:{name}")
    _require(receipt.get("terminal_authority") == TERMINAL_AUTHORITY, "TERMINAL_AUTHORITY_MISMATCH")
    _require(receipt.get("can_execute") is False, "CAN_EXECUTE_INVARIANT_VIOLATED")


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
    parser.add_argument("--live-reviews-json", type=Path)
    parser.add_argument("--live-checks-json", type=Path)
    parser.add_argument("--source-review-json", type=Path)
    parser.add_argument("--source-review-run-json", type=Path)
    parser.add_argument("--source-review-artifact-id", type=int)
    parser.add_argument("--incident-id")
    parser.add_argument("--workflow-run-attempt", type=int)
    parser.add_argument("--classify", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--deterministic-class-a", action="store_true")
    args = parser.parse_args(argv)

    def load_json(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        if args.classify:
            _require(args.evidence is not None, "CLASSIFY_EVIDENCE_REQUIRED")
            _require(not args.bootstrap, "CLASSIFY_BOOTSTRAP_CONFLICT")
            payload = load_json(args.evidence)
            pr = payload.get("pr")
            _require(isinstance(pr, Mapping), "PR_EVIDENCE_INVALID")
            print(classify_changed_files(
                payload.get("changed_files"),
                repo_root=Path.cwd(),
                expected_count=pr.get("changed_files"),
            ))
            return 0
        _require(not (args.bootstrap and args.deterministic_class_a),
                 "CLASS_A_BOOTSTRAP_MODE_CONFLICT")
        if args.bootstrap and args.evidence is None:
            raise ExistingPrGovernanceReceiptError("BOOTSTRAP_REQUIRES_EVIDENCE")
        if args.evidence:
            _require(args.output is not None, "RECEIPT_OUTPUT_MISSING")
            _require(args.pr_number is not None, "EXPECTED_PR_NUMBER_MISSING")
            _require(bool(args.incident_id), "EXPECTED_INCIDENT_ID_MISSING")
            _require(args.workflow_run_attempt is not None, "WORKFLOW_RUN_ATTEMPT_MISSING")
            payload = load_json(args.evidence)
            if args.bootstrap:
                _require(args.trusted_reviewers_json is None, "BOOTSTRAP_MUST_NOT_USE_ALLOWLIST")
                _require(args.source_review_json is None, "BOOTSTRAP_MUST_NOT_USE_SOURCE_REVIEW")
                receipt = build_trust_root_bootstrap(
                    payload,
                    expected_repository=args.repository,
                    expected_head_sha=args.head_sha,
                    expected_head_ref=args.head_ref,
                    expected_pr_number=args.pr_number,
                    expected_incident_id=args.incident_id,
                    expected_workflow_run_id=args.workflow_run_id,
                    expected_workflow_run_attempt=args.workflow_run_attempt,
                    repo_root=Path.cwd(),
                )
                verify_trust_root_bootstrap(
                    receipt,
                    expected_repository=args.repository,
                    expected_head_sha=args.head_sha,
                    expected_head_ref=args.head_ref,
                    expected_workflow_run_id=args.workflow_run_id,
                    expected_workflow_name=args.workflow_name,
                    expected_pr_number=args.pr_number,
                    live_reviews=payload["reviews"],
                    live_checks=payload["check_runs"],
                    repo_root=Path.cwd(),
                )
                verified_status = "TRUST_ROOT_BOOTSTRAP_VERIFIED"
            else:
                if args.deterministic_class_a:
                    _require(args.trusted_reviewers_json is None and args.source_review_json is None
                             and args.source_review_run_json is None and args.source_review_artifact_id is None,
                             "CLASS_A_REVIEW_MODE_CONFLICT")
                    receipt = build_receipt(
                        payload,
                        expected_repository=args.repository,
                        expected_head_sha=args.head_sha,
                        expected_head_ref=args.head_ref,
                        expected_pr_number=args.pr_number,
                        expected_incident_id=args.incident_id,
                        expected_workflow_run_id=args.workflow_run_id,
                        expected_workflow_run_attempt=args.workflow_run_attempt,
                        deterministic_class_a=True,
                    )
                elif args.source_review_json is not None:
                    _require(args.trusted_reviewers_json is None,
                             "SOURCE_REVIEW_AND_HUMAN_ALLOWLIST_CONFLICT")
                    _require(args.source_review_run_json is not None,
                             "SOURCE_REVIEW_RUN_METADATA_MISSING")
                    _require(args.source_review_run_json.is_file(),
                             "SOURCE_REVIEW_RUN_METADATA_MISSING")
                    _require(args.source_review_artifact_id is not None,
                             "SOURCE_REVIEW_ARTIFACT_ID_MISSING")
                    source_review = load_json(args.source_review_json)
                    source_run = load_json(args.source_review_run_json)
                    receipt = build_receipt(
                        payload,
                        expected_repository=args.repository,
                        expected_head_sha=args.head_sha,
                        expected_head_ref=args.head_ref,
                        expected_pr_number=args.pr_number,
                        expected_incident_id=args.incident_id,
                        expected_workflow_run_id=args.workflow_run_id,
                        expected_workflow_run_attempt=args.workflow_run_attempt,
                        automated_review=source_review,
                        source_review_run=source_run,
                        source_review_artifact_id=args.source_review_artifact_id,
                    )
                else:
                    _require(args.trusted_reviewers_json is not None,
                             "INDEPENDENT_REVIEWER_ALLOWLIST_MISSING")
                    try:
                        allowlist = json.loads(args.trusted_reviewers_json)
                    except json.JSONDecodeError as exc:
                        raise ExistingPrGovernanceReceiptError(
                            "INDEPENDENT_REVIEWER_ALLOWLIST_INVALID"
                        ) from exc
                    receipt = build_receipt(
                        payload,
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
                    live_reviews=payload["reviews"],
                    source_review_run=(
                        load_json(args.source_review_run_json)
                        if args.source_review_run_json is not None else None
                    ),
                    live_checks=payload["check_runs"] if args.deterministic_class_a else None,
                )
                verified_status = "CLASS_A_DETERMINISTIC_VERIFIED" if args.deterministic_class_a else "REVIEW_EVIDENCE_VERIFIED"
            args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            receipt = load_json(args.receipt)
            _require(args.pr_number is not None, "TRUSTED_PR_NUMBER_REQUIRED")
            _require(args.live_reviews_json is not None, "LIVE_REVIEW_RECHECK_REQUIRED")
            live_reviews = load_json(args.live_reviews_json)
            if isinstance(receipt, Mapping) and receipt.get("certification_mode") == BOOTSTRAP_MODE:
                _require(args.live_checks_json is not None, "LIVE_CHECK_RECHECK_REQUIRED")
                verify_trust_root_bootstrap(
                    receipt,
                    expected_repository=args.repository,
                    expected_head_sha=args.head_sha,
                    expected_head_ref=args.head_ref,
                    expected_workflow_run_id=args.workflow_run_id,
                    expected_workflow_name=args.workflow_name,
                    expected_pr_number=args.pr_number,
                    live_reviews=live_reviews,
                    live_checks=load_json(args.live_checks_json),
                    repo_root=Path.cwd(),
                )
                verified_status = "TRUST_ROOT_BOOTSTRAP_VERIFIED"
            else:
                source_run = None
                if isinstance(receipt, Mapping) and receipt.get("receipt_schema_version") == 3:
                    if args.source_review_run_json is not None:
                        _require(args.source_review_run_json.is_file(),
                                 "SOURCE_REVIEW_RUN_METADATA_MISSING")
                        source_run = load_json(args.source_review_run_json)
                    else:
                        try:
                            source_run = _source_review_module.resolve_source_review_run(
                                receipt["independent_review"]["artifact"],
                                artifact_id=receipt["independent_review"]["artifact_id"],
                                repository=args.repository,
                                token=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""),
                            )
                        except _source_review_module.SourceReviewError as exc:
                            raise ExistingPrGovernanceReceiptError(str(exc)) from exc
                verify_receipt(
                    receipt,
                    expected_repository=args.repository,
                    expected_head_sha=args.head_sha,
                    expected_head_ref=args.head_ref,
                    expected_workflow_run_id=args.workflow_run_id,
                    expected_workflow_name=args.workflow_name,
                    expected_pr_number=args.pr_number,
                    live_reviews=live_reviews,
                    source_review_run=source_run,
                    live_checks=(load_json(args.live_checks_json) if args.live_checks_json is not None else None),
                )
                verified_status = ("CLASS_A_DETERMINISTIC_VERIFIED" if receipt.get("receipt_schema_version") == 4
                                   else "REVIEW_EVIDENCE_VERIFIED")
    except ExistingPrGovernanceReceiptError as exc:
        print(json.dumps({"status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 1
    except (OSError, json.JSONDecodeError, KeyError):
        print(json.dumps({"status": "HOLD", "reason": "EVIDENCE_OR_RECEIPT_IO_INVALID"}, sort_keys=True))
        return 1

    print(json.dumps({"status": verified_status, "head_sha": args.head_sha,
                      "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
