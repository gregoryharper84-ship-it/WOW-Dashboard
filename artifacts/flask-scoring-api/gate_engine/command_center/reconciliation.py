"""
gate_engine/command_center/reconciliation.py
WOW Sports Intelligence Command Center — Phase 1

Row-level and batch reconciliation.  Runs AFTER ceiling enforcement.

Reconciliation rules (all must pass for CC:RECONCILIATION_PASSED):
  R-01  Every row has a final_label
  R-02  can_execute=False on every row
  R-03  final_label is not less restrictive than cc_ceiling (monotonic)
  R-04  No upstream blocker was erased (cc_blockers is append-only)
  R-05  Kalshi candidates have kalshi_recovery_caps_applied=True
  R-06  intake_valid=False rows have CC:INTAKE_INVALID in cc_blockers
  R-07  Routing-failed rows (no assigned_family) have CC routing blocker
  R-08  Engine result missing → CC:ENGINE_RESULT_MISSING in cc_blockers
  R-09  All label strings are ≤ 120 chars (sanity)
  R-10  No duplicate candidate_ids in the batch

can_execute = False (unconditional)
"""
from __future__ import annotations

from typing import Any

from .cc_labels import (
    CAN_EXECUTE,
    ceiling_rank,
    CC_RECONCILIATION_PASSED,
    CC_RECONCILIATION_FAILED,
    CC_MISSING_FINAL_LABEL,
    CC_CAN_EXECUTE_VIOLATION,
    CC_INTAKE_INVALID,
    CC_ENGINE_RESULT_MISSING,
    CC_ROUTING_CONFLICT,
    CC_ROUTING_UNRESOLVABLE,
    FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER,
)

_KALSHI_FAMILIES = frozenset({FAMILY_KALSHI_SPORTS, FAMILY_KALSHI_WEATHER})


def _check_row(row: dict[str, Any], seen_ids: set[str]) -> list[str]:
    """Return list of reconciliation failure codes for one row."""
    failures: list[str] = []
    cid = row.get("candidate_id", "?")

    # R-01: final_label present
    final_label = row.get("final_label")
    if not final_label:
        failures.append(f"R-01:MISSING_FINAL_LABEL:{cid}")
        row.setdefault("cc_blockers", []).append(CC_MISSING_FINAL_LABEL)

    # R-02: can_execute=False
    if row.get("can_execute") is not False:
        failures.append(f"R-02:CAN_EXECUTE_VIOLATION:{cid}:value={row.get('can_execute')!r}")
        row.setdefault("cc_blockers", []).append(CC_CAN_EXECUTE_VIOLATION)
        row["can_execute"] = CAN_EXECUTE   # force correct value

    # R-03: monotonic ceiling — final_label must be >= cc_ceiling in restrictiveness
    cc_ceil = row.get("cc_ceiling")
    if cc_ceil and final_label:
        if ceiling_rank(final_label) < ceiling_rank(cc_ceil):
            failures.append(
                f"R-03:MONOTONIC_VIOLATION:{cid}:"
                f"final={final_label}(rank={ceiling_rank(final_label)})"
                f"<cc_ceiling={cc_ceil}(rank={ceiling_rank(cc_ceil)})"
            )

    # R-04: intake invalid rows must have CC:INTAKE_INVALID
    if row.get("intake_valid") is False:
        blockers = row.get("cc_blockers") or []
        if not any(b == CC_INTAKE_INVALID or b.startswith("CC:INTAKE") for b in blockers):
            failures.append(f"R-04:INTAKE_INVALID_WITHOUT_CC_BLOCKER:{cid}")

    # R-05: Kalshi candidates must have recovery caps applied
    family = row.get("market_family") or row.get("assigned_family", "")
    if family in _KALSHI_FAMILIES:
        if row.get("kalshi_recovery_caps_applied") is not True:
            failures.append(f"R-05:KALSHI_RECOVERY_CAPS_NOT_APPLIED:{cid}")

    # R-06: routing failures must have routing blocker
    assigned = row.get("assigned_family")
    if not assigned:
        blockers = row.get("cc_blockers") or []
        routing_blocked = any(
            b in (CC_ROUTING_CONFLICT, CC_ROUTING_UNRESOLVABLE)
            or b.startswith("CC:ROUTING")
            for b in blockers
        )
        if not routing_blocked:
            failures.append(f"R-07:ROUTING_FAILED_WITHOUT_CC_BLOCKER:{cid}")

    # R-08: engine result missing on routed candidate
    if assigned and row.get("engine_result") is None:
        blockers = row.get("cc_blockers") or []
        if not any(CC_ENGINE_RESULT_MISSING in b for b in blockers):
            failures.append(f"R-08:ENGINE_RESULT_MISSING_WITHOUT_CC_BLOCKER:{cid}")

    # R-09: label length sanity
    for label_field in ("final_label", "engine_label", "cc_ceiling"):
        lv = row.get(label_field)
        if lv and len(str(lv)) > 120:
            failures.append(f"R-09:LABEL_TOO_LONG:{cid}:{label_field}")

    # R-10: duplicate candidate_id
    if cid in seen_ids:
        failures.append(f"R-10:DUPLICATE_CANDIDATE_ID:{cid}")
    seen_ids.add(cid)

    return failures


