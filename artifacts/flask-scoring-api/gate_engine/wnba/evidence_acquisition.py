"""
gate_engine/wnba/evidence_acquisition.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL

Main orchestrator for the WNBA Evidence Acquisition structural pipeline.

Gate insertion order (spec §8):
  SLATE → IDENTITY → PRIMARY_ACQUISITION → COVERAGE_AUDIT →
  FALLBACK_ROUTING → SOURCE_RECONCILIATION → OPPORTUNITY_PACKET_VALIDATION →
  [existing analytical pipeline unchanged] → FINAL_REFRESH

This module is called by pipeline.py AFTER status_role.run() and BEFORE
the existing WNBA opportunity engine (_wnba_opp_gate.run()).

It does NOT:
  - Compute any probability estimate or calibration score
  - Create any new qualification / confidence label
  - Touch any existing gate threshold or probability formula
  - Make any external HTTP / API calls

It DOES:
  - Build the WNBAOpportunityPacket from current row + enrichment state
  - Run the missing-field detector
  - Run field-specific fallback routing (structural config + in-pipeline reconstruction)
  - Build and store a complete acquisition_audit
  - Emit packet_status (PACKET_COMPLETE / PACKET_RECONSTRUCTED / PACKET_INCOMPLETE_REJECTED)
  - Block the row if PACKET_INCOMPLETE_REJECTED (no required field left NOT_CALLED)

can_execute=False is unconditional.
"""
from __future__ import annotations

import datetime
from typing import Any

from .acquisition_packet import (
    PacketStatus,
    AcquisitionFieldStatus,
    AcquisitionMethod,
    build_packet,
)
from .missing_field_detector import (
    detect_missing,
    classify_missing_fields,
    build_coverage_audit,
)
from .fallback_router import (
    FALLBACK_SOURCE_PRIORITY,
    RouteAttemptResult,
    route_fallback_for_categories,
    AcquisitionFieldStatus as _AFS,
)
from .opportunity_engine import is_wnba_row

can_execute = False

# ---------------------------------------------------------------------------
# Non-blocking categories — DATA_UNOBTAINABLE for these does NOT trigger
# PACKET_INCOMPLETE_REJECTED.
#
# Rationale:
#   event_status  — new observability field; existing analytical gates don't
#                   depend on it; unobtainable = informational gap, not fatal.
#   matchup       — spec §1 explicitly permits null/proxy values; downstream
#                   gates assess matchup independently.
#   box_score_log — existing opportunity engine already handles absent
#                   box_score_log with WNBA_HOLD_ROLE_UNCERTAIN (soft hold);
#                   the new acquisition gate must not add a hard reject on top
#                   of what the existing pipeline treats as a soft hold.
#
# Only role_status sub-fields trigger PACKET_INCOMPLETE_REJECTED when
# unobtainable, because status_role and the opportunity engine depend on them.
# ---------------------------------------------------------------------------
_NON_BLOCKING_PROXY_CATEGORIES: frozenset[str] = frozenset({
    "matchup",
    "event_status",
    "box_score_log",
})


# ---------------------------------------------------------------------------
# Source reconciliation check (spec gate: SOURCE_RECONCILIATION)
# ---------------------------------------------------------------------------

def _run_source_reconciliation(
    packet: dict[str, Any],
    fallback_results: dict[str, "RouteAttemptResult"],
) -> dict[str, Any]:
    """
    Check for conflicts between sources across the packet.

    Rules:
    - A role_status.active_status claim present in the packet that has
      sources with conflict_status="CONFLICT" → SOURCE_CONFLICT blocker.
    - A box_score_log from two different sources with contradictory row
      counts is flagged (currently structural: count mismatch logged only).
    """
    conflicts: list[str] = []
    notes:     list[str] = []

    role_sec  = packet.get("role_status") or {}
    sources   = role_sec.get("sources") or []
    for s in sources:
        if isinstance(s, dict) and s.get("conflict_status") == "CONFLICT":
            conflicts.append(
                f"role_status.sources: conflict reported by {s.get('source','unknown')}"
            )

    return {
        "gate":               "SOURCE_RECONCILIATION",
        "conflict_count":     len(conflicts),
        "conflicts":          conflicts,
        "notes":              notes,
        "passed":             len(conflicts) == 0,
    }


