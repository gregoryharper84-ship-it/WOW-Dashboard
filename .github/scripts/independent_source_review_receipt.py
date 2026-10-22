#!/usr/bin/env python3
"""Create and verify exact-head OpenAI source-review evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from zipfile import BadZipFile, ZipFile

CANONICAL_REPOSITORY = "gregoryharper84-ship-it/WOW-Dashboard"
WORKFLOW_NAME = "wow-v17-independent-qa-check"
WORKFLOW_PATH = ".github/workflows/wow-v17-independent-qa-check.yml"
PROVIDER_IDENTITY = "openai-codex-action"
CREDENTIAL_SCOPE = "WOW_QA_REVIEW_OPENAI_API_KEY"
MAX_REVIEW_AGE = timedelta(minutes=30)
MAX_DIFF_FILES = 100
MAX_DIFF_BYTES = 512 * 1024
MAX_DIFF_LINES = 100_000
MAX_REVIEW_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_INVENTORY = 2000
QA_POLICY_ARTIFACT_NAME = "wow-v17-qa-review-policy.json"
SHA = re.compile(r"^[a-f0-9]{40}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")


class SourceReviewError(ValueError):
    """Raised when independent exact-head review evidence is incomplete."""


def verify_unique_artifact_inventory(
    records: object,
    *,
    prefix: str,
    expected_artifact_id: int,
    now: datetime | None = None,
) -> None:
    _require(isinstance(records, list) and len(records) <= MAX_ARTIFACT_INVENTORY,
             "SOURCE_REVIEW_ARTIFACT_INVENTORY_INVALID")
    _require(isinstance(prefix, str) and prefix.startswith("wow-v17-independent-source-review-"),
             "SOURCE_REVIEW_ARTIFACT_PREFIX_INVALID")
    expected_artifact_id = _positive_int(expected_artifact_id, "SOURCE_REVIEW_ARTIFACT_ID")
    matching = []
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "SOURCE_REVIEW_CLOCK_INVALID")
    clock = clock.astimezone(timezone.utc)
    for record in records:
        _require(isinstance(record, Mapping), "SOURCE_REVIEW_ARTIFACT_INVENTORY_INVALID")
        name = record.get("name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        _require(isinstance(record.get("expired"), bool), "SOURCE_REVIEW_ARTIFACT_EXPIRY_UNKNOWN")
        _positive_int(record.get("id"), "SOURCE_REVIEW_ARTIFACT_ID")
        if not record["expired"]:
            created = _timestamp(record.get("created_at"), "SOURCE_REVIEW_ARTIFACT_CREATED_AT")
            _require(created <= clock, "SOURCE_REVIEW_ARTIFACT_TIME_INVALID")
            if clock < created + MAX_REVIEW_AGE:
                matching.append(record)
    _require(len(matching) == 1, "SOURCE_REVIEW_ARTIFACT_DUPLICATE_OR_MISSING")
    _require(matching[0]["id"] == expected_artifact_id, "SOURCE_REVIEW_ARTIFACT_REPLAYED")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SourceReviewError(reason)


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
        raise SourceReviewError(f"{name}_INVALID") from exc
    _require(parsed.tzinfo is not None, f"{name}_TIMEZONE_MISSING")
    return parsed.astimezone(timezone.utc)


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _github_get_json(*, repository: str, path: str, token: str) -> Any:
    _require(repository == CANONICAL_REPOSITORY, "SOURCE_REVIEW_REPOSITORY_MISMATCH")
    request = Request(
        f"https://api.github.com/repos/{repository}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "token " + token,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read(MAX_DIFF_BYTES * 4 + 1)
            _require(len(body) <= MAX_DIFF_BYTES * 4, "SOURCE_REVIEW_API_RESPONSE_LIMIT_EXCEEDED")
            return json.loads(body)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        raise SourceReviewError("SOURCE_REVIEW_GITHUB_API_UNAVAILABLE") from exc


def _github_download_artifact(*, repository: str, artifact_id: int, token: str) -> bytes:
    _require(repository == CANONICAL_REPOSITORY, "SOURCE_REVIEW_REPOSITORY_MISMATCH")
    artifact_id = _positive_int(artifact_id, "SOURCE_REVIEW_ARTIFACT_ID")
    _require(bool(token), "SOURCE_REVIEW_GITHUB_TOKEN_MISSING")
    request = Request(
        f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "token " + token,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(MAX_REVIEW_ARTIFACT_BYTES + 1)
    except HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise SourceReviewError("SOURCE_REVIEW_ARTIFACT_DOWNLOAD_FAILED") from exc
        location = exc.headers.get("Location")
        parsed = urlparse(location or "")
        host = (parsed.hostname or "").lower()
        allowed_host = (
            host.endswith(".blob.core.windows.net")
            or host == "github.com"
            or host.endswith(".githubusercontent.com")
        )
        _require(parsed.scheme == "https" and allowed_host, "SOURCE_REVIEW_ARTIFACT_REDIRECT_UNTRUSTED")
        try:
            with urlopen(Request(location, headers={"User-Agent": "wow-v17-source-review"}), timeout=30) as response:
                body = response.read(MAX_REVIEW_ARTIFACT_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as download_error:
            raise SourceReviewError("SOURCE_REVIEW_ARTIFACT_DOWNLOAD_FAILED") from download_error
    except (URLError, TimeoutError, OSError) as exc:
        raise SourceReviewError("SOURCE_REVIEW_ARTIFACT_DOWNLOAD_FAILED") from exc
    _require(len(body) <= MAX_REVIEW_ARTIFACT_BYTES, "SOURCE_REVIEW_ARTIFACT_LIMIT_EXCEEDED")
    return body


def resolve_current_source_review(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    head_ref: str,
    token: str,
    expected_file_paths: list[str] | None = None,
    expected_workflow_run_id: int | None = None,
    expected_workflow_run_attempt: int | None = None,
    now: datetime | None = None,
    get_json: Any = None,
    download_artifact: Any = None,
) -> dict[str, Any]:
    _require(repository == CANONICAL_REPOSITORY, "SOURCE_REVIEW_REPOSITORY_MISMATCH")
    _positive_int(pr_number, "SOURCE_REVIEW_PR_NUMBER")
    _require(bool(SHA.fullmatch(head_sha)), "SOURCE_REVIEW_HEAD_INVALID")
    _require(isinstance(head_ref, str) and bool(head_ref), "SOURCE_REVIEW_HEAD_REF_INVALID")
    _require(bool(token), "SOURCE_REVIEW_GITHUB_TOKEN_MISSING")
    json_fetch = get_json or (lambda path: _github_get_json(repository=repository, path=path, token=token))
    artifact_fetch = download_artifact or (
        lambda artifact_id: _github_download_artifact(
            repository=repository, artifact_id=artifact_id, token=token
        )
    )

    prefix = f"wow-v17-independent-source-review-{pr_number}-{head_sha}-"
    records: list[dict[str, Any]] = []
    for page in range(1, 21):
        response = json_fetch(f"/actions/artifacts?per_page=100&page={page}")
        _require(isinstance(response, Mapping) and isinstance(response.get("artifacts"), list),
                 "SOURCE_REVIEW_ARTIFACT_INVENTORY_INVALID")
        page_records = response["artifacts"]
        records.extend(page_records)
        _require(len(records) <= MAX_ARTIFACT_INVENTORY, "SOURCE_REVIEW_ARTIFACT_INVENTORY_LIMIT_EXCEEDED")
        if len(page_records) < 100:
            break
    else:
        raise SourceReviewError("SOURCE_REVIEW_ARTIFACT_INVENTORY_LIMIT_EXCEEDED")

    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "SOURCE_REVIEW_CLOCK_INVALID")
    matching = []
    for record in records:
        _require(isinstance(record, Mapping), "SOURCE_REVIEW_ARTIFACT_INVENTORY_INVALID")
        if not isinstance(record.get("name"), str) or not record["name"].startswith(prefix):
            continue
        _require(isinstance(record.get("expired"), bool), "SOURCE_REVIEW_ARTIFACT_EXPIRY_UNKNOWN")
        if not record["expired"]:
            created = _timestamp(record.get("created_at"), "SOURCE_REVIEW_ARTIFACT_CREATED_AT")
            if created <= clock.astimezone(timezone.utc) < created + MAX_REVIEW_AGE:
                matching.append(record)
    if expected_workflow_run_id is not None:
        expected_workflow_run_id = _positive_int(
            expected_workflow_run_id, "SOURCE_REVIEW_RUN_ID"
        )
        expected_workflow_run_attempt = _positive_int(
            expected_workflow_run_attempt, "SOURCE_REVIEW_RUN_ATTEMPT"
        )
        suffix = f"-{expected_workflow_run_id}-{expected_workflow_run_attempt}"
        matching = [record for record in matching if str(record.get("name", "")).endswith(suffix)]
    else:
        matching.sort(key=lambda record: (_timestamp(record.get("created_at"), "SOURCE_REVIEW_ARTIFACT_CREATED_AT"),
                                          int(record.get("id") or 0)), reverse=True)
        if matching:
            latest_name = matching[0].get("name")
            name_match = re.fullmatch(
                re.escape(prefix) + r"([1-9][0-9]*)-([1-9][0-9]*)", str(latest_name)
            )
            _require(name_match is not None, "SOURCE_REVIEW_ARTIFACT_NAME_INVALID")
            suffix = f"-{name_match.group(1)}-{name_match.group(2)}"
            matching = [record for record in matching if str(record.get("name", "")).endswith(suffix)]
    _require(len(matching) == 1, "SOURCE_REVIEW_ARTIFACT_DUPLICATE_OR_MISSING")
    artifact_meta = matching[0]
    artifact_id = _positive_int(artifact_meta.get("id"), "SOURCE_REVIEW_ARTIFACT_ID")
    artifact_name = artifact_meta.get("name")
    name_match = re.fullmatch(
        re.escape(prefix) + r"([1-9][0-9]*)-([1-9][0-9]*)", str(artifact_name)
    )
    _require(name_match is not None, "SOURCE_REVIEW_ARTIFACT_NAME_INVALID")
    run_id, attempt = name_match.groups()

    verify_unique_artifact_inventory(
        [record for record in records if str(record.get("name", "")).startswith(prefix)
         and str(record.get("name", "")).endswith(f"-{run_id}-{attempt}")],
        prefix=prefix,
        expected_artifact_id=artifact_id,
        now=clock,
    )
    actual_meta = json_fetch(f"/actions/artifacts/{artifact_id}")
    _require(
        isinstance(actual_meta, Mapping)
        and actual_meta.get("id") == artifact_id
        and actual_meta.get("expired") is False
        and actual_meta.get("name") == artifact_name
        and (actual_meta.get("workflow_run") or {}).get("id") == int(run_id),
        "SOURCE_REVIEW_ARTIFACT_PROVENANCE_MISMATCH",
    )
    archive = artifact_fetch(artifact_id)
    try:
        with ZipFile(io.BytesIO(archive)) as zip_file:
            names = zip_file.namelist()
            expected_names = [
                "wow-v17-independent-source-review.json",
                "wow-v17-independent-source-review.json.sha256",
            ]
            _require(sorted(names) == sorted(expected_names), "SOURCE_REVIEW_ARTIFACT_CONTENT_INVALID")
            infos = {info.filename: info for info in zip_file.infolist()}
            _require(
                all(infos[name].file_size <= MAX_REVIEW_ARTIFACT_BYTES for name in expected_names),
                "SOURCE_REVIEW_ARTIFACT_LIMIT_EXCEEDED",
            )
            artifact_bytes = zip_file.read(expected_names[0])
            checksum = zip_file.read(expected_names[1]).decode("ascii").strip()
    except (BadZipFile, KeyError, UnicodeDecodeError, OSError) as exc:
        raise SourceReviewError("SOURCE_REVIEW_ARTIFACT_CONTENT_INVALID") from exc
    _require(
        bool(DIGEST.fullmatch(checksum)) and hashlib.sha256(artifact_bytes).hexdigest() == checksum,
        "SOURCE_REVIEW_ARTIFACT_CHECKSUM_MISMATCH",
    )
    try:
        artifact = json.loads(artifact_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceReviewError("SOURCE_REVIEW_ARTIFACT_CONTENT_INVALID") from exc
    if expected_file_paths is not None:
        diff = artifact.get("diff") if isinstance(artifact, Mapping) else None
        paths = diff.get("file_paths") if isinstance(diff, Mapping) else None
        _require(
            paths == sorted(set(expected_file_paths)),
            "SOURCE_REVIEW_DIFF_INVENTORY_MISMATCH",
        )
    run = resolve_source_review_run(
        artifact,
        artifact_id=artifact_id,
        repository=repository,
        token=token,
        now=clock,
        get_json=json_fetch,
    )
    return {"artifact_id": artifact_id, "artifact": artifact, "run": run}


def _commit_metadata(value: object, *, expected_head_sha: str) -> dict[str, Any]:
    _require(isinstance(value, Mapping), "SOURCE_REVIEW_COMMIT_METADATA_INVALID")
    normalized_keys = {
        "sha", "tree_sha", "parent_shas", "message", "author_login", "committer_login",
    }
    if set(value) == normalized_keys:
        sha = value.get("sha")
        tree_sha = value.get("tree_sha")
        parent_shas = value.get("parent_shas")
        message = value.get("message")
        author_login = value.get("author_login")
        committer_login = value.get("committer_login")
        _require(sha == expected_head_sha and bool(SHA.fullmatch(str(sha))),
                 "SOURCE_REVIEW_COMMIT_SHA_MISMATCH")
        _require(isinstance(tree_sha, str) and bool(SHA.fullmatch(tree_sha)),
                 "SOURCE_REVIEW_COMMIT_TREE_INVALID")
        _require(isinstance(parent_shas, list), "SOURCE_REVIEW_COMMIT_PARENTS_INVALID")
        for parent_sha in parent_shas:
            _require(isinstance(parent_sha, str) and bool(SHA.fullmatch(parent_sha)),
                     "SOURCE_REVIEW_COMMIT_PARENTS_INVALID")
        _require(
            isinstance(message, str) and message.strip()
            and len(message.encode("utf-8")) <= 8192,
            "SOURCE_REVIEW_COMMIT_MESSAGE_INVALID",
        )
        for login in (author_login, committer_login):
            _require(login is None or isinstance(login, str), "SOURCE_REVIEW_COMMIT_IDENTITY_INVALID")
        return {
            "sha": sha,
            "tree_sha": tree_sha,
            "parent_shas": list(parent_shas),
            "message": message,
            "author_login": author_login,
            "committer_login": committer_login,
        }
    commit = value.get("commit")
    _require(isinstance(commit, Mapping), "SOURCE_REVIEW_COMMIT_METADATA_INVALID")
    tree = commit.get("tree") or {}
    parents = value.get("parents")
    _require(isinstance(tree, Mapping) and isinstance(parents, list),
             "SOURCE_REVIEW_COMMIT_METADATA_INVALID")
    sha = value.get("sha")
    tree_sha = tree.get("sha")
    _require(sha == expected_head_sha and bool(SHA.fullmatch(str(sha))),
             "SOURCE_REVIEW_COMMIT_SHA_MISMATCH")
    _require(isinstance(tree_sha, str) and bool(SHA.fullmatch(tree_sha)),
             "SOURCE_REVIEW_COMMIT_TREE_INVALID")
    parent_shas = []
    for parent in parents:
        _require(isinstance(parent, Mapping), "SOURCE_REVIEW_COMMIT_PARENTS_INVALID")
        parent_sha = parent.get("sha")
        _require(isinstance(parent_sha, str) and bool(SHA.fullmatch(parent_sha)),
                 "SOURCE_REVIEW_COMMIT_PARENTS_INVALID")
        parent_shas.append(parent_sha)
    message = commit.get("message")
    _require(
        isinstance(message, str) and message.strip()
        and len(message.encode("utf-8")) <= 8192,
        "SOURCE_REVIEW_COMMIT_MESSAGE_INVALID",
    )
    author = value.get("author") or {}
    committer = value.get("committer") or {}
    _require(isinstance(author, Mapping) and isinstance(committer, Mapping),
             "SOURCE_REVIEW_COMMIT_METADATA_INVALID")
    return {
        "sha": sha,
        "tree_sha": tree_sha,
        "parent_shas": parent_shas,
        "message": message,
        "author_login": author.get("login") if isinstance(author.get("login"), str) else None,
        "committer_login": committer.get("login") if isinstance(committer.get("login"), str) else None,
    }


def _validate_patch_counts(patch: str, *, additions: object, deletions: object) -> None:
    _require(
        isinstance(additions, int) and not isinstance(additions, bool) and additions >= 0
        and isinstance(deletions, int) and not isinstance(deletions, bool) and deletions >= 0
        and additions + deletions <= MAX_DIFF_LINES,
        "SOURCE_DIFF_LINE_COUNTS_INVALID",
    )
    lines = patch.splitlines()
    _require(bool(lines), "SOURCE_DIFF_PATCH_INCOMPLETE")
    hunk_re = re.compile(r"^@@ -([0-9]+)(?:,([0-9]+))? \+([0-9]+)(?:,([0-9]+))? @@(?: .*)?$")
    observed_additions = 0
    observed_deletions = 0
    total_additions = 0
    total_deletions = 0
    hunk_old_remaining = 0
    hunk_new_remaining = 0
    in_hunk = False

    for line in lines:
        match = hunk_re.fullmatch(line)
        if match:
            if in_hunk:
                _require(hunk_old_remaining == 0 and hunk_new_remaining == 0,
                         "SOURCE_DIFF_PATCH_HUNK_INCOMPLETE")
            hunk_old_remaining = int(match.group(2) or 1)
            hunk_new_remaining = int(match.group(4) or 1)
            total_deletions += hunk_old_remaining
            total_additions += hunk_new_remaining
            _require(total_additions + total_deletions <= MAX_DIFF_LINES,
                     "SOURCE_DIFF_LINE_COUNTS_INVALID")
            in_hunk = True
            continue
        _require(in_hunk, "SOURCE_DIFF_PATCH_HUNK_INVALID")
        if line.startswith("\\ "):
            continue
        _require(bool(line) and line[0] in {" ", "+", "-"}, "SOURCE_DIFF_PATCH_LINE_INVALID")
        if line[0] in {" ", "-"}:
            hunk_old_remaining -= 1
            _require(hunk_old_remaining >= 0, "SOURCE_DIFF_PATCH_HUNK_OVERFLOW")
        if line[0] in {" ", "+"}:
            hunk_new_remaining -= 1
            _require(hunk_new_remaining >= 0, "SOURCE_DIFF_PATCH_HUNK_OVERFLOW")
        if line[0] == "+":
            observed_additions += 1
        elif line[0] == "-":
            observed_deletions += 1
    _require(in_hunk and hunk_old_remaining == 0 and hunk_new_remaining == 0,
             "SOURCE_DIFF_PATCH_HUNK_INCOMPLETE")
    _require(
        observed_additions == additions and observed_deletions == deletions,
        "SOURCE_DIFF_PATCH_COUNTS_MISMATCH",
    )


def canonical_diff(files: object, *, expected_count: object) -> tuple[list[dict[str, Any]], str]:
    _require(isinstance(expected_count, int) and not isinstance(expected_count, bool),
             "SOURCE_DIFF_COUNT_INVALID")
    _require(0 < expected_count <= MAX_DIFF_FILES, "SOURCE_DIFF_LIMIT_EXCEEDED")
    _require(isinstance(files, list) and len(files) == expected_count, "SOURCE_DIFF_INCOMPLETE")
    normalized = []
    paths = set()
    total_bytes = 0
    for file in files:
        file = _exact_keys(
            file,
            {"filename", "previous_filename", "status", "patch", "additions", "deletions"},
            "SOURCE_DIFF_FILE",
        )
        filename = file.get("filename")
        previous = file.get("previous_filename")
        status = file.get("status")
        patch = file.get("patch")
        _require(isinstance(filename, str) and filename and "\x00" not in filename, "SOURCE_DIFF_PATH_INVALID")
        _require(previous is None or isinstance(previous, str), "SOURCE_DIFF_PATH_INVALID")
        _require(isinstance(status, str) and status, "SOURCE_DIFF_STATUS_INVALID")
        _require(isinstance(patch, str), "SOURCE_DIFF_PATCH_MISSING")
        _validate_patch_counts(
            patch, additions=file.get("additions"), deletions=file.get("deletions")
        )
        _require(filename not in paths, "SOURCE_DIFF_DUPLICATE_PATH")
        paths.add(filename)
        total_bytes += len(patch.encode("utf-8"))
        _require(total_bytes <= MAX_DIFF_BYTES, "SOURCE_DIFF_LIMIT_EXCEEDED")
        normalized.append({
            "filename": filename,
            "previous_filename": previous,
            "status": status,
            "patch": patch,
            "additions": file["additions"],
            "deletions": file["deletions"],
        })
    normalized.sort(key=lambda item: item["filename"])
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return normalized, hashlib.sha256(canonical).hexdigest()


def _validate_review_result(
    result: object, *, head_sha: str, diff_sha256: str, file_paths: list[str]
) -> dict[str, Any]:
    result = _exact_keys(
        result,
        {
            "decision", "summary", "reviewed_head_sha", "reviewed_diff_sha256",
            "reviewed_files", "assessment", "findings",
        },
        "MODEL_REVIEW",
    )
    _require(result.get("decision") in {"PASS", "REJECT"}, "MODEL_REVIEW_DECISION_INVALID")
    _require(
        isinstance(result.get("summary"), str)
        and result["summary"].strip()
        and len(result["summary"]) <= 4000,
        "MODEL_REVIEW_SUMMARY_MISSING",
    )
    _require(result.get("reviewed_head_sha") == head_sha, "MODEL_REVIEW_HEAD_MISMATCH")
    _require(result.get("reviewed_diff_sha256") == diff_sha256, "MODEL_REVIEW_DIFF_MISMATCH")
    reviewed_files = result.get("reviewed_files")
    _require(
        isinstance(reviewed_files, list)
        and all(isinstance(path, str) for path in reviewed_files)
        and sorted(reviewed_files) == file_paths
        and len(set(reviewed_files)) == len(file_paths),
        "MODEL_REVIEW_FILE_COVERAGE_INCOMPLETE",
    )
    assessment = _exact_keys(
        result.get("assessment"),
        {"source_review", "security_review", "probability_integrity_review"},
        "MODEL_REVIEW_ASSESSMENT",
    )
    _require(
        all(value in {"PASS", "FAIL"} for value in assessment.values()),
        "MODEL_REVIEW_ASSESSMENT_INVALID",
    )
    findings = result.get("findings")
    _require(isinstance(findings, list) and len(findings) <= 50, "MODEL_REVIEW_FINDINGS_INVALID")
    normalized_findings = []
    for finding in findings:
        finding = _exact_keys(finding, {"severity", "file", "detail", "evidence"}, "MODEL_REVIEW_FINDING")
        _require(finding.get("severity") in {"critical", "high", "medium", "low"},
                 "MODEL_REVIEW_FINDING_SEVERITY_INVALID")
        _require(finding.get("file") in file_paths, "MODEL_REVIEW_FINDING_FILE_UNKNOWN")
        _require(
            all(isinstance(finding.get(key), str) and finding[key].strip() for key in ("detail", "evidence")),
            "MODEL_REVIEW_FINDING_INCOMPLETE",
        )
        _require(
            all(len(finding[key]) <= 4000 for key in ("detail", "evidence")),
            "MODEL_REVIEW_FINDING_LIMIT_EXCEEDED",
        )
        normalized_findings.append(dict(finding))
    if any(finding["severity"] in {"critical", "high"} for finding in normalized_findings):
        _require(result.get("decision") == "REJECT", "MODEL_REVIEW_HIGH_FINDING_NOT_REJECTED")
    if result.get("decision") == "PASS":
        _require(all(value == "PASS" for value in assessment.values()),
                 "MODEL_REVIEW_ASSESSMENT_FAILED")
    else:
        _require(
            any(value == "FAIL" for value in assessment.values()) or bool(normalized_findings),
            "MODEL_REVIEW_REJECTION_EVIDENCE_MISSING",
        )
    return {
        "decision": result["decision"],
        "summary": result["summary"].strip(),
        "assessment": dict(assessment),
        "findings": normalized_findings,
        "reviewed_files": file_paths,
    }


def build_artifact(
    *,
    repository: str,
    pr: Mapping[str, Any],
    files: object,
    commit: object,
    review_result: object,
    run: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    _require(repository == CANONICAL_REPOSITORY, "SOURCE_REVIEW_REPOSITORY_MISMATCH")
    _require(pr.get("state") == "open" and pr.get("draft") is False and not pr.get("merged_at"),
             "SOURCE_REVIEW_PR_NOT_READY")
    _require(pr.get("number") == _positive_int(pr.get("number"), "SOURCE_REVIEW_PR_NUMBER"),
             "SOURCE_REVIEW_PR_NUMBER_INVALID")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    author = pr.get("user") or {}
    _require(isinstance(head, Mapping) and isinstance(base, Mapping) and isinstance(author, Mapping),
             "SOURCE_REVIEW_PR_IDENTITY_INVALID")
    head_sha = head.get("sha")
    head_ref = head.get("ref")
    _require(isinstance(head_sha, str) and bool(SHA.fullmatch(head_sha)), "SOURCE_REVIEW_HEAD_INVALID")
    _require(isinstance(head_ref, str) and bool(head_ref), "SOURCE_REVIEW_HEAD_REF_INVALID")
    _require(
        (head.get("repo") or {}).get("full_name") == repository
        and (base.get("repo") or {}).get("full_name") == repository
        and base.get("ref") == "main",
        "SOURCE_REVIEW_REPOSITORY_OR_BASE_MISMATCH",
    )
    author_id = _positive_int(author.get("id"), "SOURCE_REVIEW_AUTHOR_ID")
    author_login = author.get("login")
    _require(isinstance(author_login, str) and author_login.strip(), "SOURCE_REVIEW_AUTHOR_LOGIN_INVALID")
    changed_count = pr.get("changed_files")
    normalized_files, diff_sha256 = canonical_diff(files, expected_count=changed_count)
    commit_metadata = _commit_metadata(commit, expected_head_sha=head_sha)
    file_paths = [item["filename"] for item in normalized_files]
    review = _validate_review_result(
        review_result, head_sha=head_sha, diff_sha256=diff_sha256, file_paths=file_paths
    )

    run_id = _positive_int(run.get("id"), "SOURCE_REVIEW_RUN_ID")
    attempt = _positive_int(run.get("run_attempt"), "SOURCE_REVIEW_RUN_ATTEMPT")
    source_event = run.get("event")
    run_repository = run.get("repository")
    run_repository_name = (
        run_repository if isinstance(run_repository, str)
        else run_repository.get("full_name") if isinstance(run_repository, Mapping)
        else None
    )
    _require(
        run.get("name") == WORKFLOW_NAME
        and run.get("path") == WORKFLOW_PATH
        and source_event in {"workflow_dispatch", "workflow_run"}
        and run.get("head_branch") == "main"
        and run_repository_name == repository,
        "SOURCE_REVIEW_WORKFLOW_IDENTITY_MISMATCH",
    )
    source_head_sha = run.get("head_sha")
    _require(isinstance(source_head_sha, str) and bool(SHA.fullmatch(source_head_sha)),
             "SOURCE_REVIEW_WORKFLOW_HEAD_INVALID")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "SOURCE_REVIEW_CLOCK_INVALID")
    expires = clock.astimezone(timezone.utc) + MAX_REVIEW_AGE
    return {
        "schema_version": 1,
        "repository": repository,
        "target": {
            "pr_number": pr["number"],
            "author": {"id": author_id, "login": author_login},
            "base_ref": "main",
            "head_ref": head_ref,
            "head_sha": head_sha,
        },
        "diff": {
            "sha256": diff_sha256,
            "file_count": len(file_paths),
            "file_paths": file_paths,
        },
        "commit": commit_metadata,
        "review": review,
        "source": {
            "run_id": str(run_id),
            "run_attempt": attempt,
            "workflow_name": WORKFLOW_NAME,
            "workflow_path": WORKFLOW_PATH,
            "event": source_event,
            "head_branch": "main",
            "head_sha": source_head_sha,
            "provider_identity": PROVIDER_IDENTITY,
            "credential_scope": CREDENTIAL_SCOPE,
        },
        "created_at_utc": _utc(clock),
        "expires_at_utc": _utc(expires),
    }


def capture_pr_evidence(
    *, repository: str, pr_number: int, expected_head_sha: str, token: str
) -> dict[str, Any]:
    _require(repository == CANONICAL_REPOSITORY, "SOURCE_REVIEW_REPOSITORY_MISMATCH")
    _positive_int(pr_number, "SOURCE_REVIEW_PR_NUMBER")
    _require(bool(SHA.fullmatch(expected_head_sha)), "SOURCE_REVIEW_EXPECTED_HEAD_INVALID")
    _require(bool(token), "SOURCE_REVIEW_GITHUB_TOKEN_MISSING")

    def get_json(path: str) -> Any:
        return _github_get_json(repository=repository, path=path, token=token)

    def pr_identity() -> dict[str, Any]:
        value = get_json(f"/pulls/{pr_number}")
        _require(isinstance(value, Mapping), "SOURCE_REVIEW_PR_EVIDENCE_INVALID")
        head = value.get("head") or {}
        base = value.get("base") or {}
        user = value.get("user") or {}
        _require(isinstance(head, Mapping) and isinstance(base, Mapping) and isinstance(user, Mapping),
                 "SOURCE_REVIEW_PR_IDENTITY_INVALID")
        _require(
            value.get("number") == pr_number
            and value.get("state") == "open"
            and value.get("draft") is False
            and not value.get("merged_at"),
            "SOURCE_REVIEW_PR_NOT_READY",
        )
        _require(
            head.get("sha") == expected_head_sha
            and isinstance(head.get("ref"), str)
            and bool(head.get("ref"))
            and (head.get("repo") or {}).get("full_name") == repository
            and base.get("ref") == "main"
            and (base.get("repo") or {}).get("full_name") == repository,
            "SOURCE_REVIEW_PR_IDENTITY_MISMATCH",
        )
        return {
            "number": pr_number,
            "state": value["state"],
            "draft": value["draft"],
            "merged_at": value.get("merged_at"),
            "changed_files": value.get("changed_files"),
            "user": {"id": user.get("id"), "login": user.get("login")},
            "head": {
                "sha": head.get("sha"),
                "ref": head.get("ref"),
                "repo": {"full_name": (head.get("repo") or {}).get("full_name")},
            },
            "base": {
                "ref": base.get("ref"),
                "repo": {"full_name": (base.get("repo") or {}).get("full_name")},
            },
        }

    initial = pr_identity()
    expected_count = initial.get("changed_files")
    _require(
        isinstance(expected_count, int) and not isinstance(expected_count, bool)
        and 0 < expected_count <= MAX_DIFF_FILES,
        "SOURCE_REVIEW_DIFF_LIMIT_EXCEEDED",
    )
    api_files = get_json(f"/pulls/{pr_number}/files?per_page={MAX_DIFF_FILES}&page=1")
    _require(isinstance(api_files, list), "SOURCE_REVIEW_DIFF_INVENTORY_INVALID")
    files = [
        {
            "filename": item.get("filename"),
            "previous_filename": item.get("previous_filename"),
            "status": item.get("status"),
            "patch": item.get("patch"),
            "additions": item.get("additions"),
            "deletions": item.get("deletions"),
        }
        for item in api_files
        if isinstance(item, Mapping)
    ]
    _require(len(files) == len(api_files), "SOURCE_REVIEW_DIFF_INVENTORY_INVALID")
    normalized, digest = canonical_diff(files, expected_count=expected_count)
    commit = get_json(f"/commits/{expected_head_sha}")
    commit_metadata = _commit_metadata(commit, expected_head_sha=expected_head_sha)
    current = pr_identity()
    _require(current == initial, "SOURCE_REVIEW_PR_MOVED_DURING_CAPTURE")
    return {
        "pr": initial,
        "files": normalized,
        "commit": commit_metadata,
        "diff_sha256": digest,
    }


def verify_artifact(
    artifact: object,
    *,
    run: Mapping[str, Any],
    expected_repository: str,
    expected_pr_number: int,
    expected_head_sha: str,
    expected_head_ref: str,
    expected_attempt: int | None = None,
    now: datetime | None = None,
    producer_context: bool = False,
) -> None:
    keys = {
        "schema_version", "repository", "target", "diff", "commit", "review", "source",
        "created_at_utc", "expires_at_utc",
    }
    artifact = _exact_keys(artifact, keys, "SOURCE_REVIEW_ARTIFACT")
    _require(artifact.get("schema_version") == 1, "SOURCE_REVIEW_SCHEMA_VERSION_UNSUPPORTED")
    _require(artifact.get("repository") == expected_repository == CANONICAL_REPOSITORY,
             "SOURCE_REVIEW_REPOSITORY_MISMATCH")
    target = _exact_keys(
        artifact.get("target"), {"pr_number", "author", "base_ref", "head_ref", "head_sha"},
        "SOURCE_REVIEW_TARGET",
    )
    _require(target.get("pr_number") == expected_pr_number, "SOURCE_REVIEW_PR_NUMBER_MISMATCH")
    _require(target.get("base_ref") == "main", "SOURCE_REVIEW_BASE_MISMATCH")
    _require(target.get("head_sha") == expected_head_sha and bool(SHA.fullmatch(expected_head_sha)),
             "SOURCE_REVIEW_HEAD_MISMATCH")
    _require(target.get("head_ref") == expected_head_ref, "SOURCE_REVIEW_HEAD_REF_MISMATCH")
    author = _exact_keys(target.get("author"), {"id", "login"}, "SOURCE_REVIEW_AUTHOR")
    _positive_int(author.get("id"), "SOURCE_REVIEW_AUTHOR_ID")
    _require(isinstance(author.get("login"), str) and author["login"].strip(),
             "SOURCE_REVIEW_AUTHOR_LOGIN_INVALID")
    commit = _exact_keys(
        artifact.get("commit"),
        {"sha", "tree_sha", "parent_shas", "message", "author_login", "committer_login"},
        "SOURCE_REVIEW_COMMIT",
    )
    _require(commit.get("sha") == expected_head_sha, "SOURCE_REVIEW_COMMIT_SHA_MISMATCH")
    _require(isinstance(commit.get("tree_sha"), str) and bool(SHA.fullmatch(commit["tree_sha"])),
             "SOURCE_REVIEW_COMMIT_TREE_INVALID")
    parents = commit.get("parent_shas")
    _require(
        isinstance(parents, list) and all(isinstance(parent, str) and bool(SHA.fullmatch(parent))
                                          for parent in parents),
        "SOURCE_REVIEW_COMMIT_PARENTS_INVALID",
    )
    _require(
        isinstance(commit.get("message"), str) and commit["message"].strip()
        and len(commit["message"].encode("utf-8")) <= 8192,
        "SOURCE_REVIEW_COMMIT_MESSAGE_INVALID",
    )
    for key in ("author_login", "committer_login"):
        _require(commit.get(key) is None or isinstance(commit.get(key), str),
                 "SOURCE_REVIEW_COMMIT_IDENTITY_INVALID")

    diff = _exact_keys(artifact.get("diff"), {"sha256", "file_count", "file_paths"}, "SOURCE_REVIEW_DIFF")
    _require(isinstance(diff.get("sha256"), str) and bool(DIGEST.fullmatch(diff["sha256"])),
             "SOURCE_REVIEW_DIFF_DIGEST_INVALID")
    paths = diff.get("file_paths")
    _require(
        isinstance(paths, list) and 0 < len(paths) <= MAX_DIFF_FILES
        and all(isinstance(path, str) and path for path in paths)
        and paths == sorted(set(paths))
        and diff.get("file_count") == len(paths),
        "SOURCE_REVIEW_DIFF_INVENTORY_INVALID",
    )
    review = _exact_keys(
        artifact.get("review"),
        {"decision", "summary", "assessment", "findings", "reviewed_files"},
        "SOURCE_REVIEW_DECISION",
    )
    decision = review.get("decision")
    _require(decision in {"PASS", "REJECT"}, "SOURCE_REVIEW_DECISION_INVALID")
    _require(
        isinstance(review.get("summary"), str)
        and review["summary"].strip()
        and len(review["summary"]) <= 4000,
        "SOURCE_REVIEW_SUMMARY_MISSING",
    )
    assessment = _exact_keys(
        review.get("assessment"),
        {"source_review", "security_review", "probability_integrity_review"},
        "SOURCE_REVIEW_ASSESSMENT",
    )
    _require(review.get("reviewed_files") == paths, "SOURCE_REVIEW_FILE_COVERAGE_INCOMPLETE")
    if decision == "PASS":
        _require(all(value == "PASS" for value in assessment.values()),
                 "SOURCE_REVIEW_ASSESSMENT_FAILED")
    findings = review.get("findings")
    _require(isinstance(findings, list) and len(findings) <= 50, "SOURCE_REVIEW_FINDINGS_INVALID")
    for finding in findings:
        finding = _exact_keys(finding, {"severity", "file", "detail", "evidence"}, "SOURCE_REVIEW_FINDING")
        _require(finding.get("severity") in {"critical", "high", "medium", "low"},
                 "SOURCE_REVIEW_FINDING_SEVERITY_INVALID")
        _require(finding.get("file") in paths, "SOURCE_REVIEW_FINDING_FILE_UNKNOWN")
        _require(all(isinstance(finding.get(key), str) and finding[key].strip()
                     for key in ("detail", "evidence")), "SOURCE_REVIEW_FINDING_INCOMPLETE")
        _require(all(len(finding[key]) <= 4000 for key in ("detail", "evidence")),
                 "SOURCE_REVIEW_FINDING_LIMIT_EXCEEDED")
        _require(
            decision == "REJECT" or finding.get("severity") not in {"critical", "high"},
            "SOURCE_REVIEW_HIGH_FINDING_NOT_REJECTED",
        )
    if decision == "REJECT":
        _require(
            any(value == "FAIL" for value in assessment.values()) or bool(findings),
            "SOURCE_REVIEW_REJECTION_EVIDENCE_MISSING",
        )

    source = _exact_keys(
        artifact.get("source"),
        {
            "run_id", "run_attempt", "workflow_name", "workflow_path", "event", "head_branch",
            "head_sha", "provider_identity", "credential_scope",
        },
        "SOURCE_REVIEW_PROVENANCE",
    )
    source_run_id = source.get("run_id")
    _require(
        isinstance(source_run_id, str)
        and bool(re.fullmatch(r"[1-9][0-9]*", source_run_id))
        and source_run_id == str(run.get("id")),
        "SOURCE_REVIEW_RUN_ID_MISMATCH",
    )
    attempt = _positive_int(source.get("run_attempt"), "SOURCE_REVIEW_RUN_ATTEMPT")
    _require(expected_attempt is None or attempt == expected_attempt, "SOURCE_REVIEW_RUN_ATTEMPT_MISMATCH")
    _require(run.get("run_attempt") == attempt, "SOURCE_REVIEW_RUN_ATTEMPT_MISMATCH")
    _require(
        source.get("workflow_name") == WORKFLOW_NAME
        and source.get("workflow_path") == WORKFLOW_PATH
        and source.get("event") in {"workflow_dispatch", "workflow_run"}
        and source.get("head_branch") == "main"
        and source.get("head_sha") == run.get("head_sha")
        and source.get("provider_identity") == PROVIDER_IDENTITY
        and source.get("credential_scope") == CREDENTIAL_SCOPE,
        "SOURCE_REVIEW_PROVENANCE_MISMATCH",
    )
    if producer_context:
        repository = run.get("repository")
        repository_name = (
            repository if isinstance(repository, str)
            else repository.get("full_name") if isinstance(repository, Mapping)
            else None
        )
        _require(
            # REST run.name is a dynamic display title; authenticate the
            # protected workflow path, producer receipt and source metadata.
            run.get("path") == WORKFLOW_PATH
            and run.get("event") == source.get("event")
            and run.get("head_branch") == "main"
            and repository_name == expected_repository,
            "SOURCE_REVIEW_PRODUCER_CONTEXT_UNTRUSTED",
        )
    else:
        repository = run.get("repository")
        head_repository = run.get("head_repository")
        _require(
            run.get("name") == WORKFLOW_NAME
            and run.get("path") == WORKFLOW_PATH
            and run.get("event") == source.get("event")
            and run.get("head_branch") == "main"
            and isinstance(head_repository, Mapping)
            and head_repository.get("full_name") == expected_repository
            and isinstance(repository, Mapping)
            and repository.get("full_name") == expected_repository
            and run.get("status") == "completed"
            and run.get("conclusion") == "success",
            "SOURCE_REVIEW_WORKFLOW_RUN_UNTRUSTED",
        )
    created = _timestamp(artifact.get("created_at_utc"), "SOURCE_REVIEW_CREATED_AT")
    expires = _timestamp(artifact.get("expires_at_utc"), "SOURCE_REVIEW_EXPIRY")
    _require(expires == created + MAX_REVIEW_AGE, "SOURCE_REVIEW_EXPIRY_MISMATCH")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "SOURCE_REVIEW_CLOCK_INVALID")
    _require(created <= clock.astimezone(timezone.utc) < expires, "SOURCE_REVIEW_EXPIRED_OR_FUTURE")


def write_artifact(artifact: Mapping[str, Any], output: Path) -> None:
    payload = (json.dumps(artifact, indent=2, sort_keys=True) + "\n").encode("utf-8")
    output.write_bytes(payload)
    output.with_suffix(output.suffix + ".sha256").write_text(
        hashlib.sha256(payload).hexdigest() + "\n", encoding="ascii"
    )


def resolve_source_review_run(
    artifact: object,
    *,
    artifact_id: int,
    repository: str,
    token: str,
    now: datetime | None = None,
    get_json: Any = None,
) -> Mapping[str, Any]:
    _require(bool(token), "SOURCE_REVIEW_GITHUB_TOKEN_MISSING")
    _require(isinstance(artifact, Mapping), "SOURCE_REVIEW_ARTIFACT_INVALID")
    source = artifact.get("source") or {}
    target = artifact.get("target") or {}
    _require(isinstance(source, Mapping) and isinstance(target, Mapping),
             "SOURCE_REVIEW_PROVENANCE_INVALID")
    run_id = source.get("run_id")
    _require(isinstance(run_id, str) and bool(re.fullmatch(r"[1-9][0-9]*", run_id)),
             "SOURCE_REVIEW_RUN_ID_INVALID")
    attempt = _positive_int(source.get("run_attempt"), "SOURCE_REVIEW_RUN_ATTEMPT")
    pr_number = _positive_int(target.get("pr_number"), "SOURCE_REVIEW_PR_NUMBER")
    head_sha = target.get("head_sha")
    head_ref = target.get("head_ref")
    _require(isinstance(head_sha, str) and bool(SHA.fullmatch(head_sha)),
             "SOURCE_REVIEW_HEAD_INVALID")
    _require(isinstance(head_ref, str) and bool(head_ref), "SOURCE_REVIEW_HEAD_REF_INVALID")
    artifact_id = _positive_int(artifact_id, "SOURCE_REVIEW_ARTIFACT_ID")

    json_fetch = get_json or (lambda path: _github_get_json(repository=repository, path=path, token=token))
    run = json_fetch(f"/actions/runs/{run_id}")
    artifact_meta = json_fetch(f"/actions/artifacts/{artifact_id}")
    expected_name = (
        f"wow-v17-independent-source-review-{pr_number}-{head_sha}-{run_id}-{attempt}"
    )
    _require(
        isinstance(artifact_meta, Mapping)
        and artifact_meta.get("id") == artifact_id
        and artifact_meta.get("expired") is False
        and artifact_meta.get("name") == expected_name
        and (artifact_meta.get("workflow_run") or {}).get("id") == int(run_id),
        "SOURCE_REVIEW_ARTIFACT_PROVENANCE_MISMATCH",
    )

    records = []
    for page in range(1, 21):
        response = json_fetch(f"/actions/artifacts?per_page=100&page={page}")
        _require(isinstance(response, Mapping) and isinstance(response.get("artifacts"), list),
                 "SOURCE_REVIEW_ARTIFACT_INVENTORY_INVALID")
        page_records = response["artifacts"]
        records.extend(page_records)
        _require(len(records) <= MAX_ARTIFACT_INVENTORY,
                 "SOURCE_REVIEW_ARTIFACT_INVENTORY_LIMIT_EXCEEDED")
        if len(page_records) < 100:
            break
    else:
        raise SourceReviewError("SOURCE_REVIEW_ARTIFACT_INVENTORY_LIMIT_EXCEEDED")
    verify_unique_artifact_inventory(
        [
            record for record in records
            if isinstance(record, Mapping)
            and isinstance(record.get("name"), str)
            and record["name"].startswith(f"wow-v17-independent-source-review-{pr_number}-{head_sha}-")
            and record["name"].endswith(f"-{run_id}-{attempt}")
        ],
        prefix=f"wow-v17-independent-source-review-{pr_number}-{head_sha}-",
        expected_artifact_id=artifact_id,
        now=now,
    )
    verify_artifact(
        artifact,
        run=run,
        expected_repository=repository,
        expected_pr_number=pr_number,
        expected_head_sha=head_sha,
        expected_head_ref=head_ref,
        expected_attempt=attempt,
        now=now,
    )
    return run


def build_qa_review_policy(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    head_ref: str,
    mode: str,
    run: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    _require(repository == CANONICAL_REPOSITORY, "QA_POLICY_REPOSITORY_MISMATCH")
    _positive_int(pr_number, "QA_POLICY_PR_NUMBER")
    _require(bool(SHA.fullmatch(head_sha)), "QA_POLICY_HEAD_INVALID")
    _require(isinstance(head_ref, str) and bool(head_ref), "QA_POLICY_HEAD_REF_INVALID")
    _require(mode in {"legacy", "independent", "canary"}, "QA_POLICY_MODE_INVALID")
    _require(isinstance(run, Mapping), "QA_POLICY_RUN_INVALID")
    _positive_int(run.get("id"), "QA_POLICY_RUN_ID")
    attempt = _positive_int(run.get("run_attempt"), "QA_POLICY_RUN_ATTEMPT")
    _require(
        run.get("name") == WORKFLOW_NAME
        and run.get("path") == WORKFLOW_PATH
        and run.get("event") in {"workflow_dispatch", "workflow_run"}
        and run.get("head_branch") == "main"
        and run.get("repository") == repository
        and isinstance(run.get("head_sha"), str)
        and bool(SHA.fullmatch(run["head_sha"])),
        "QA_POLICY_RUN_IDENTITY_INVALID",
    )
    _require(mode != "canary" or run.get("event") == "workflow_dispatch",
             "QA_POLICY_CANARY_REQUIRES_DISPATCH")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "QA_POLICY_CLOCK_INVALID")
    created = clock.astimezone(timezone.utc)
    return {
        "schema_version": 1,
        "repository": repository,
        "target": {
            "pr_number": pr_number,
            "head_sha": head_sha,
            "head_ref": head_ref,
        },
        "mode": mode,
        "canary_scope": {"pr_number": pr_number, "head_sha": head_sha} if mode == "canary" else None,
        "source": {
            "run_id": str(run["id"]),
            "run_attempt": attempt,
            "workflow_name": WORKFLOW_NAME,
            "workflow_path": WORKFLOW_PATH,
            "event": run["event"],
            "head_branch": "main",
            "head_sha": run["head_sha"],
            "repository": repository,
        },
        "created_at_utc": _utc(created),
        "expires_at_utc": _utc(created + MAX_REVIEW_AGE),
    }


def verify_qa_review_policy(
    artifact: object,
    *,
    run: Mapping[str, Any],
    expected_repository: str,
    expected_pr_number: int,
    expected_head_sha: str,
    expected_attempt: int,
    now: datetime | None = None,
) -> None:
    artifact = _exact_keys(
        artifact,
        {"schema_version", "repository", "target", "mode", "canary_scope", "source",
         "created_at_utc", "expires_at_utc"},
        "QA_POLICY_ARTIFACT",
    )
    _require(artifact.get("schema_version") == 1, "QA_POLICY_SCHEMA_VERSION_UNSUPPORTED")
    _require(artifact.get("repository") == expected_repository == CANONICAL_REPOSITORY,
             "QA_POLICY_REPOSITORY_MISMATCH")
    target = _exact_keys(artifact.get("target"), {"pr_number", "head_sha", "head_ref"}, "QA_POLICY_TARGET")
    _require(target.get("pr_number") == expected_pr_number, "QA_POLICY_PR_NUMBER_MISMATCH")
    _require(target.get("head_sha") == expected_head_sha and bool(SHA.fullmatch(expected_head_sha)),
             "QA_POLICY_HEAD_MISMATCH")
    _require(isinstance(target.get("head_ref"), str) and bool(target["head_ref"]),
             "QA_POLICY_HEAD_REF_INVALID")
    policy_mode = artifact.get("mode")
    _require(policy_mode in {"legacy", "independent", "canary"}, "QA_POLICY_MODE_INVALID")
    scope = artifact.get("canary_scope")
    if policy_mode == "canary":
        _require(
            scope == {"pr_number": expected_pr_number, "head_sha": expected_head_sha},
            "QA_POLICY_CANARY_SCOPE_MISMATCH",
        )
    else:
        _require(scope is None, "QA_POLICY_CANARY_SCOPE_UNEXPECTED")

    source = _exact_keys(
        artifact.get("source"),
        {"run_id", "run_attempt", "workflow_name", "workflow_path", "event", "head_branch",
         "head_sha", "repository"},
        "QA_POLICY_SOURCE",
    )
    run_id = _positive_int(run.get("id"), "QA_POLICY_RUN_ID")
    attempt = _positive_int(source.get("run_attempt"), "QA_POLICY_RUN_ATTEMPT")
    _require(
        source.get("run_id") == str(run_id)
        and attempt == expected_attempt == run.get("run_attempt")
        and source.get("workflow_name") == WORKFLOW_NAME
        and source.get("workflow_path") == run.get("path") == WORKFLOW_PATH
        and source.get("event") == run.get("event")
        and source.get("event") in {"workflow_dispatch", "workflow_run"}
        and source.get("head_branch") == run.get("head_branch") == "main"
        and source.get("head_sha") == run.get("head_sha")
        and source.get("repository") == expected_repository
        and (run.get("repository") or {}).get("full_name") == expected_repository
        and (run.get("head_repository") or {}).get("full_name") == expected_repository
        and run.get("status") == "completed"
        and run.get("conclusion") in {"success", "failure"},
        "QA_POLICY_RUN_PROVENANCE_MISMATCH",
    )
    _require(policy_mode != "canary" or source.get("event") == "workflow_dispatch",
             "QA_POLICY_CANARY_REQUIRES_DISPATCH")
    created = _timestamp(artifact.get("created_at_utc"), "QA_POLICY_CREATED_AT")
    expires = _timestamp(artifact.get("expires_at_utc"), "QA_POLICY_EXPIRY")
    _require(expires == created + MAX_REVIEW_AGE, "QA_POLICY_EXPIRY_MISMATCH")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None, "QA_POLICY_CLOCK_INVALID")
    _require(created <= clock.astimezone(timezone.utc) < expires, "QA_POLICY_EXPIRED_OR_FUTURE")


def resolve_qa_review_policy(
    *,
    repository: str,
    run_id: int,
    run_attempt: int,
    token: str,
    expected_pr_number: int | None = None,
    now: datetime | None = None,
    get_json: Any = None,
    download_artifact: Any = None,
) -> dict[str, Any]:
    _require(repository == CANONICAL_REPOSITORY, "QA_POLICY_REPOSITORY_MISMATCH")
    _positive_int(run_id, "QA_POLICY_RUN_ID")
    _positive_int(run_attempt, "QA_POLICY_RUN_ATTEMPT")
    _require(bool(token), "QA_POLICY_GITHUB_TOKEN_MISSING")
    json_fetch = get_json or (lambda path: _github_get_json(repository=repository, path=path, token=token))
    artifact_fetch = download_artifact or (
        lambda artifact_id: _github_download_artifact(
            repository=repository, artifact_id=artifact_id, token=token
        )
    )
    run = json_fetch(f"/actions/runs/{run_id}")
    _require(isinstance(run, Mapping), "QA_POLICY_RUN_UNREADABLE")
    _require(run.get("id") == run_id, "QA_POLICY_RUN_ID_MISMATCH")
    _require(run.get("run_attempt") == run_attempt, "QA_POLICY_RUN_ATTEMPT_MISMATCH")

    inventory: list[Mapping[str, Any]] = []
    for page in range(1, 21):
        response = json_fetch(f"/actions/runs/{run_id}/artifacts?per_page=100&page={page}")
        _require(isinstance(response, Mapping) and isinstance(response.get("artifacts"), list),
                 "QA_POLICY_ARTIFACT_INVENTORY_INVALID")
        rows = response["artifacts"]
        _require(len(inventory) + len(rows) <= MAX_ARTIFACT_INVENTORY,
                 "QA_POLICY_ARTIFACT_INVENTORY_LIMIT_EXCEEDED")
        inventory.extend(row for row in rows if isinstance(row, Mapping))
        if len(rows) < 100:
            break
    else:
        raise SourceReviewError("QA_POLICY_ARTIFACT_INVENTORY_LIMIT_EXCEEDED")

    expected_name = f"wow-v17-qa-policy-{run_id}-{run_attempt}"
    matching = [row for row in inventory if row.get("name") == expected_name]
    _require(len(matching) == 1, "QA_POLICY_ARTIFACT_DUPLICATE_OR_MISSING")
    metadata = matching[0]
    artifact_id = _positive_int(metadata.get("id"), "QA_POLICY_ARTIFACT_ID")
    _require(metadata.get("expired") is False, "QA_POLICY_ARTIFACT_EXPIRED")
    created = _timestamp(metadata.get("created_at"), "QA_POLICY_ARTIFACT_CREATED_AT")
    clock = now or datetime.now(timezone.utc)
    _require(clock.tzinfo is not None and created <= clock.astimezone(timezone.utc) < created + MAX_REVIEW_AGE,
             "QA_POLICY_ARTIFACT_STALE_OR_FUTURE")
    actual_metadata = json_fetch(f"/actions/artifacts/{artifact_id}")
    _require(
        isinstance(actual_metadata, Mapping)
        and actual_metadata.get("id") == artifact_id
        and actual_metadata.get("name") == expected_name
        and actual_metadata.get("expired") is False
        and (actual_metadata.get("workflow_run") or {}).get("id") == run_id,
        "QA_POLICY_ARTIFACT_PROVENANCE_MISMATCH",
    )
    archive = artifact_fetch(artifact_id)
    _require(isinstance(archive, bytes) and len(archive) <= MAX_REVIEW_ARTIFACT_BYTES,
             "QA_POLICY_ARTIFACT_LIMIT_EXCEEDED")
    try:
        with ZipFile(io.BytesIO(archive)) as archive_file:
            names = archive_file.namelist()
            _require(
                sorted(names) == sorted([QA_POLICY_ARTIFACT_NAME, QA_POLICY_ARTIFACT_NAME + ".sha256"]),
                "QA_POLICY_ARTIFACT_CONTENTS_INVALID",
            )
            payload = archive_file.read(QA_POLICY_ARTIFACT_NAME)
            checksum = archive_file.read(QA_POLICY_ARTIFACT_NAME + ".sha256").decode("ascii").strip()
        _require(
            bool(DIGEST.fullmatch(checksum)) and hashlib.sha256(payload).hexdigest() == checksum,
            "QA_POLICY_ARTIFACT_CHECKSUM_MISMATCH",
        )
        policy = json.loads(payload)
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceReviewError("QA_POLICY_ARTIFACT_INVALID") from exc

    target = policy.get("target") if isinstance(policy, Mapping) else None
    pr_number = target.get("pr_number") if isinstance(target, Mapping) else None
    head_sha = target.get("head_sha") if isinstance(target, Mapping) else None
    _positive_int(pr_number, "QA_POLICY_PR_NUMBER")
    _require(expected_pr_number is None or pr_number == expected_pr_number, "QA_POLICY_PR_NUMBER_MISMATCH")
    verify_qa_review_policy(
        policy,
        run=run,
        expected_repository=repository,
        expected_pr_number=pr_number,
        expected_head_sha=head_sha,
        expected_attempt=run_attempt,
        now=now,
    )
    pr = json_fetch(f"/pulls/{pr_number}")
    _require(
        isinstance(pr, Mapping)
        and pr.get("number") == pr_number
        and pr.get("state") == "open"
        and pr.get("draft") is False
        and not pr.get("merged_at")
        and (pr.get("head") or {}).get("sha") == head_sha
        and (pr.get("head") or {}).get("ref") == target.get("head_ref")
        and ((pr.get("head") or {}).get("repo") or {}).get("full_name") == repository
        and (pr.get("base") or {}).get("ref") == "main"
        and ((pr.get("base") or {}).get("repo") or {}).get("full_name") == repository,
        "QA_POLICY_TARGET_NO_LONGER_CURRENT",
    )
    return {"artifact_id": artifact_id, "policy": policy, "run": run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--capture", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--write-policy", action="store_true")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--head-ref")
    parser.add_argument("--attempt", type=int)
    parser.add_argument("--policy-mode", choices=("legacy", "independent", "canary"))
    args = parser.parse_args(argv)
    try:
        if args.capture:
            _require(args.output is not None, "SOURCE_REVIEW_CAPTURE_ARGUMENT_MISSING")
            captured = capture_pr_evidence(
                repository=args.repository,
                pr_number=args.pr_number,
                expected_head_sha=args.head_sha,
                token=os.environ.get("GITHUB_TOKEN", ""),
            )
            args.output.write_text(json.dumps(captured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"status": "SOURCE_REVIEW_INPUT_CAPTURED",
                              "head_sha": args.head_sha, "diff_sha256": captured["diff_sha256"]}, sort_keys=True))
            return 0
        if args.build:
            _require(args.input is not None and args.result is not None and args.run is not None
                     and args.output is not None and args.head_ref is not None,
                     "SOURCE_REVIEW_BUILD_ARGUMENT_MISSING")
            captured = json.loads(args.input.read_text(encoding="utf-8"))
            run = json.loads(args.run.read_text(encoding="utf-8"))
            _require(isinstance(captured, Mapping), "SOURCE_REVIEW_INPUT_INVALID")
            artifact = build_artifact(
                repository=args.repository,
                pr=captured.get("pr") or {},
                files=captured.get("files"),
                commit=captured.get("commit"),
                review_result=json.loads(args.result.read_text(encoding="utf-8")),
                run=run,
            )
            _require(artifact["target"]["pr_number"] == args.pr_number
                     and artifact["target"]["head_sha"] == args.head_sha
                     and artifact["target"]["head_ref"] == args.head_ref,
                     "SOURCE_REVIEW_BUILD_TARGET_MISMATCH")
            write_artifact(artifact, args.output)
            print(json.dumps({"status": (
                                  "INDEPENDENT_SOURCE_REVIEW_ARTIFACT_CREATED"
                                  if artifact["review"]["decision"] == "PASS"
                                  else "INDEPENDENT_SOURCE_REVIEW_REJECTED"
                              ),
                              "pr_number": args.pr_number, "head_sha": args.head_sha,
                              "can_execute": False}, sort_keys=True))
            return 0
        if args.write_policy:
            _require(args.output is not None and args.run is not None and args.head_ref is not None
                     and args.attempt is not None and args.policy_mode is not None,
                     "QA_POLICY_BUILD_ARGUMENT_MISSING")
            run = json.loads(args.run.read_text(encoding="utf-8"))
            artifact = build_qa_review_policy(
                repository=args.repository,
                pr_number=args.pr_number,
                head_sha=args.head_sha,
                head_ref=args.head_ref,
                mode=args.policy_mode,
                run=run,
            )
            _require(artifact["source"]["run_attempt"] == args.attempt, "QA_POLICY_RUN_ATTEMPT_MISMATCH")
            write_artifact(artifact, args.output)
            print(json.dumps({"status": "QA_REVIEW_POLICY_RECORDED", "mode": args.policy_mode,
                              "head_sha": args.head_sha}, sort_keys=True))
            return 0
        _require(args.artifact is not None and args.run is not None and args.head_ref is not None,
                 "SOURCE_REVIEW_VERIFY_ARGUMENT_MISSING")
        artifact_bytes = args.artifact.read_bytes()
        checksum = args.artifact.with_suffix(args.artifact.suffix + ".sha256").read_text(encoding="ascii").strip()
        _require(bool(DIGEST.fullmatch(checksum)) and hashlib.sha256(artifact_bytes).hexdigest() == checksum,
                 "SOURCE_REVIEW_ARTIFACT_CHECKSUM_MISMATCH")
        artifact = json.loads(artifact_bytes)
        run = json.loads(args.run.read_text(encoding="utf-8"))
        verify_artifact(
            artifact,
            run=run,
            expected_repository=args.repository,
            expected_pr_number=args.pr_number,
            expected_head_sha=args.head_sha,
            expected_head_ref=args.head_ref,
            expected_attempt=args.attempt,
        )
    except SourceReviewError as exc:
        print(json.dumps({"status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 1
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        print(json.dumps({"status": "HOLD", "reason": "SOURCE_REVIEW_EVIDENCE_IO_INVALID"}, sort_keys=True))
        return 1
    print(json.dumps({
        "status": "INDEPENDENT_SOURCE_REVIEW_VERIFIED",
        "pr_number": args.pr_number,
        "head_sha": args.head_sha,
        "can_execute": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
