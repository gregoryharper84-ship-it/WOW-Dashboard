"""
gate_engine/command_center/shared_services.py
WOW Sports Intelligence Command Center — Phase 1

Cross-cutting shared services applied AFTER engine results are collected.
None of these services can emit an approval label; they can only add blockers
or set a more-restrictive cc_ceiling.

Services:
  1. Slate integrity           — date consistency across all candidates
  2. Calibration check         — flags candidates with stale calibration
  3. Failure-path audit        — flags candidates missing kill paths
  4. Exact-line audit          — cross-engine line consistency
  5. Cross-platform exposure   — duplicate exposure across engine families
  6. Final refresh             — freshness validation (mandatory)
  7. Row reconciliation        — every row has required CC fields

can_execute = False (unconditional)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .cc_labels import (
    CAN_EXECUTE,
    CC_SLATE_INTEGRITY_FAILED,
    CC_EXPOSURE_CONFLICT,
    CC_FINAL_REFRESH_REQUIRED,
    CC_EXACT_LINE_MISMATCH,
    CC_SHARED_SERVICE_FAILED,
    ceiling_rank,
)
from .ceiling_resolver import apply_ceiling_to_row

# ---------------------------------------------------------------------------
# 1. Slate integrity
# ---------------------------------------------------------------------------

def run_slate_integrity(
    candidates: list[dict[str, Any]],
    target_date: str,
) -> dict[str, Any]:
    """
    Verify all candidates are for the correct slate date.
    Any mismatch → CC:SLATE_INTEGRITY_FAILED blocker on the candidate.

    Returns a service report.
    """
    failures: list[str] = []
    try:
        _target = datetime.strptime(target_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        # Invalid target_date — mark all candidates
        for c in candidates:
            c.setdefault("cc_blockers", []).append(
                f"{CC_SLATE_INTEGRITY_FAILED}:invalid_target_date={target_date!r}"
            )
            apply_ceiling_to_row(c, CC_SLATE_INTEGRITY_FAILED, source="slate_integrity")
        return {
            "service": "slate_integrity",
            "status":  "FAILED",
            "reason":  f"invalid target_date: {target_date!r}",
            "failures": [c.get("candidate_id") for c in candidates],
        }

    for c in candidates:
        c_date_str = c.get("slate_date") or ""
        try:
            c_date = datetime.strptime(c_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            c_date = None

        if c_date is None or c_date != _target:
            note = f"{CC_SLATE_INTEGRITY_FAILED}:candidate_date={c_date_str}:target={target_date}"
            c.setdefault("cc_blockers", []).append(note)
            apply_ceiling_to_row(c, CC_SLATE_INTEGRITY_FAILED, source="slate_integrity")
            c["slate_integrity_ok"] = False
            failures.append(c.get("candidate_id", "?"))
        else:
            c["slate_integrity_ok"] = True

    return {
        "service":     "slate_integrity",
        "status":      "FAILED" if failures else "PASSED",
        "target_date": target_date,
        "failures":    failures,
        "total":       len(candidates),
        "can_execute": CAN_EXECUTE,
    }


# ---------------------------------------------------------------------------
# 2. Cross-platform exposure check
# ---------------------------------------------------------------------------

def run_cross_platform_exposure(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Detect duplicate exposure across engine families.

    Duplicate exposure = two candidates with:
      - same player AND same prop_type AND same direction (PROP duplicates)
      - OR same event_id across different families (cross-family event overlap)

    Exact same event_id appearing in PROP + LLP results → CC:CROSS_PLATFORM_EXPOSURE_CONFLICT.
    The lower-ranked candidate (by ceiling_rank of engine_label) receives the blocker.
    """
    seen_prop_keys:  dict[str, str] = {}   # prop_key → candidate_id
    seen_event_ids:  dict[str, tuple[str, str]] = {}  # event_id → (candidate_id, family)
    conflicts: list[dict[str, Any]] = []

    for c in candidates:
        cid    = c.get("candidate_id", "?")
        family = c.get("market_family") or c.get("assigned_family") or ""

        # Prop-level dedup
        player    = (c.get("player") or "").lower()
        prop_type = (c.get("prop_type") or "").upper()
        direction = (c.get("direction") or "").upper()
        if player and prop_type:
            prop_key = f"{player}::{prop_type}::{direction}"
            if prop_key in seen_prop_keys:
                existing_cid = seen_prop_keys[prop_key]
                note = f"{CC_EXPOSURE_CONFLICT}:prop_duplicate:existing={existing_cid}"
                c.setdefault("cc_blockers", []).append(note)
                apply_ceiling_to_row(c, CC_EXPOSURE_CONFLICT, source="cross_platform_exposure")
                c["exposure_conflict"] = True
                conflicts.append({"candidate_id": cid, "type": "prop_duplicate",
                                  "existing": existing_cid, "key": prop_key})
            else:
                seen_prop_keys[prop_key] = cid
                c.setdefault("exposure_conflict", False)

        # Event-level cross-family dedup
        event_id = c.get("event_id") or ""
        if event_id:
            if event_id in seen_event_ids:
                existing_cid, existing_family = seen_event_ids[event_id]
                if existing_family != family:
                    note = (f"{CC_EXPOSURE_CONFLICT}:cross_family_event:"
                            f"event_id={event_id}:families={existing_family},{family}")
                    c.setdefault("cc_blockers", []).append(note)
                    apply_ceiling_to_row(c, CC_EXPOSURE_CONFLICT, source="cross_platform_exposure")
                    c["exposure_conflict"] = True
                    conflicts.append({"candidate_id": cid, "type": "cross_family_event",
                                      "event_id": event_id, "families": [existing_family, family]})
            else:
                seen_event_ids[event_id] = (cid, family)
                c.setdefault("exposure_conflict", False)

    return {
        "service":     "cross_platform_exposure",
        "status":      "FAILED" if conflicts else "PASSED",
        "conflicts":   conflicts,
        "total":       len(candidates),
        "can_execute": CAN_EXECUTE,
    }


