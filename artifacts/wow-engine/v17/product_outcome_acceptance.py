"""Read-only product-outcome, handoff and model-recovery acceptance audit for WOW V17.

Consumes externally attributed receipts; it neither scores sporting events nor
dispatches providers, controls leases, merges, deploys, or authorizes wagers.
It does not mint backend failure codes or replace existing independent gates.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from math import isfinite
from typing import Any, Mapping, Sequence

from v17.amrt_closure_gate import closure_gate

TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
TERMINAL_DISPOSITIONS = frozenset({"SCORED", "HELD", "REJECTED", "PURGED"})
ALL_DISPOSITIONS = TERMINAL_DISPOSITIONS | {"PENDING"}


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and 0 <= value <= 1


def valid_numeric_package(row: Mapping[str, Any]) -> bool:
    """Verify evidence shape; never infer a missing probability from market data."""
    lower = row.get("calibrated_lower_bound")
    point = row.get("calibrated_probability")
    raw = row.get("raw_model_probability")
    if not all(_probability(v) for v in (raw, point, lower)):
        return False
    if not (0 < raw < 1 and 0 < point < 1 and lower <= point):
        return False
    return all(_nonempty(row.get(key)) for key in (
        "controlling_specialist", "model_artifact_id", "model_timestamp",
        "terminal_receipt_ref",
    )) and row.get("probability_receipt_verified") is True


def audit_handoffs(handoffs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Audit ownership without creating a second queue or automatic approvals."""
    failures: list[str] = []
    for index, item in enumerate(handoffs):
        if not all(_nonempty(item.get(k)) for k in (
            "incident_id", "current_owner", "next_owner", "state",
            "evidence_ref", "next_action", "reentry_trigger",
        )):
            failures.append(f"HANDOFF_{index}_UNOWNED_OR_UNATTRIBUTED")
        if item.get("state") == "FIXED_AND_VERIFIED":
            if item.get("independent_production_closure_verified") is not True:
                failures.append(f"HANDOFF_{index}_FALSE_CLOSURE")
    return {"total": len(handoffs), "valid": len(failures) == 0, "findings": failures}


