"""Fail-closed non-authorizing packet and PR-shape validation for a WOW Copilot pilot.

This module does NOT mint leases, assign an issue to Copilot, approve code, merge
a PR, dispatch a workflow or produce a production verification receipt.
All evidence comes from untrusted JSON until separately checked against GitHub
and the authoritative incident lease store by an independent controller.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import PurePosixPath
from typing import Any, Mapping

SCHEMA = "WOW_COPILOT_HANDOFF_V1"
SHA = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LEASE = re.compile(r"^[A-Z0-9_]{1,72}$")
PROTECTED_PREFIXES = (".github/", ".agents/")
PROTECTED_FILES = frozenset({
    ".agents",
    ".github",
    ".github/CODEOWNERS",
    ".github/actions/wow-chatgpt-agent/*",
    ".github/actions/wow-claude-agent/*",
    ".github/scripts/verify_engineering_governance_receipt.py",
    ".github/scripts/verify_existing_pr_governance_receipt.py",
    ".github/workflows/wow-engine-verify.yml",
    ".github/workflows/wow-v17-change-impact-gate.yml",
    ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    ".github/workflows/wow-v17-claude-engineering-worker.yml",
    ".github/workflows/wow-v17-engineering-provider-dispatcher.yml",
    ".github/workflows/wow-v17-existing-pr-governance-review.yml",
    ".github/workflows/wow-v17-morning-green-continuation.yml",
    ".github/workflows/wow-v17-rapid-repair.yml",
    ".github/workflows/wow-v17-release-production-verification-agent.yml",
    ".github/workflows/wow-v17-trusted-governance-gate.yml",
    ".github/workflows/wow-verify.yml",
    "artifacts/flask-scoring-api/requirements.txt",
    "artifacts/wow-engine/requirements.txt",
    "artifacts/wow-engine/v17/agent_identity_policy.py",
    "artifacts/wow-engine/v17/engineering_agent_team.py",
    "artifacts/wow-engine/v17/engineering_dispatch_manifest.json",
    "artifacts/wow-engine/v17/engineering_dispatch_queue.py",
    "artifacts/wow-engine/v17/engineering_effectiveness.py",
    "artifacts/wow-engine/v17/engineering_incident_identity.py",
    "artifacts/wow-engine/v17/engineering_provider_failover.py",
    "artifacts/wow-engine/v17/independent_release_checks.py",
    "artifacts/wow-engine/v17/persistent_worker_safety.py",
    "render.yaml",
})
VALID_CLASSES = {"A", "B", "C"}
VALID_SEVERITIES = {"P0", "P1", "P2", "P3"}
COPILOT_ACTORS = {"Copilot", "copilot-swe-agent[bot]"}


def _valid_path(path: Any) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/") or "\\" in path:
        return False
    if any(ord(char) < 33 or ord(char) == 127 for char in path):
        return False
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if PurePosixPath(path).as_posix() != path:
        return False
    return True


def _protected(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES) or any(
        fnmatch.fnmatchcase(path, pattern) for pattern in PROTECTED_FILES
    )


def validate_handoff(packet: Mapping[str, Any], *, repository: str, current_main_sha: str) -> dict:
    """Validate a proposal, not an authorization or proof of a lease."""
    errors: list[str] = []
    if packet.get("schema") != SCHEMA:
        errors.append("SCHEMA_UNRECOGNIZED")
    if not isinstance(repository, str) or not REPO.fullmatch(repository) or packet.get("repository") != repository:
        errors.append("REPOSITORY_MISMATCH")
    incident = packet.get("incident_id")
    if type(incident) is not int or incident <= 0:
        errors.append("INCIDENT_ID_INVALID")
    if packet.get("mode") != "PROPOSE_ONLY" or packet.get("can_execute") is not False:
        errors.append("EXECUTION_AUTHORITY_FORBIDDEN")
    source = packet.get("source_main_sha")
    if not isinstance(source, str) or not SHA.fullmatch(source):
        errors.append("SOURCE_SHA_INVALID")
    if not isinstance(current_main_sha, str) or not SHA.fullmatch(current_main_sha) or source != current_main_sha:
        errors.append("MAIN_SHA_STALE")
    change_class = packet.get("change_class")
    if not isinstance(change_class, str) or change_class not in VALID_CLASSES:
        errors.append("CHANGE_CLASS_INVALID")
    if not isinstance(packet.get("severity"), str) or packet.get("severity") not in VALID_SEVERITIES:
        errors.append("SEVERITY_INVALID")
    if packet.get("owner") != "WOW_ENGINEERING":
        errors.append("INCIDENT_OWNER_INVALID")
    lease_group = packet.get("lease_group")
    if not isinstance(lease_group, str) or not LEASE.fullmatch(lease_group):
        errors.append("LEASE_GROUP_INVALID")
    paths = packet.get("allowed_paths")
    if not isinstance(paths, list) or not paths or len(paths) > 20:
        errors.append("ALLOWED_PATHS_INVALID")
    else:
        seen: set[str] = set()
        for path in paths:
            if not _valid_path(path):
                errors.append("UNSAFE_PATH")
                continue
            if path in seen:
                errors.append("ALLOWED_PATHS_DUPLICATE")
            seen.add(path)
            if _protected(path):
                errors.append("PROTECTED_PATH_REQUIRES_GOVERNED_REVIEW")
    if isinstance(change_class, str) and change_class in {"B", "C"}:
        errors.append("GOVERNED_CHANGE_REQUIRES_INDEPENDENT_APPROVAL")
    if errors:
        return {"decision": "HOLD", "reasons": sorted(set(errors)), "can_execute": False,
                "assignment_authorized": False}
    return {"decision": "PACKET_VALID_NON_AUTHORIZING", "reasons": [],
            "can_execute": False, "assignment_authorized": False,
            "required_next_evidence": ["authoritative_lease_and_conflict_inventory",
                                       "trusted_handoff_actor", "copilot_session_start"]}



def _links_incident(body: Any, repository: str, incident: int) -> bool:
    """Accept only a local issue mention, this repo's qualified shorthand, or full URL."""
    if not isinstance(body, str) or not isinstance(repository, str):
        return False
    label = str(incident)
    repo = re.escape(repository)
    patterns = (
        rf"(?<![\w/#])#{label}(?!\d)",
        rf"(?<![\w/]){repo}#{label}(?!\d)",
        rf"https://github\.com/{repo}/issues/{label}(?![\d/])",
    )
    return any(re.search(pattern, body) for pattern in patterns)