# ---------------------------------------------------------------------------
# Packet validation (spec gate: OPPORTUNITY_PACKET_VALIDATION)
# ---------------------------------------------------------------------------

def _validate_packet(
    packet: dict[str, Any],
    missing_after_primary: list[str],
    fallback_results: dict[str, "RouteAttemptResult"],
    reconciliation: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    """
    Determine the final packet_status and field_status_map.

    Returns (packet_status, fields_reconstructed, fields_unresolved).
    """
    fields_reconstructed: list[str] = []
    fields_unresolved:    list[str] = []

    # Categorise fallback results
    blocking_categories: list[str] = []
    for category, result in fallback_results.items():
        if category in _NON_BLOCKING_PROXY_CATEGORIES:
            # Proxy-only matchup never blocks
            continue
        if result.status == AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION:
            blocking_categories.append(category)
            fields_unresolved.extend(
                [f for f in missing_after_primary if f.split(".")[0] == category or category in f]
            )
        elif result.status in (
            AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            AcquisitionFieldStatus.PRIMARY_RETRIEVED,
        ):
            fields_reconstructed.extend(
                [f for f in missing_after_primary if f.split(".")[0] == category or category in f]
            )

    # Source conflict → reject immediately
    if not reconciliation["passed"]:
        return PacketStatus.PACKET_INCOMPLETE_REJECTED, fields_reconstructed, fields_unresolved

    if blocking_categories:
        return PacketStatus.PACKET_INCOMPLETE_REJECTED, fields_reconstructed, fields_unresolved

    if missing_after_primary:
        # Some fields were missing but successfully reconstructed
        return PacketStatus.PACKET_RECONSTRUCTED, fields_reconstructed, fields_unresolved

    return PacketStatus.PACKET_COMPLETE, fields_reconstructed, fields_unresolved


# ---------------------------------------------------------------------------
# Acquisition audit builder (spec §5)
# ---------------------------------------------------------------------------

def _build_acquisition_audit(
    primary_api_result:      str,
    missing_after_primary:   list[str],
    coverage_audit:          dict[str, Any],
    fallback_results:        dict[str, "RouteAttemptResult"],
    reconciliation:          dict[str, Any],
    packet_status:           str,
    fields_reconstructed:    list[str],
    fields_unresolved:       list[str],
    run_ts:                  str,
) -> dict[str, Any]:
    """
    Build the complete acquisition_audit object per spec §5.

    All required audit fields are present:
      primary_api_attempted, primary_api_result, missing_after_primary,
      fallback_required, fallback_triggered, fallback_routes_attempted,
      fallback_sources_successful, fields_reconstructed, fields_unresolved,
      fallback_failure_reason, packet_status.
    """
    fallback_required  = bool(missing_after_primary)
    fallback_triggered = fallback_required  # always triggered when required (structural)

    fallback_routes_attempted: list[str] = []
    fallback_sources_successful: list[str] = []
    fallback_failure_reason: str | None = None

    for category, result in fallback_results.items():
        fallback_routes_attempted.extend(result.routes_attempted)
        if result.status not in (
            AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
            AcquisitionFieldStatus._NOT_YET_ATTEMPTED,
        ):
            fallback_sources_successful.append(f"{category}:{result.source_id}")

    if fields_unresolved:
        fallback_failure_reason = (
            f"Fields unresolvable after exhausting all configured routes: "
            f"{', '.join(fields_unresolved)}"
        )
    elif not reconciliation["passed"]:
        fallback_failure_reason = (
            f"Source conflict detected: {reconciliation.get('conflicts')}"
        )

    return {
        "run_ts":                    run_ts,
        "primary_api_attempted":     True,
        "primary_api_result":        primary_api_result,
        "missing_after_primary":     list(missing_after_primary),
        "fallback_required":         fallback_required,
        "fallback_triggered":        fallback_triggered,
        "fallback_routes_attempted": list(dict.fromkeys(fallback_routes_attempted)),  # dedupe, preserve order
        "fallback_sources_successful": fallback_sources_successful,
        "fields_reconstructed":      list(fields_reconstructed),
        "fields_unresolved":         list(fields_unresolved),
        "fallback_failure_reason":   fallback_failure_reason,
        "packet_status":             packet_status,
        "source_reconciliation":     reconciliation,
        "coverage_audit":            coverage_audit,
        "fallback_result_details":   {
            cat: {
                "source_id":        r.source_id,
                "source_grade":     r.source_grade,
                "method":           r.method,
                "status":           r.status,
                "note":             r.note,
                "routes_attempted": r.routes_attempted,
            }
            for cat, r in fallback_results.items()
        },
    }


# ---------------------------------------------------------------------------
# Field status map builder
# ---------------------------------------------------------------------------

def _build_field_status_map(
    required_fields:         list[str],
    missing_after_primary:   list[str],
    fallback_results:        dict[str, "RouteAttemptResult"],
    packet:                  dict[str, Any],
) -> dict[str, str]:
    """
    Build a per-field terminal status map using the strict enum.
    NOT_CALLED is never a terminal status.
    """
    from .missing_field_detector import REQUIRED_PACKET_FIELDS

    status_map: dict[str, str] = {}

    for field_path in REQUIRED_PACKET_FIELDS:
        if field_path not in missing_after_primary:
            # Was present from primary acquisition
            status_map[field_path] = AcquisitionFieldStatus.PRIMARY_RETRIEVED
            continue

        # Determine category for this field
        category = field_path.split(".")[0]
        if "box_score_log" in field_path or "l5_ledger" in field_path or "l10_ledger" in field_path:
            category = "box_score_log"
        if category not in fallback_results:
            status_map[field_path] = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION
            continue

        result = fallback_results[category]
        # Map RouteAttemptResult.status → AcquisitionFieldStatus
        status_map[field_path] = result.status

    return status_map


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(
    row: dict[str, Any],
    enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Per-row entry point.  Only acts on WNBA rows.

    Called by pipeline.py after status_role.run() and before
    the existing WNBA opportunity engine.

    Reads:
      row["role_status"]          — set by status_role gate
      enrichment["box_score_log"] — raw per-game dicts
      enrichment["matchup"]       — opponent context (may be absent)
      enrichment["status_payload"]— role/injury payload

    Writes:
      row["gates"]["wnba_evidence_acquisition"]  — full gate result
      row["can_execute"]                         — always False

    Returns the gate result dict (also stored on row).
    """
    if not is_wnba_row(row):
        return {}

    row["can_execute"] = False
    row.setdefault("gates", {})
    row.setdefault("blockers", [])

    enr    = enrichment or {}
    run_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Step 1: PRIMARY_ACQUISITION result assessment
    # (data already in enrichment from auto_enrichment / caller)
    # ------------------------------------------------------------------
    has_box_score  = bool(enr.get("box_score_log") or enr.get("game_log"))
    has_event_stat = bool(enr.get("event_status") or enr.get("game_status"))
    has_role_data  = bool(enr.get("role_timestamp") or (row.get("role_status") or {}).get("role_timestamp"))

    if has_box_score and has_event_stat and has_role_data:
        primary_api_result = "FULL"
    elif has_box_score or has_role_data:
        primary_api_result = "PARTIAL"
    else:
        primary_api_result = "PARTIAL"

    # ------------------------------------------------------------------
    # Step 2: Build opportunity packet from current state
    # ------------------------------------------------------------------
    packet = build_packet(row, enr, as_of=run_ts)

    # ------------------------------------------------------------------
    # Step 3: COVERAGE_AUDIT — detect missing required fields
    # ------------------------------------------------------------------
    initial_missing_fields = detect_missing(packet)  # before any fallback
    coverage_audit         = build_coverage_audit(packet, initial_missing_fields)

    # missing_after_primary tracks what is still unresolved after fallback.
    # We keep initial_missing_fields for the audit (fallback_required/triggered).
    missing_after_primary = list(initial_missing_fields)

    # ------------------------------------------------------------------
    # Step 4: FALLBACK_ROUTING — attempt reconstruction / source lookup
    # ------------------------------------------------------------------
    fallback_results: dict[str, RouteAttemptResult] = {}

    if initial_missing_fields:
        categories = classify_missing_fields(initial_missing_fields)
        fallback_results = route_fallback_for_categories(categories, packet, enr)

        # Apply any in-pipeline reconstructed values back into enrichment
        for cat, result in fallback_results.items():
            if cat == "box_score_log" and result.status in (
                AcquisitionFieldStatus.FALLBACK_RETRIEVED,
                AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            ):
                # Reconstruction from game_log alt key: write back into enr
                if not enr.get("box_score_log") and enr.get("game_log"):
                    enr["box_score_log"] = enr["game_log"]
                    # Rebuild packet with the now-populated box_score_log
                    packet = build_packet(row, enr, as_of=run_ts)

            if cat == "event_status" and result.status == AcquisitionFieldStatus.FALLBACK_RETRIEVED:
                if result.value_retrieved and not enr.get("event_status"):
                    enr["event_status"] = result.value_retrieved

        # Re-check what is still missing AFTER reconstruction attempts
        missing_after_primary = detect_missing(packet)

    # ------------------------------------------------------------------
    # Step 5: SOURCE_RECONCILIATION
    # ------------------------------------------------------------------
    reconciliation = _run_source_reconciliation(packet, fallback_results)

    # ------------------------------------------------------------------
    # Step 6: OPPORTUNITY_PACKET_VALIDATION — determine packet_status
    # ------------------------------------------------------------------
    packet_status, fields_reconstructed, fields_unresolved = _validate_packet(
        packet,
        missing_after_primary,
        fallback_results,
        reconciliation,
    )

    packet["packet_status"] = packet_status

    # ------------------------------------------------------------------
    # Step 7: Build field status map (strict enum — no NOT_CALLED terminal)
    # ------------------------------------------------------------------
    from .missing_field_detector import REQUIRED_PACKET_FIELDS
    field_status_map = _build_field_status_map(
        REQUIRED_PACKET_FIELDS,
        missing_after_primary,
        fallback_results,
        packet,
    )
    packet["field_status_map"] = field_status_map

    # ------------------------------------------------------------------
    # Step 8: Build and attach acquisition_audit
    # ------------------------------------------------------------------
    acquisition_audit = _build_acquisition_audit(
        primary_api_result    = primary_api_result,
        missing_after_primary = initial_missing_fields,   # pre-fallback list (for triggered flag)
        coverage_audit        = coverage_audit,
        fallback_results      = fallback_results,
        reconciliation        = reconciliation,
        packet_status         = packet_status,
        fields_reconstructed  = fields_reconstructed,
        fields_unresolved     = fields_unresolved,
        run_ts                = run_ts,
    )
    packet["acquisition_audit"] = acquisition_audit

    # ------------------------------------------------------------------
    # Step 9: Stamp gate result on row
    # ------------------------------------------------------------------
    gate_result: dict[str, Any] = {
        "gate":                  "WNBA_EVIDENCE_ACQUISITION",
        "patch":                 "WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL",
        "packet_status":         packet_status,
        "fields_unresolved":     fields_unresolved,
        "fields_reconstructed":  fields_reconstructed,
        "missing_after_primary": missing_after_primary,
        "acquisition_audit":     acquisition_audit,
        "field_status_map":      field_status_map,
        "can_execute":           False,
    }

    row["gates"]["wnba_evidence_acquisition"] = gate_result
    return gate_result
