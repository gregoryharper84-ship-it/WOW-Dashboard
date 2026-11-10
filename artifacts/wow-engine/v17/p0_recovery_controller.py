"""Fail-closed P0 recovery planner. Pure/deterministic; no deployment or merge permissions.

The Conductor may propose independent parallel recovery lanes, but never approve itself.
"""
from dataclasses import dataclass
from enum import Enum


class Blocker(str, Enum):
    REVIEW_MISSING = "REVIEW_MISSING"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    DEPLOYMENT_STALE = "DEPLOYMENT_STALE"
    ACTION_DRIFT = "ACTION_DRIFT"
    CERTIFICATION_MISSING = "CERTIFICATION_MISSING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RecoveryPlan:
    blocker: Blocker
    permanent_fix: str
    safe_workaround: str
    parallel_next_action: str
    release_hold: bool = True
    can_execute: bool = False


PLANS = {
    Blocker.REVIEW_MISSING: (
        "Audit merge actors, rulesets, independent-review receipts; repair enforcement.",
        "Build a fresh exact-revision release candidate for distinct independent review and owner authorization; preserve historic findings.",
        "Prepare immutable deployment manifest and non-wagering Action tests in parallel.",
    ),
    Blocker.PROVIDER_FAILURE: (
        "Repair typed provider/structured-output handling with bounded retry and regression.",
        "Route to a distinct authorized human/independent reviewer; keep failed provider result HOLD.",
        "Continue deterministic CI, deployment preflight and read-only contract checks.",
    ),
    Blocker.DEPLOYMENT_STALE: (
        "Reconcile source SHA, approved release SHA and Render live SHA.",
        "Prepare exact approved SHA for owner-authorized manual deploy; never deploy automatically.",
        "Prepare authenticated read-only Action canary and rollback manifest.",
    ),
    Blocker.ACTION_DRIFT: (
        "Repair gateway/editor canonical schema synchronization and add parity regression.",
        "Use already-certified read-only single-event route only if independently verified.",
        "Generate operation/security/digest parity receipt without publishing picks.",
    ),
    Blocker.CERTIFICATION_MISSING: (
        "Run frozen exact-line or route-specific calibration and independent certification.",
        "Keep candidate in research/shadow with publishable=false.",
        "Collect replay evidence and prepare non-publishing canaries.",
    ),
    Blocker.UNKNOWN: (
        "Collect typed error, exact SHA, failing job and reproducible regression.",
        "Fail closed; request human diagnosis rather than inventing a bypass.",
        "Continue independent read-only evidence gathering.",
    ),
}


def plan_recovery(blocker: str) -> RecoveryPlan:
    try:
        category = Blocker(blocker)
    except (ValueError, TypeError):
        category = Blocker.UNKNOWN
    fix, workaround, next_action = PLANS[category]
    return RecoveryPlan(category, fix, workaround, next_action)


def may_release(*, exact_head_ci: bool, independent_review: bool,
                owner_authorized: bool, calibrated: bool,
                deployed_revision_matches: bool, action_verified: bool) -> bool:
    return all((exact_head_ci, independent_review, owner_authorized,
                calibrated, deployed_revision_matches, action_verified))