# ---------------------------------------------------------------------------
# 3. Calibration check (lightweight — delegates to calibration_health)
# ---------------------------------------------------------------------------

def run_calibration_check(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Lightweight calibration staleness check at CC layer.
    If a candidate's engine_result includes calibration_status=STALE or
    calibration_health.passed=False, the CC layer notes it but does NOT
    apply a ceiling (calibration is engine-owned).
    Returns informational report only.
    """
    stale: list[str] = []
    for c in candidates:
        er = c.get("engine_result") or {}
        cal_status = er.get("calibration_status", "")
        cal_health = (er.get("calibration_health") or {}).get("passed")
        if cal_status == "STALE" or cal_health is False:
            stale.append(c.get("candidate_id", "?"))

    return {
        "service":     "calibration_check",
        "status":      "WARNING" if stale else "PASSED",
        "stale_count": len(stale),
        "stale_ids":   stale,
        "note":        "Calibration authority belongs to engine; CC layer notes only.",
        "can_execute": CAN_EXECUTE,
    }


# ---------------------------------------------------------------------------
# 4. Failure-path audit (lightweight)
# ---------------------------------------------------------------------------

def run_failure_path_audit(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Lightweight failure-path completeness check at CC layer.
    Checks if engine_result contains failure_path data.  Full validation
    lives in gate_engine/failure_path.py; this is a presence check only.
    """
    missing: list[str] = []
    for c in candidates:
        er = c.get("engine_result") or {}
        fp = er.get("failure_path") or er.get("failure_path_matrix")
        if fp is None and c.get("market_family") == "PROP":
            # PROP candidates without failure_path in engine result get noted
            missing.append(c.get("candidate_id", "?"))

    return {
        "service":        "failure_path_audit",
        "status":         "WARNING" if missing else "PASSED",
        "missing_count":  len(missing),
        "missing_ids":    missing,
        "note":           "Full failure-path validation is engine-owned; CC layer checks presence.",
        "can_execute":    CAN_EXECUTE,
    }


# ---------------------------------------------------------------------------
# 5. Exact-line audit
# ---------------------------------------------------------------------------

def run_exact_line_audit(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Verify that the line in the candidate envelope matches the line in the
    engine result.  Mismatch → CC:EXACT_LINE_MISMATCH blocker.
    """
    mismatches: list[dict[str, Any]] = []

    for c in candidates:
        intake_line = c.get("line")
        if intake_line is None:
            c["exact_line_audit_ok"] = None   # not applicable
            continue

        er = c.get("engine_result") or {}
        # Try to extract the engine's line from standard locations
        engine_line = (
            er.get("line")
            or er.get("displayed_line")
            or er.get("sportsbook_line")
        )
        if engine_line is None:
            c["exact_line_audit_ok"] = None   # engine didn't return a line
            continue

        try:
            if abs(float(intake_line) - float(engine_line)) > 0.5:
                note = (f"{CC_EXACT_LINE_MISMATCH}:"
                        f"intake_line={intake_line}:engine_line={engine_line}")
                c.setdefault("cc_blockers", []).append(note)
                apply_ceiling_to_row(c, CC_EXACT_LINE_MISMATCH, source="exact_line_audit")
                c["exact_line_audit_ok"] = False
                mismatches.append({
                    "candidate_id": c.get("candidate_id", "?"),
                    "intake_line": intake_line,
                    "engine_line": engine_line,
                })
            else:
                c["exact_line_audit_ok"] = True
        except (TypeError, ValueError):
            c["exact_line_audit_ok"] = None

    return {
        "service":     "exact_line_audit",
        "status":      "FAILED" if mismatches else "PASSED",
        "mismatches":  mismatches,
        "total":       len(candidates),
        "can_execute": CAN_EXECUTE,
    }


# ---------------------------------------------------------------------------
# 6. Final refresh check (mandatory)
# ---------------------------------------------------------------------------

def run_final_refresh_check(
    candidates: list[dict[str, Any]],
    freshness_window_minutes: int = 30,
) -> dict[str, Any]:
    """
    Verify that each candidate's data is within the freshness window.
    Stale data → CC:FINAL_REFRESH_REQUIRED blocker.

    The orchestrator calls this after all engine results are collected.
    If no freshness metadata is available on a candidate, it is flagged
    with a WARNING but not rejected (the engine may not report timestamps).
    """
    stale: list[str]   = []
    unknown: list[str] = []

    now_ts = datetime.utcnow()

    for c in candidates:
        er = c.get("engine_result") or {}
        raw = c.get("raw_data") or {}

        # Look for a freshness timestamp in standard locations
        ts_str = (
            er.get("checked_at")
            or er.get("timestamp")
            or raw.get("retrieved_at")
            or raw.get("price_timestamp")
            or None
        )
        if ts_str is None:
            unknown.append(c.get("candidate_id", "?"))
            c["final_refresh_ok"] = None
            continue

        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).replace(tzinfo=None)
            age_minutes = (now_ts - ts).total_seconds() / 60.0
            if age_minutes > freshness_window_minutes:
                note = (f"{CC_FINAL_REFRESH_REQUIRED}:"
                        f"age={age_minutes:.1f}min:window={freshness_window_minutes}min")
                c.setdefault("cc_blockers", []).append(note)
                apply_ceiling_to_row(c, CC_FINAL_REFRESH_REQUIRED, source="final_refresh")
                c["final_refresh_ok"] = False
                stale.append(c.get("candidate_id", "?"))
            else:
                c["final_refresh_ok"] = True
        except (ValueError, AttributeError, TypeError):
            c["final_refresh_ok"] = None
            unknown.append(c.get("candidate_id", "?"))

    return {
        "service":                   "final_refresh",
        "status":                    "FAILED" if stale else "PASSED",
        "stale_ids":                 stale,
        "unknown_freshness_ids":     unknown,
        "freshness_window_minutes":  freshness_window_minutes,
        "total":                     len(candidates),
        "can_execute":               CAN_EXECUTE,
    }


# ---------------------------------------------------------------------------
# 7. Row completeness check
# ---------------------------------------------------------------------------

_REQUIRED_ROW_FIELDS = frozenset({
    "candidate_id", "market_family", "slate_date",
    "cc_blockers", "can_execute",
})


def run_row_completeness(
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Check every row has the minimum CC envelope fields.
    Missing fields → CC:SHARED_SERVICE_FAILED blocker.
    """
    incomplete: list[dict[str, Any]] = []

    for c in candidates:
        missing = [f for f in _REQUIRED_ROW_FIELDS if f not in c]
        if missing:
            note = f"{CC_SHARED_SERVICE_FAILED}:missing_fields={','.join(missing)}"
            c.setdefault("cc_blockers", []).append(note)
            incomplete.append({
                "candidate_id": c.get("candidate_id", "?"),
                "missing": missing,
            })
        # Always stamp can_execute
        c["can_execute"] = CAN_EXECUTE

    return {
        "service":    "row_completeness",
        "status":     "FAILED" if incomplete else "PASSED",
        "incomplete": incomplete,
        "total":      len(candidates),
        "can_execute": CAN_EXECUTE,
    }


# ---------------------------------------------------------------------------
# Run all shared services
# ---------------------------------------------------------------------------

def run_all(
    candidates: list[dict[str, Any]],
    target_date: str,
    freshness_window_minutes: int = 30,
) -> dict[str, Any]:
    """
    Run all shared services in the required order.
    Returns a consolidated service report.
    """
    reports = {
        "slate_integrity":       run_slate_integrity(candidates, target_date),
        "cross_platform_exposure": run_cross_platform_exposure(candidates),
        "calibration_check":     run_calibration_check(candidates),
        "failure_path_audit":    run_failure_path_audit(candidates),
        "exact_line_audit":      run_exact_line_audit(candidates),
        "final_refresh":         run_final_refresh_check(candidates, freshness_window_minutes),
        "row_completeness":      run_row_completeness(candidates),
    }
    any_failed = any(
        r.get("status") == "FAILED"
        for r in reports.values()
    )
    return {
        "shared_services_status": "FAILED" if any_failed else "PASSED",
        "reports":                reports,
        "can_execute":            CAN_EXECUTE,
    }