def reconcile_row(row: dict[str, Any]) -> dict[str, Any]:
    """Reconcile one row independently. Returns the row with reconciliation status set."""
    seen: set[str] = set()
    failures = _check_row(row, seen)
    if failures:
        row["reconciliation_status"] = CC_RECONCILIATION_FAILED
        row.setdefault("cc_blockers", []).extend(
            [f"{CC_RECONCILIATION_FAILED}:{f}" for f in failures]
        )
    else:
        row["reconciliation_status"] = CC_RECONCILIATION_PASSED
    row["can_execute"] = CAN_EXECUTE
    return row


def reconcile_batch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Reconcile all rows in a batch.

    Returns a report:
      {
        total:            int,
        passed:           int,
        failed:           int,
        failure_details:  list[str],
        all_passed:       bool,
        can_execute:      False,
      }
    """
    seen_ids: set[str] = set()
    all_failures: list[str] = []
    passed_count = 0

    for row in rows:
        row_failures = _check_row(row, seen_ids)
        if row_failures:
            row["reconciliation_status"] = CC_RECONCILIATION_FAILED
            row.setdefault("cc_blockers", []).extend(
                [f"{CC_RECONCILIATION_FAILED}:{f}" for f in row_failures]
            )
            all_failures.extend(row_failures)
        else:
            row["reconciliation_status"] = CC_RECONCILIATION_PASSED
            passed_count += 1
        row["can_execute"] = CAN_EXECUTE

    return {
        "total":           len(rows),
        "passed":          passed_count,
        "failed":          len(rows) - passed_count,
        "failure_details": all_failures,
        "all_passed":      len(all_failures) == 0,
        "can_execute":     CAN_EXECUTE,
    }


def build_run_summary(
    all_rows:       list[dict[str, Any]],
    routing_report: dict[str, Any],
    service_report: dict[str, Any],
    recon_report:   dict[str, Any],
) -> dict[str, Any]:
    """Build the top-level CC run summary."""
    label_counts: dict[str, int] = {}
    for row in all_rows:
        lbl = row.get("final_label") or "UNLABELED"
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    return {
        "total_candidates":        len(all_rows),
        "routed_successfully":     routing_report.get("total_routed", 0),
        "routing_failed":          routing_report.get("total_failed", 0),
        "by_family":               routing_report.get("routing_summary", {}),
        "shared_services_status":  service_report.get("shared_services_status", "UNKNOWN"),
        "reconciliation_passed":   recon_report.get("passed", 0),
        "reconciliation_failed":   recon_report.get("failed", 0),
        "reconciliation_all_pass": recon_report.get("all_passed", False),
        "by_final_label":          label_counts,
        "can_execute":             CAN_EXECUTE,
        "dry_run_only":            True,
        "kalshi_recovery_mode":    "ACTIVE",
    }
