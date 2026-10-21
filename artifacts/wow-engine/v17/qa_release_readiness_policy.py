"""#1944: pure, non-authorizing exact-head QA/Release readiness observations.

This module does not fetch GitHub, start a worker, dispatch a workflow, publish
a check, adjudicate review findings, merge, deploy, or score a betting market.
The trusted dispatcher must supply the required check names/App IDs from the
currently installed protected governance contract and prove pagination
completeness before using this *advisory* observation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from v17.agent_identity_policy import REQUIRED_REGRESSION_CHECKS

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PENDING = frozenset(("queued", "in_progress", "requested", "waiting", "pending"))
_GITHUB_ACTIONS_APP_ID = 15368
_MANDATORY = frozenset((name, _GITHUB_ACTIONS_APP_ID) for name in (
    *REQUIRED_REGRESSION_CHECKS, "WOW V17 change impact gate"
))


@dataclass(frozen=True)
class Readiness:
    repository: str
    pr_number: int
    head_sha: str
    state: str
    reasons: tuple[str, ...]
    evidence_ids: tuple[int, ...] = ()
    can_execute: bool = False
    can_publish_qa: bool = False
    can_publish_release: bool = False
    can_merge: bool = False
    can_deploy: bool = False


@dataclass(frozen=True)
class Reentry:
    state: str
    reason: str
    lease_key: str = ""
    can_execute: bool = False
    dispatch_authorized: bool = False
    can_publish_check: bool = False


def _verdict(repo: str, pr: int, head: str, state: str,
             reasons: Sequence[str], ids: Sequence[int] = ()) -> Readiness:
    return Readiness(repo, pr, head, state, tuple(reasons), tuple(ids))


def evaluate_exact_head(
    *, repository: str, pr_number: int, head_sha: str,
    pr: Mapping[str, Any], checks: Sequence[Mapping[str, Any]],
    requirements: Sequence[tuple[str, int]], inventory_complete: bool,
) -> Readiness:
    """Read-only CI admission observation, NOT QA or Release authorization.

    A requirement is an exact (check_name, installed GitHub App id) pair.
    Only completed GitHub check-runs from the specified App and PR-head SHA
    count. Latest authenticated check-run ID controls a context. Merely
    missing/running evidence is WAIT_UPSTREAM, not a substantive REJECT.
    """
    bad = lambda reason: _verdict(repository, pr_number, head_sha, "HOLD_IDENTITY", (reason,))
    if not isinstance(repository, str) or "/" not in repository:
        return bad("REPOSITORY_INVALID")
    if type(pr_number) is not int or pr_number <= 0 or not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha):
        return bad("PR_OR_HEAD_INVALID")
    if not isinstance(pr, Mapping):
        return bad("PR_INVENTORY_INVALID")
    target_head = pr.get("head")
    target_base = pr.get("base")
    if not isinstance(target_head, Mapping) or not isinstance(target_base, Mapping):
        return bad("PR_IDENTITY_MISSING")
    head_repo = target_head.get("repo")
    base_repo = target_base.get("repo")
    if not isinstance(head_repo, Mapping) or not isinstance(base_repo, Mapping):
        return bad("PR_REPOSITORY_MISSING")
    if (
        pr.get("number") != pr_number
        or pr.get("state") != "open"
        or pr.get("draft") is not False
        or target_head.get("sha") != head_sha
        or head_repo.get("full_name") != repository
        or target_base.get("ref") != "main"
        or base_repo.get("full_name") != repository
    ):
        return bad("PR_EXACT_HEAD_OR_REPOSITORY_MISMATCH")

    if inventory_complete is not True or not isinstance(checks, (list, tuple)):
        return _verdict(repository, pr_number, head_sha, "HOLD_EVIDENCE", ("CHECK_INVENTORY_INCOMPLETE",))
    if not isinstance(requirements, (list, tuple)) or not requirements:
        return _verdict(repository, pr_number, head_sha, "HOLD_EVIDENCE", ("REQUIREMENTS_INVALID",))
    if any(
        not isinstance(item, tuple) or len(item) != 2 or
        not isinstance(item[0], str) or not item[0] or
        type(item[1]) is not int or item[1] <= 0
        for item in requirements
    ) or len(set(requirements)) != len(requirements) or len({x[0] for x in requirements}) != len(requirements):
        return _verdict(repository, pr_number, head_sha, "HOLD_EVIDENCE", ("REQUIREMENTS_INVALID",))

    if not _MANDATORY.issubset(set(requirements)):
        return _verdict(repository, pr_number, head_sha, "HOLD_EVIDENCE", ("MANDATORY_REQUIREMENTS_OMITTED",))

    holds: list[str] = []
    pending: list[str] = []
    used: list[int] = []
    for name, app_id in requirements:
        candidates = [row for row in checks if isinstance(row, Mapping) and row.get("name") == name]
        if any(not isinstance(row, Mapping) for row in checks):
            holds.append("CHECK_INVENTORY_MALFORMED")
            break
        authentic = []
        wrong_source = False
        for row in candidates:
            app = row.get("app")
            if not isinstance(app, Mapping) or type(row.get("id")) is not int or row["id"] <= 0:
                holds.append("CHECK_PROVENANCE_INVALID:" + name)
                continue
            if row.get("head_sha") != head_sha:
                continue
            if app.get("id") != app_id:
                wrong_source = True
                continue
            authentic.append(row)
        if not authentic:
            if wrong_source:
                holds.append("CHECK_APP_MISMATCH:" + name)
            else:
                pending.append("WAIT_UPSTREAM:" + name)
            continue
        latest = max(authentic, key=lambda row: row["id"])
        used.append(latest["id"])
        status = latest.get("status")
        conclusion = latest.get("conclusion")
        if status in _PENDING and conclusion is None:
            pending.append("WAIT_UPSTREAM:" + name)
        elif status == "completed" and conclusion == "success":
            pass
        elif status == "completed" and conclusion in {
            "failure", "cancelled", "timed_out", "action_required", "neutral", "skipped", "stale"
        }:
            holds.append("CHECK_FAILED:" + name + ":" + str(conclusion))
        else:
            holds.append("CHECK_STATE_INVALID:" + name)

    if holds:
        return _verdict(repository, pr_number, head_sha, "HOLD_CI_OR_EVIDENCE", holds + pending, used)
    if pending:
        return _verdict(repository, pr_number, head_sha, "WAIT_UPSTREAM", pending, used)
    return _verdict(repository, pr_number, head_sha, "READY_FOR_INDEPENDENT_REVIEW", (), used)


def advise_reentry(
    *, previous: Readiness | None, current: Readiness, role: str,
    active_lease: bool, already_completed: bool = False,
    provider_state: str = "available", qa_receipt_verified: bool = False,
    previous_provider_state: str = "available",
    previous_qa_receipt_verified: bool = False,
) -> Reentry:
    """Suggest a wakeup to an *independently authorized* dispatcher only.

    The caller must still acquire an atomic repository/PR/SHA/role lease and
    independently authenticate all protected policy, QA artifact and actor
    requirements. A suggestion never itself dispatches or authorizes release.
    """
    if role not in ("qa", "release"):
        return Reentry("HOLD", "ROLE_INVALID")
    if previous is not None and (
        previous.repository != current.repository
        or previous.pr_number != current.pr_number
        or previous.head_sha != current.head_sha
    ):
        return Reentry("HOLD", "HEAD_CHANGED_REVALIDATE")
    if current.state != "READY_FOR_INDEPENDENT_REVIEW":
        return Reentry("WAIT_OR_HOLD", "UPSTREAM_NOT_READY:" + current.state)
    if active_lease:
        return Reentry("WAIT", "ROLE_LEASE_ALREADY_ACTIVE")
    if already_completed:
        return Reentry("NOOP", "AUTHENTICATED_ROLE_ALREADY_COMPLETE")
    if role == "release" and qa_receipt_verified is not True:
        return Reentry("HOLD", "QA_RECEIPT_NOT_INDEPENDENTLY_VERIFIED")
    if provider_state in ("quota_exceeded", "timeout"):
        return Reentry("WAIT", "REVIEW_PROVIDER_UNAVAILABLE:" + provider_state)
    if provider_state != "available":
        return Reentry("HOLD", "REVIEW_PROVIDER_IDENTITY_OR_STATE_INVALID")
    if previous is not None and previous.state == "READY_FOR_INDEPENDENT_REVIEW":
        provider_recovered = role == "qa" and previous_provider_state in ("quota_exceeded", "timeout")
        qa_evidence_arrived = (role == "release" and qa_receipt_verified is True
                               and previous_qa_receipt_verified is not True)
        if not (provider_recovered or qa_evidence_arrived):
            return Reentry("WAIT", "NO_NEW_UPSTREAM_TRANSITION")

    # Lease key is data for an external CAS, not an acquired lease.
    lease_key = ":".join((current.repository, str(current.pr_number), current.head_sha, role))
    return Reentry("READY_FOR_EXTERNAL_DISPATCH", "AUTHENTICATED_REENTRY_CANDIDATE", lease_key)