def verify_draft_pr(
    packet: Mapping[str, Any], pr: Mapping[str, Any], changed_files: list[str],
    *, repository: str, current_main_sha: str
) -> dict:
    """Shape-check a purported Copilot PR. This NEVER certifies CI or deployment."""
    result = validate_handoff(packet, repository=repository, current_main_sha=current_main_sha)
    if result["decision"] != "PACKET_VALID_NON_AUTHORIZING":
        return result
    if not isinstance(pr, Mapping):
        return {"decision": "HOLD", "reasons": ["PR_SNAPSHOT_INVALID"],
                "can_execute": False, "assignment_authorized": False}
    errors: list[str] = []
    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    author = pr.get("user") if isinstance(pr.get("user"), dict) else {}
    if pr.get("state") != "open" or pr.get("draft") is not True or pr.get("merged") is True:
        errors.append("PR_NOT_OPEN_DRAFT")
    if not isinstance(author.get("login"), str) or author.get("login") not in COPILOT_ACTORS:
        errors.append("PR_AUTHOR_NOT_COPILOT")
    if base.get("ref") != "main" or base.get("sha") != packet["source_main_sha"]:
        errors.append("PR_BASE_SHA_UNBOUND")
    base_repo = base.get("repo") if isinstance(base.get("repo"), dict) else {}
    if base_repo.get("full_name") != repository:
        errors.append("PR_BASE_REPOSITORY_MISMATCH")
    if not isinstance(head.get("sha"), str) or not SHA.fullmatch(head["sha"]):
        errors.append("PR_HEAD_SHA_INVALID")
    if head.get("sha") == packet["source_main_sha"]:
        errors.append("PR_HAS_NO_DISTINCT_HEAD")
    head_repo = head.get("repo") if isinstance(head.get("repo"), dict) else {}
    if head_repo.get("full_name") != repository:
        errors.append("PR_REPOSITORY_MISMATCH")
    body = pr.get("body") or ""
    incident = packet["incident_id"]
    if not _links_incident(body, repository, incident):
        errors.append("PR_INCIDENT_LINK_MISSING")
    changed_count = pr.get("changed_files")
    if type(changed_count) is not int or changed_count <= 0:
        errors.append("PR_CHANGED_FILE_COUNT_INVALID")
    if not isinstance(changed_files, list) or not changed_files:
        errors.append("PR_FILES_UNVERIFIED")
    else:
        if type(changed_count) is int and changed_count != len(changed_files):
            errors.append("PR_FILE_INVENTORY_INCOMPLETE")
        seen_files: set[str] = set()
        for path in changed_files:
            if not _valid_path(path):
                errors.append("PR_SCOPE_VIOLATION")
                continue
            if path in seen_files or path not in packet["allowed_paths"]:
                errors.append("PR_SCOPE_VIOLATION")
            seen_files.add(path)
    if errors:
        return {"decision": "HOLD", "reasons": sorted(set(errors)),
                "can_execute": False, "assignment_authorized": False}
    return {"decision": "PR_CREATED_UNVERIFIED", "reasons": [],
            "can_execute": False, "assignment_authorized": False,
            "head_sha": head["sha"],
            "required_next_evidence": ["exact_head_ci", "independent_code_review",
                                       "CODEOWNER_if_applicable", "release_authority",
                                       "authorized_merge", "live_sha_and_acceptance",
                                       "independent_production_QA"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", help="Path to JSON handoff packet")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--current-main-sha", required=True)
    args = parser.parse_args()
    with open(args.packet, encoding="utf-8") as source:
        packet = json.load(source)
    if not isinstance(packet, dict):
        raise SystemExit("PACKET_NOT_OBJECT")
    decision = validate_handoff(packet, repository=args.repository,
                                current_main_sha=args.current_main_sha)
    print(json.dumps(decision, sort_keys=True))
    return 0 if decision["decision"] == "PACKET_VALID_NON_AUTHORIZING" else 2


if __name__ == "__main__":
    raise SystemExit(main())
