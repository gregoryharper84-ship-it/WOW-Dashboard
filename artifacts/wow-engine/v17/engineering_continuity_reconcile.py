"""Evidence-only reconciliation of a V17 continuity outbox dispatch.

This module NEVER clears a durable intent, retries dispatch, grants a writer
lease, merges a PR or authorizes a Render deployment. It only identifies whether
a trusted resolver has enough immutable, independently fetched evidence to
*consider* a separately certified terminal transition. Missing a run for a
GitHub 204/timeout is ALWAYS ambiguous, not permission to retry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import re

_SHA = re.compile(r"[a-f0-9]{40}\Z")
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_ALLOWED_WORKFLOWS = frozenset({
    ".github/workflows/wow-v17-chatgpt-engineering-worker.yml",
    ".github/workflows/wow-v17-claude-engineering-worker.yml",
})


@dataclass(frozen=True)
class PendingIntent:
    incident: int
    intent_id: str
    main_sha: str
    action: str
    pr_number: int | None
    pr_head_sha: str | None
    workflow_run_id: int | None


@dataclass(frozen=True)
class RunReceipt:
    """Server-attested readback from a protected GitHub run, not issue text."""
    run_id: int
    repository: str
    workflow_path: str
    event: str
    head_branch: str
    head_sha: str
    status: str
    conclusion: str | None


@dataclass(frozen=True)
class DispatchArtifact:
    """Readback of artifact belonging to the exact trusted workflow run.

    The adapter MUST verify artifact ownership, workflow SHA and job provenance
    using authenticated GitHub APIs before constructing this object. Text/JSON
    in a user comment or unverified PR artifact is not trustworthy.
    """
    workflow_run_id: int
    intent_id: str
    incident: int
    main_sha: str
    action: str
    pr_number: int | None
    pr_head_sha: str | None


@dataclass(frozen=True)
class Reconciliation:
    code: str
    candidate: bool = False
    terminal: bool = False
    may_redispatch: bool = False


class TrustedGithubEvidence(Protocol):
    def fetch_verified_run(self, run_id: int) -> RunReceipt: ...
    def fetch_verified_dispatch_artifact(self, run_id: int, intent_id: str) -> DispatchArtifact: ...


def evaluate_reconciliation(
    intent: PendingIntent, source: TrustedGithubEvidence, *, repository: str
) -> Reconciliation:
    """Read-only. No automatic retry or DB mutation can result."""
    hold = lambda code: Reconciliation(code)
    if (type(intent.incident) is not int or intent.incident <= 0
            or not isinstance(intent.intent_id, str)
            or not _ID.fullmatch(intent.intent_id)
            or not isinstance(intent.main_sha, str)
            or not _SHA.fullmatch(intent.main_sha)
            or intent.action not in {"DISPATCH_NEW_REPAIR", "REPAIR_SAME_BRANCH"}):
        return hold("RECONCILE_INTENT_INVALID")
    if intent.action == "REPAIR_SAME_BRANCH":
        if (type(intent.pr_number) is not int or intent.pr_number <= 0
                or not isinstance(intent.pr_head_sha, str)
                or not _SHA.fullmatch(intent.pr_head_sha)):
            return hold("RECONCILE_PR_IDENTITY_INVALID")
    elif intent.pr_number is not None or intent.pr_head_sha is not None:
        return hold("RECONCILE_PR_IDENTITY_CONTRADICTION")
    if type(intent.workflow_run_id) is not int or intent.workflow_run_id <= 0:
        return hold("RECONCILE_RUN_NOT_CORRELATED")
    if not isinstance(repository, str) or not repository.strip():
        return hold("RECONCILE_REPOSITORY_INVALID")
    try:
        run = source.fetch_verified_run(intent.workflow_run_id)
    except Exception:
        return hold("RECONCILE_RUN_READBACK_UNAVAILABLE")
    if not isinstance(run, RunReceipt):
        return hold("RECONCILE_RUN_READBACK_INVALID")
    if (run.run_id != intent.workflow_run_id or run.repository != repository
            or run.workflow_path not in _ALLOWED_WORKFLOWS
            or run.event != "workflow_dispatch" or run.head_branch != "main"
            or run.head_sha != intent.main_sha):
        return hold("RECONCILE_RUN_PROVENANCE_MISMATCH")
    if run.status != "completed":
        return hold("RECONCILE_RUN_NOT_TERMINAL")
    if run.conclusion not in {"success", "failure", "cancelled", "timed_out"}:
        return hold("RECONCILE_RUN_CONCLUSION_UNKNOWN")
    try:
        artifact = source.fetch_verified_dispatch_artifact(run.run_id, intent.intent_id)
    except Exception:
        return hold("RECONCILE_ARTIFACT_UNAVAILABLE")
    if not isinstance(artifact, DispatchArtifact):
        return hold("RECONCILE_ARTIFACT_INVALID")
    if (artifact.workflow_run_id != run.run_id or artifact.intent_id != intent.intent_id
            or artifact.incident != intent.incident or artifact.main_sha != intent.main_sha
            or artifact.action != intent.action or artifact.pr_number != intent.pr_number
            or artifact.pr_head_sha != intent.pr_head_sha):
        return hold("RECONCILE_ARTIFACT_IDENTITY_MISMATCH")
    # Even a real completed run can leave partial PR mutations, pending jobs,
    # or unresolved QA. Separate independent closure + ledger CAS required.
    if run.conclusion == "success":
        return Reconciliation("RECONCILE_COMPLETED_NEEDS_INDEPENDENT_CLOSURE", candidate=True)
    return Reconciliation("RECONCILE_FAILED_NEEDS_INDEPENDENT_REPAIR_REVIEW", candidate=True)


__all__ = [
    "PendingIntent", "RunReceipt", "DispatchArtifact",
    "Reconciliation", "TrustedGithubEvidence", "evaluate_reconciliation",
]