def product_outcome_scorecard(
    requests: Sequence[Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compute evidence-based metrics; empty/unknown denominators remain None.

    A HELD candidate may be a correct user-visible outcome, but is never
    counted as a usable sporting probability. A scored row with an invalid
    package is unresolved regardless of its claimed disposition.
    """
    issues: list[str] = []
    seen: set[tuple[str, str]] = set()
    per_request: dict[str, list[str]] = defaultdict(list)
    counts: Counter[str] = Counter()
    by_lane: dict[str, Counter[str]] = defaultdict(Counter)
    eligible_count = 0
    rank_eligible_count = 0
    unknown_eligibility = False

    for i, row in enumerate(rows):
        request_id = row.get("request_id")
        candidate_id = row.get("candidate_id")
        state = row.get("disposition")
        if not _nonempty(request_id) or not _nonempty(candidate_id):
            issues.append(f"ROW_{i}_IDENTITY_MISSING")
            continue
        key = (request_id, candidate_id)
        if key in seen:
            issues.append(f"ROW_{i}_DUPLICATE_CANONICAL_ID")
            continue
        seen.add(key)
        valid = state in ALL_DISPOSITIONS and _nonempty(row.get("current_owner"))
        if state in {"HELD", "REJECTED", "PURGED", "PENDING"}:
            valid = valid and all(_nonempty(row.get(k)) for k in (
                "failure_code", "evidence_ref", "next_action", "reentry_trigger",
            ))
        if state == "SCORED":
            valid = valid and valid_numeric_package(row)
            if row.get("event_status") == "LIVE":
                valid = valid and row.get("dedicated_live_specialist_certified") is True
        if row.get("rank_eligible") is True:
            valid = valid and state == "SCORED" and row.get("terminal_rank_eligible_verified") is True
        if not valid:
            issues.append(f"ROW_{i}_UNPROVEN_DISPOSITION")
            state = "UNRESOLVED"
        per_request[request_id].append(state)
        counts[state] += 1
        lane = str(row.get("sport") or "UNSPECIFIED") + "/" + str(row.get("market_family") or "UNSPECIFIED")
        by_lane[lane][state] += 1
        if row.get("eligible") is True:
            eligible_count += 1
            if state == "SCORED" and row.get("rank_eligible") is True:
                rank_eligible_count += 1
        elif row.get("eligible") is not False:
            unknown_eligibility = True
            issues.append(f"ROW_{i}_ELIGIBILITY_UNATTESTED")

    unique_requests: set[str] = set()
    supported_count = 0
    completed_count = 0
    first_pass_count = 0
    for i, req in enumerate(requests):
        rid = req.get("request_id")
        if not _nonempty(rid) or rid in unique_requests:
            issues.append(f"REQUEST_{i}_IDENTITY_MISSING_OR_DUPLICATE")
            continue
        unique_requests.add(rid)
        if req.get("supported") is not True:
            if req.get("supported") is not False:
                issues.append(f"REQUEST_{i}_SUPPORT_UNATTESTED")
            continue
        supported_count += 1
        dispositions = per_request.get(rid, [])
        empty_verified = not dispositions and req.get("no_current_candidates_verified") is True
        complete = (
            req.get("output_delivered_verified") is True
            and (empty_verified or (bool(dispositions) and all(s in TERMINAL_DISPOSITIONS for s in dispositions)))
        )
        if complete:
            completed_count += 1
            if req.get("provider_attempts") == 1 and req.get("repair_cycles") == 0:
                first_pass_count += 1

    for rid in per_request:
        if rid not in unique_requests:
            issues.append("ROW_REQUEST_NOT_IN_INVENTORY:" + rid)

    canonical_count = len(seen)
    terminal_count = sum(counts[s] for s in TERMINAL_DISPOSITIONS)
    return {
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "audit_complete": len(issues) == 0,
        "findings": issues,
        "rows_unique": canonical_count,
        "rows_terminal": terminal_count,
        "rows_unresolved": canonical_count - terminal_count,
        "candidate_conservation_rate": terminal_count / canonical_count if canonical_count else None,
        "supported_requests": supported_count,
        "completed_requests": completed_count,
        "product_outcome_completion_rate": completed_count / supported_count if supported_count else None,
        "first_pass_completion_rate": first_pass_count / supported_count if supported_count else None,
        "eligible_candidates": eligible_count if not unknown_eligibility else None,
        "rank_eligible_candidates": rank_eligible_count,
        "reliable_decision_availability": (
            rank_eligible_count / eligible_count if eligible_count and not unknown_eligibility else None
        ),
        "dispositions": dict(counts),
        "by_sport_market": {lane: dict(result) for lane, result in sorted(by_lane.items())},
    }


def model_restoration_acceptance(
    closure_receipt: Mapping[str, Any], product_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Additional model-specific proof AFTER the existing AMRT closure gate.

    A proper infrastructure release can exist without a publishable model; only
    the exact restored model route with independent row evidence is accepted.
    """
    gate = closure_gate(closure_receipt)
    blockers = list(gate.blockers)
    if product_receipt.get("can_execute") is not False or product_receipt.get("terminal_authority") != TERMINAL_AUTHORITY:
        blockers.append("PRODUCT_GOVERNANCE_UNPROVEN")
    for key in (
        "correct_sport_market_route", "model_invoked_verified",
        "dynamic_calibration_verified", "final_refresh_verified",
        "full_requested_scope_reconciled", "sirt_independent_verification",
    ):
        if product_receipt.get(key) is not True:
            blockers.append("PRODUCT_" + key.upper() + "_UNPROVEN")
    if not valid_numeric_package(product_receipt):
        blockers.append("PRODUCT_NUMERIC_PACKAGE_UNPROVEN")
    if product_receipt.get("event_status") == "LIVE" and product_receipt.get("dedicated_live_specialist_certified") is not True:
        blockers.append("PRODUCT_LIVE_MODEL_UNCERTIFIED")
    if product_receipt.get("run_id") != closure_receipt.get("action_run_id"):
        blockers.append("PRODUCT_ACTION_RUN_MISMATCH")
    if product_receipt.get("deployed_sha") != closure_receipt.get("deployed_sha"):
        blockers.append("PRODUCT_DEPLOYMENT_SHA_MISMATCH")
    return {
        "status": "FIXED_AND_VERIFIED" if not blockers else "BLOCKED_WITH_EXACT_REASON",
        "blockers": tuple(blockers),
        "next_owner": "INDEPENDENT_QA" if not blockers else "RELEASE_VERIFICATION",
        "can_execute": False,
    }
