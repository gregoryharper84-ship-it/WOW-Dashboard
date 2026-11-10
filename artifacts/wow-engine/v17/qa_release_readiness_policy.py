"""Read-only exact-head readiness observations for independent QA and Release."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from v17.agent_identity_policy import REQUIRED_REGRESSION_CHECKS

GITHUB_ACTIONS_APP_ID = 15368
CHANGE_IMPACT_CHECK = "WOW V17 change impact gate"
TRUSTED_GOVERNANCE_CHECK = "Trusted exact-head engineering governance"
MANDATORY_CHECKS = tuple(
    (name, GITHUB_ACTIONS_APP_ID)
    for name in (*REQUIRED_REGRESSION_CHECKS, CHANGE_IMPACT_CHECK)
)

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_PENDING = frozenset({"queued", "in_progress", "requested", "waiting", "pending"})
_FAILED = frozenset({"failure", "cancelled", "timed_out", "action_required", "neutral", "skipped", "stale"})


@dataclass(frozen=True)
class SourceIdentity:
    app_id: int
    workflow: str
    run_id: int
    run_attempt: int


@dataclass(frozen=True)
class Readiness:
    repository: str
    pr_number: int
    head_sha: str
    source: SourceIdentity | None
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


def _result(
    repository: str,
    pr_number: int,
    head_sha: str,
    source: SourceIdentity | None,
    state: str,
    reasons: Sequence[str],
    evidence_ids: Sequence[int] = (),
) -> Readiness:
    return Readiness(
        repository, pr_number, head_sha, source, state,
        tuple(reasons), tuple(evidence_ids),
    )


def _valid_source(source: Any) -> bool:
    return (
        isinstance(source, SourceIdentity)
        and type(source.app_id) is int
        and source.app_id == GITHUB_ACTIONS_APP_ID
        and isinstance(source.workflow, str)
        and bool(source.workflow.strip())
        and type(source.run_id) is int
        and source.run_id > 0
        and type(source.run_attempt) is int
        and source.run_attempt > 0
    )


def _check_app_id(row: Mapping[str, Any]) -> Any:
    app = row.get("app")
    nested_id = app.get("id") if isinstance(app, Mapping) else None
    flat_id = row.get("app_id")
    if nested_id is not None and flat_id is not None and nested_id != flat_id:
        return None
    return nested_id if nested_id is not None else flat_id


def evaluate_exact_head(
    *,
    repository: str,
    pr_number: int,
    head_sha: str,
    source: SourceIdentity,
    pr: Mapping[str, Any],
    checks: Sequence[Mapping[str, Any]],
    requirements: Sequence[tuple[str, int]] = MANDATORY_CHECKS,
    inventory_complete: bool,
) -> Readiness:
    """Classify exact-head CI evidence without issuing a QA or Release verdict.

    ``checks`` accepts the normalized ``check_runs`` rows from
    ``independent_release_checks.gather`` or raw GitHub check-run records.
    ``source`` identifies the workflow run that requested this observation;
    authentic source/run claims still have to be verified by the external caller.
    """
    if not isinstance(repository, str) or repository.count("/") != 1:
        return _result(repository, pr_number, head_sha, source, "HOLD_IDENTITY", ("REPOSITORY_INVALID",))
    if (
        type(pr_number) is not int or pr_number <= 0
        or not isinstance(head_sha, str) or not _SHA.fullmatch(head_sha)
    ):
        return _result(repository, pr_number, head_sha, source, "HOLD_IDENTITY", ("PR_OR_HEAD_INVALID",))
    if not _valid_source(source):
        return _result(repository, pr_number, head_sha, source, "HOLD_EVIDENCE", ("SOURCE_RUN_IDENTITY_INVALID",))
    if not isinstance(pr, Mapping):
        return _result(repository, pr_number, head_sha, source, "HOLD_IDENTITY", ("PR_INVENTORY_INVALID",))

    head, base = pr.get("head"), pr.get("base")
    head_repo = head.get("repo") if isinstance(head, Mapping) else None
    base_repo = base.get("repo") if isinstance(base, Mapping) else None
    if not isinstance(head_repo, Mapping) or not isinstance(base_repo, Mapping):
        return _result(repository, pr_number, head_sha, source, "HOLD_IDENTITY", ("PR_REPOSITORY_MISSING",))
    if (
        type(pr.get("number")) is not int or pr["number"] != pr_number
        or pr.get("state") != "open"
        or pr.get("draft") is not False
        or head.get("sha") != head_sha
        or head_repo.get("full_name") != repository
        or base.get("ref") != "main"
        or base_repo.get("full_name") != repository
    ):
        return _result(repository, pr_number, head_sha, source, "HOLD_IDENTITY", ("PR_EXACT_HEAD_OR_REPOSITORY_MISMATCH",))

    if inventory_complete is not True or not isinstance(checks, (list, tuple)):
        return _result(repository, pr_number, head_sha, source, "HOLD_EVIDENCE", ("CHECK_INVENTORY_INCOMPLETE",))
    if (
        not isinstance(requirements, (list, tuple)) or not requirements
        or any(
            not isinstance(item, tuple) or len(item) != 2
            or not isinstance(item[0], str) or not item[0]
            or type(item[1]) is not int or item[1] <= 0
            for item in requirements
        )
        or len(set(requirements)) != len(requirements)
        or len({item[0] for item in requirements}) != len(requirements)
    ):
        return _result(repository, pr_number, head_sha, source, "HOLD_EVIDENCE", ("REQUIREMENTS_INVALID",))
    if not set(MANDATORY_CHECKS).issubset(set(requirements)):
        return _result(repository, pr_number, head_sha, source, "HOLD_EVIDENCE", ("MANDATORY_REQUIREMENTS_OMITTED",))

    by_id: dict[int, list[Mapping[str, Any]]] = {}
    for row in checks:
        if (
            isinstance(row, Mapping)
            and type(row.get("id")) is int and row["id"] > 0
            and row.get("head_sha") == head_sha
        ):
            by_id.setdefault(row["id"], []).append(row)

    collision_names: set[str] = set()
    for rows in by_id.values():
        first = rows[0]
        first_app = _check_app_id(first)
        if any(
            row.get("name") != first.get("name")
            or row.get("status") != first.get("status")
            or row.get("conclusion") != first.get("conclusion")
            or _check_app_id(row) != first_app
            for row in rows[1:]
        ):
            collision_names.update(row["name"] for row in rows if isinstance(row.get("name"), str))

    holds: list[str] = []
    pending: list[str] = []
    used_ids: list[int] = []
    if any(not isinstance(row, Mapping) for row in checks):
        holds.append("CHECK_INVENTORY_MALFORMED")

    for name, app_id in requirements:
        candidates = [row for row in checks if isinstance(row, Mapping) and row.get("name") == name]
        if name in collision_names:
            holds.append("CHECK_ID_COLLISION:" + name)
            continue

        authentic: list[Mapping[str, Any]] = []
        wrong_source = False
        for row in candidates:
            check_id = row.get("id")
            source_app = _check_app_id(row)
            if type(check_id) is not int or check_id <= 0 or type(source_app) is not int:
                holds.append("CHECK_PROVENANCE_INVALID:" + name)
                continue
            if row.get("head_sha") != head_sha:
                continue
            if source_app != app_id:
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
        used_ids.append(latest["id"])
        status, conclusion = latest.get("status"), latest.get("conclusion")
        if status in _PENDING and conclusion is None:
            pending.append("WAIT_UPSTREAM:" + name)
        elif status == "completed" and conclusion == "success":
            continue
        elif status == "completed" and conclusion in _FAILED:
            holds.append("CHECK_FAILED:" + name + ":" + str(conclusion))
        else:
            holds.append("CHECK_STATE_INVALID:" + name)

    if holds:
        return _result(repository, pr_number, head_sha, source, "HOLD_CI_OR_EVIDENCE", (*holds, *pending), used_ids)
    if pending:
        return _result(repository, pr_number, head_sha, source, "WAIT_UPSTREAM", pending, used_ids)
    return _result(repository, pr_number, head_sha, source, "READY_FOR_INDEPENDENT_REVIEW", (), used_ids)


def advise_reentry(
    *,
    previous: Readiness | None,
    current: Readiness,
    role: str,
    active_lease: bool,
    already_completed: bool = False,
    provider_state: str = "unknown",
    qa_receipt_verified: bool = False,
    previous_provider_state: str = "unknown",
    previous_qa_receipt_verified: bool = False,
) -> Reentry:
    """Suggest an external, separately authorized wakeup; never dispatch or publish."""
    if role not in {"qa", "release"}:
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
    if role == "qa":
        if provider_state in {"quota_exceeded", "timeout"}:
            return Reentry("WAIT", "REVIEW_PROVIDER_UNAVAILABLE:" + provider_state)
        if provider_state != "available":
            return Reentry("HOLD", "REVIEW_PROVIDER_IDENTITY_OR_STATE_INVALID")

    if previous is not None and previous.state == "READY_FOR_INDEPENDENT_REVIEW":
        provider_recovered = (
            role == "qa"
            and previous_provider_state in {"quota_exceeded", "timeout"}
            and provider_state == "available"
        )
        receipt_arrived = (
            role == "release"
            and qa_receipt_verified is True
            and previous_qa_receipt_verified is not True
        )
        if not (provider_recovered or receipt_arrived):
            return Reentry("WAIT", "NO_NEW_UPSTREAM_TRANSITION")

    lease_key = ":".join((current.repository, str(current.pr_number), current.head_sha, role))
    return Reentry("READY_FOR_EXTERNAL_DISPATCH", "AUTHENTICATED_REENTRY_CANDIDATE", lease_key)
