#!/usr/bin/env python3
"""Read-only V17 review-readiness evidence classifier. Never authorizes merges/deploys."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
BLOCKED_ACCOUNTS = {"github-actions", "dependabot", "renovate", "copilot", "github"}
VALID_STATES = {"APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED", "PENDING"}
PROTECTED_PARTS = ("secret", "credential", "auth", "permission", "terminal", "calibration")
MODEL_PARTS = ("models", "model_artifact", "model_training", "model_registry", "fitted_model")


def usernames(text: str) -> list[str]:
    result = []
    for raw in re.split(r"[\s,]+", text or ""):
        value = raw.strip()
        if not value:
            continue
        if not USERNAME.fullmatch(value) or value.lower() in BLOCKED_ACCOUNTS:
            raise ValueError("INVALID_APPROVER_CONFIGURATION")
        if value.lower() not in {x.lower() for x in result}:
            result.append(value.lower())
    return result


def risk_class(files: list[str]) -> str:
    if not files or not all(isinstance(p, str) and p.strip() for p in files):
        return "R3_PROTECTED_UNKNOWN"
    for raw in files:
        p = raw.lower().replace("\\", "/")
        name = p.rsplit("/", 1)[-1]
        if p.startswith(".github/") or p == "render.yaml":
            return "R3_PROTECTED"
        if name in {"wow_v17_custom_gpt_instructions.txt", "openapi.wow-betting-engine.v17.yaml"}:
            return "R3_PROTECTED"
        if any(x in p for x in PROTECTED_PARTS) or p.endswith((".sql", ".tf", ".tfvars")):
            return "R3_PROTECTED"
    for raw in files:
        p = raw.lower().replace("\\", "/")
        if any(x in p for x in MODEL_PARTS) or (
            p.startswith("artifacts/wow-engine/") and
            any(x in p for x in ("specialist", "distribution", "probability_engine"))
        ):
            return "CLASS_C_REVIEW"
    if all(p.lower().endswith((".md", ".rst", ".txt")) for p in files):
        return "R1_DOCUMENTATION"
    return "R2_ENGINEERING"


def last_review_states(reviews: list[dict], head_sha: str) -> tuple[set[str], set[str]]:
    latest = {}
    stale = set()
    for position, review in enumerate(reviews):
        user = (review.get("user") or {}).get("login", "").lower()
        if not user or user.endswith("[bot]") or user in BLOCKED_ACCOUNTS:
            continue
        state = str(review.get("state", "")).upper()
        if state not in VALID_STATES or state == "PENDING":
            continue
        # API review order is chronological; use submitted_at and index for ties.
        when = review.get("submitted_at") or ""
        key = (when, position)
        if user not in latest or key >= latest[user][0]:
            latest[user] = (key, state, review.get("commit_id") or "")
    approved = set()
    for user, (_, state, commit) in latest.items():
        if state == "APPROVED":
            if commit == head_sha and head_sha:
                approved.add(user)
            else:
                stale.add(user)
    return approved, stale


def evaluate(pr: dict, files: list[str], reviews: list[dict],
             qa_names: list[str], release_names: list[str]) -> dict:
    risk = risk_class(files)
    author = ((pr.get("user") or {}).get("login") or "").lower()
    sha = str((pr.get("head") or {}).get("sha") or "")
    draft = bool(pr.get("draft", True))
    reviewers, stale = last_review_states(reviews, sha)
    qa_allowed = set(qa_names) - {author}
    release_allowed = set(release_names) - {author}
    qa_approved = sorted(reviewers & qa_allowed)
    # A release approver cannot also serve as the independent QA signer for the same PR.
    release_approved = sorted((reviewers & release_allowed) - set(qa_approved))
    release_needed = risk != "R1_DOCUMENTATION"
    blockers = []
    if draft:
        blockers.append("DRAFT_PR")
    if not sha or not re.fullmatch(r"[a-f0-9]{40}", sha):
        blockers.append("EXACT_HEAD_UNKNOWN")
    if not qa_allowed:
        blockers.append("INDEPENDENT_QA_REVIEWER_UNCONFIGURED")
    elif not qa_approved:
        blockers.append("INDEPENDENT_QA_APPROVAL_MISSING")
    if release_needed:
        if not release_allowed - set(qa_approved):
            blockers.append("INDEPENDENT_RELEASE_APPROVER_UNCONFIGURED")
        elif not release_approved:
            blockers.append("RELEASE_APPROVAL_MISSING")
    if stale and not qa_approved:
        blockers.append("STALE_HEAD_APPROVAL")
    if not files:
        blockers.append("CHANGED_FILE_LIST_UNAVAILABLE")
    held = bool(blockers)
    # No output in this tool is a merge or production authorization. Existing protected
    # CI, branch rules, human identities, SIRT and environment protection still apply.
    requested = {x.get("login", "").lower() for x in pr.get("requested_reviewers") or []}
    return {
        "schema": "WOW_V17_APPROVAL_READINESS_V1",
        "pr_number": pr.get("number"),
        "head_sha": sha,
        "risk_class": risk,
        "qa_approved": qa_approved,
        "release_approved": release_approved,
        "qa_request_candidates": sorted(qa_allowed - reviewers - requested) if not draft else [],
        "release_request_candidates": sorted((release_allowed - reviewers - requested) - set(qa_approved)) if not draft and release_needed else [],
        "status": "HELD" if held else "REVIEW_RECEIPTS_PRESENT_NOT_MERGE_AUTHORIZED",
        "blockers": sorted(set(blockers)),
        "merge_authorized": False,
        "production_deploy_authorized": False,
        "sporting_probability_authority": False,
        "can_execute": False,
        "notes": [
            "GitHub native protected rules, reviewer permissions, exact-head CI and deployment environment must be independently verified.",
            "No automatic approval, merge, deployment, terminal override or wager execution."
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-json", required=True, type=Path)
    parser.add_argument("--files-json", required=True, type=Path)
    parser.add_argument("--reviews-json", required=True, type=Path)
    parser.add_argument("--qa-reviewers", default="")
    parser.add_argument("--release-reviewers", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        pr = json.loads(args.pr_json.read_text())
        files = json.loads(args.files_json.read_text())
        reviews = json.loads(args.reviews_json.read_text())
        assert isinstance(pr, dict) and isinstance(files, list) and isinstance(reviews, list)
        result = evaluate(pr, files, reviews, usernames(args.qa_reviewers), usernames(args.release_reviewers))
    except (ValueError, TypeError, AssertionError, json.JSONDecodeError, OSError) as exc:
        result = {
            "schema": "WOW_V17_APPROVAL_READINESS_V1",
            "status": "HELD", "blockers": ["APPROVAL_AUDIT_INPUT_INVALID"],
            "diagnostic": type(exc).__name__,
            "merge_authorized": False, "production_deploy_authorized": False,
            "can_execute": False,
        }
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
