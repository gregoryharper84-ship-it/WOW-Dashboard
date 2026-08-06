"""
gate_engine/wnba/evidence_acquisition.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL
WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS

Main orchestrator for the WNBA Evidence Acquisition pipeline.

Gate insertion order (spec §8):
  SLATE → IDENTITY → PRIMARY_ACQUISITION → COVERAGE_AUDIT →
  FALLBACK_ROUTING → SOURCE_RECONCILIATION → OPPORTUNITY_PACKET_VALIDATION →
  [existing analytical pipeline unchanged] → FINAL_REFRESH

This module is called by pipeline.py AFTER status_role.run() and BEFORE
the existing WNBA opportunity engine (_wnba_opp_gate.run()).

Three-tier packet validation (WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS):

  PACKET_COMPLETE
    All critical + qualification fields present from primary acquisition.
    No fallback triggered.

  PACKET_RECONSTRUCTED_COMPLETE
    All critical + qualification fields satisfied (via primary or successful
    fallback). At least one field required a fallback to satisfy.
    Do NOT emit when box_score_log or l5_ledger/l10_ledger remain empty —
    that must be PACKET_INCOMPLETE_REJECTED instead.

  PACKET_PARTIAL_HOLD
    All critical-blocking fields satisfied.
    ≥1 qualification-blocking field (matchup / market_comparison /
    news_contradiction_check) remains DATA_UNOBTAINABLE_AFTER_EXHAUSTION.
    Row proceeds to analytical pipeline but cannot advance to a
    probability-qualified label downstream (capped at MODEL_QUALIFIED_HOLD).

  PACKET_INCOMPLETE_REJECTED
    Any critical-blocking field remains DATA_UNOBTAINABLE_AFTER_EXHAUSTION
    after full fallback exhaustion, OR a source conflict exists.
    Row is blocked — does NOT enter the analytical pipeline.

This module does NOT:
  - Compute any probability estimate or calibration score
  - Create any new qualification / confidence label
  - Touch any existing gate threshold or probability formula
  - Change the order of any existing analytical gate

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
    CRITICAL_BLOCKING_FIELDS,
    QUALIFICATION_BLOCKING_FIELDS,
    detect_missing,
    classify_missing_fields,
    build_coverage_audit,
    REQUIRED_PACKET_FIELDS,
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
# Statuses that mean "field was successfully obtained at some quality level"
# ---------------------------------------------------------------------------

_QUALIFYING_FIELD_STATUSES: frozenset[str] = frozenset({
    AcquisitionFieldStatus.PRIMARY_RETRIEVED,
    AcquisitionFieldStatus.FALLBACK_RETRIEVED,
    AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
    # PROXY_ONLY counts as "resolved at proxy level" for packet_status purposes
    # (matchup / market fields that are unavailable but not fabricated)
    AcquisitionFieldStatus.PROXY_ONLY,
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
    - role_status.sources with conflict_status="CONFLICT" → SOURCE_CONFLICT blocker.
    - news_contradiction adapter reported contradiction → SOURCE_CONFLICT blocker.
    - box_score_log from two different sources with contradictory row counts
      is flagged (structural: count mismatch logged, does not block alone).
    """
    conflicts: list[str] = []
    notes:     list[str] = []

    # Role source conflicts
    role_sec = packet.get("role_status") or {}
    for s in (role_sec.get("sources") or []):
        if isinstance(s, dict) and s.get("conflict_status") == "CONFLICT":
            conflicts.append(
                f"role_status.sources: conflict reported by {s.get('source','unknown')}"
            )

    # News contradiction adapter result
    news_result = fallback_results.get("news_contradiction")
    if news_result and news_result.adapter_result:
        if news_result.adapter_result.conflict_status == "CONFLICT":
            conflicts.append(
                "news_contradiction: contradictory role signals found in recent news"
            )
            nf = news_result.adapter_result.normalized_fields or {}
            notes.append(
                f"out_signals={nf.get('out_signals',0)}, "
                f"active_signals={nf.get('active_signals',0)}"
            )

    return {
        "gate":           "SOURCE_RECONCILIATION",
        "conflict_count": len(conflicts),
        "conflicts":      conflicts,
        "notes":          notes,
        "passed":         len(conflicts) == 0,
    }


# ---------------------------------------------------------------------------
# Packet validation (spec gate: OPPORTUNITY_PACKET_VALIDATION)
# Three-tier logic per WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS
# ---------------------------------------------------------------------------

def _get_category_for_field(field_path: str) -> str:
    """Map a field path to its fallback router category key."""
    if "event_status" in field_path:
        return "event_status"
    if field_path.startswith("role_status"):
        return "role_status"
    if any(k in field_path for k in ("box_score_log", "l5_ledger", "l10_ledger")):
        return "box_score_log"
    if "matchup" in field_path:
        return "matchup"
    if "market_comparison" in field_path:
        return "market_comparison"
    if "news_contradiction" in field_path:
        return "news_contradiction"
    return "other"


def _validate_packet(
    packet: dict[str, Any],
    missing_after_primary: list[str],
    fallback_results: dict[str, "RouteAttemptResult"],
    reconciliation: dict[str, Any],
) -> tuple[str, list[str], list[str]]:
    """
    Determine packet_status, fields_reconstructed, fields_unresolved.

    Decision ladder:
      1. Source conflict → PACKET_INCOMPLETE_REJECTED
      2. Any CRITICAL_BLOCKING field unresolved → PACKET_INCOMPLETE_REJECTED
      3. Any QUALIFICATION_BLOCKING field unresolved (DATA_UNOBTAINABLE) →
         PACKET_PARTIAL_HOLD (note: PROXY_ONLY does NOT trigger this)
      4. Any field was missing but successfully reconstructed →
         PACKET_RECONSTRUCTED_COMPLETE
      5. No missing fields → PACKET_COMPLETE
    """
    fields_reconstructed: list[str] = []
    fields_unresolved:    list[str] = []

    # Classify each field that was initially missing
    for field_path in missing_after_primary:
        category = _get_category_for_field(field_path)
        result   = fallback_results.get(category)

        if result and result.status in _QUALIFYING_FIELD_STATUSES:
            fields_reconstructed.append(field_path)
        else:
            fields_unresolved.append(field_path)

    # 1. Source conflict → reject
    if not reconciliation["passed"]:
        return (
            PacketStatus.PACKET_INCOMPLETE_REJECTED,
            fields_reconstructed,
            fields_unresolved,
        )

    # 2. Any CRITICAL unresolved → reject
    critical_unresolved = [
        f for f in fields_unresolved
        if f in CRITICAL_BLOCKING_FIELDS
    ]
    if critical_unresolved:
        return (
            PacketStatus.PACKET_INCOMPLETE_REJECTED,
            fields_reconstructed,
            fields_unresolved,
        )

    # 3. Any QUALIFICATION unresolved (DATA_UNOBTAINABLE, not PROXY_ONLY) → partial hold
    # PROXY_ONLY is in _QUALIFYING_FIELD_STATUSES so it doesn't land in fields_unresolved
    qualification_unresolved = [
        f for f in fields_unresolved
        if f in QUALIFICATION_BLOCKING_FIELDS
    ]
    if qualification_unresolved:
        return (
            PacketStatus.PACKET_PARTIAL_HOLD,
            fields_reconstructed,
            fields_unresolved,
        )

    # 4. Some fields were missing but all are now resolved
    if fields_reconstructed:
        return (
            PacketStatus.PACKET_RECONSTRUCTED_COMPLETE,
            fields_reconstructed,
            fields_unresolved,
        )

    # 5. Nothing was missing → complete
    return PacketStatus.PACKET_COMPLETE, [], []


# ---------------------------------------------------------------------------
# Extended acquisition audit builder
# ---------------------------------------------------------------------------

def _build_acquisition_audit(
    primary_api_result:        str,
    initial_missing_fields:    list[str],
    coverage_audit:            dict[str, Any],
    fallback_results:          dict[str, "RouteAttemptResult"],
    reconciliation:            dict[str, Any],
    packet_status:             str,
    fields_reconstructed:      list[str],
    fields_unresolved:         list[str],
    run_ts:                    str,
) -> dict[str, Any]:
    """
    Build the complete acquisition_audit object.

    Extended fields (WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS):
      external_fetch_required, external_fetch_triggered, adapters_called,
      adapters_succeeded, adapters_failed, request_count,
      normalized_record_count, fields_filled_externally,
      fields_still_missing, source_conflicts, terminal_packet_status.

    Audit-semantics correction fields (request_made invariant enforced):
      routes_attempted          — normalized records where request_made=True only
      fallback_routes_attempted — provider names where request_made=True only
      routes_skipped_by_policy  — providers blocked by policy (e.g. BBRef)
      routes_not_implemented    — providers configured but no handler implemented
      routes_unavailable        — providers called; source was unreachable
      routes_auth_required      — providers where AUTH_REQUIRED was returned

    INVARIANTS enforced here:
      - request_made=False records never appear in routes_attempted or
        fallback_routes_attempted.
      - request_count equals the sum of request_count across records where
        request_made=True.
      - adapters_called contains only providers where request_made=True
        (i.e. adapter.request_count > 0).
    """
    fallback_required  = bool(initial_missing_fields)
    fallback_triggered = fallback_required

    # Audit-semantics: HTTP-only routes (request_made=True)
    fallback_routes_attempted:   list[str]  = []   # provider names, HTTP only
    fallback_sources_successful: list[str]  = []
    fallback_failure_reason:     str | None = None

    # Normalized route records aggregated across all categories
    all_route_records:            list[dict] = []
    agg_routes_skipped_policy:    list[str]  = []
    agg_routes_not_implemented:   list[str]  = []
    agg_routes_unavailable:       list[str]  = []
    agg_routes_auth_required:     list[str]  = []

    # External adapter tracking (request_made=True only)
    adapters_called:          list[str] = []
    adapters_succeeded:       list[str] = []
    adapters_failed:          list[str] = []
    total_request_count:      int = 0
    total_normalized_records: int = 0
    fields_filled_externally: list[str] = []

    for category, result in fallback_results.items():
        # INVARIANT: routes_attempted / fallback_routes_attempted = HTTP only
        for route_id in result.routes_attempted:   # already HTTP-only per handler contract
            if route_id not in fallback_routes_attempted:
                fallback_routes_attempted.append(route_id)

        if result.status in _QUALIFYING_FIELD_STATUSES and result.status not in (
            AcquisitionFieldStatus._NOT_YET_ATTEMPTED,
        ):
            if result.status != AcquisitionFieldStatus.PROXY_ONLY:
                fallback_sources_successful.append(f"{category}:{result.source_id}")

        # Collect normalized route records (deduplicated by provider)
        seen_providers = {r["provider"] for r in all_route_records}
        for rec in result.route_records:
            if rec["provider"] not in seen_providers:
                all_route_records.append(rec)
                seen_providers.add(rec["provider"])

        # Aggregate not-called route categories
        for p in result.routes_skipped_by_policy:
            if p not in agg_routes_skipped_policy:
                agg_routes_skipped_policy.append(p)
        for p in result.routes_not_implemented:
            if p not in agg_routes_not_implemented:
                agg_routes_not_implemented.append(p)

        # External adapter tracking — ONLY when a real request was made
        if result.adapter_result is not None:
            ar = result.adapter_result
            # Availability / auth categorization (any outcome)
            if ar.request_status == "SOURCE_UNAVAILABLE":
                if ar.provider not in agg_routes_unavailable:
                    agg_routes_unavailable.append(ar.provider)
            if ar.request_status == "AUTH_REQUIRED":
                if ar.provider not in agg_routes_auth_required:
                    agg_routes_auth_required.append(ar.provider)
            # INVARIANT: only count adapters that issued at least one HTTP request
            if ar.request_count > 0:
                if ar.provider not in adapters_called:
                    adapters_called.append(ar.provider)
                total_request_count      += ar.request_count
                total_normalized_records += ar.raw_record_count
                if ar.request_status == "REQUEST_SUCCEEDED":
                    adapters_succeeded.append(ar.provider)
                    if result.status in _QUALIFYING_FIELD_STATUSES:
                        for fp in initial_missing_fields:
                            if _get_category_for_field(fp) == category:
                                fields_filled_externally.append(fp)
                else:
                    adapters_failed.append(f"{ar.provider}:{ar.request_status}")

    if fields_unresolved:
        fallback_failure_reason = (
            f"Fields unresolvable after exhausting all configured routes: "
            f"{', '.join(fields_unresolved)}"
        )
    elif not reconciliation["passed"]:
        fallback_failure_reason = (
            f"Source conflict detected: {reconciliation.get('conflicts')}"
        )

    # Source conflicts summary
    source_conflicts = list(reconciliation.get("conflicts") or [])

    external_fetch_required = bool(adapters_called)

    # routes_attempted (new normalized field) = only records where request_made=True
    http_route_records = [r for r in all_route_records if r.get("request_made")]

    return {
        # Original fields
        "run_ts":                      run_ts,
        "primary_api_attempted":       True,
        "primary_api_result":          primary_api_result,
        "missing_after_primary":       list(initial_missing_fields),
        "fallback_required":           fallback_required,
        "fallback_triggered":          fallback_triggered,
        # INVARIANT: HTTP-only provider name list (backward-compat string list)
        "fallback_routes_attempted":   list(dict.fromkeys(fallback_routes_attempted)),
        "fallback_sources_successful": fallback_sources_successful,
        "fields_reconstructed":        list(fields_reconstructed),
        "fields_unresolved":           list(fields_unresolved),
        "fallback_failure_reason":     fallback_failure_reason,
        "packet_status":               packet_status,
        "source_reconciliation":       reconciliation,
        "coverage_audit":              coverage_audit,
        # Extended fields (external adapter telemetry — request_made=True only)
        "external_fetch_required":     external_fetch_required,
        "external_fetch_triggered":    bool(adapters_called),
        "adapters_called":             adapters_called,   # request_made=True providers only
        "adapters_succeeded":          adapters_succeeded,
        "adapters_failed":             adapters_failed,
        "request_count":               total_request_count,  # sum of request_made=True counts
        "normalized_record_count":     total_normalized_records,
        "fields_filled_externally":    fields_filled_externally,
        "fields_still_missing":        list(fields_unresolved),
        "source_conflicts":            source_conflicts,
        "terminal_packet_status":      packet_status,
        # Audit-semantics correction fields (WOW-PATCH audit-semantics correction)
        "routes_attempted":            http_route_records,    # normalized; request_made=True only
        "routes_skipped_by_policy":    agg_routes_skipped_policy,
        "routes_not_implemented":      agg_routes_not_implemented,
        "routes_unavailable":          agg_routes_unavailable,
        "routes_auth_required":        agg_routes_auth_required,
        # Per-category adapter detail for postmortem
        "fallback_result_details": {
            cat: {
                "source_id":        r.source_id,
                "source_grade":     r.source_grade,
                "method":           r.method,
                "status":           r.status,
                "note":             r.note,
                # route_records replaces routes_attempted in per-category detail
                "route_records":    r.route_records,
                "request_count":    r.request_count,
                **(
                    {
                        "adapter": {
                            "provider":         r.adapter_result.provider,
                            "source_url_or_id": r.adapter_result.source_url_or_id,
                            "retrieved_at":     r.adapter_result.retrieved_at,
                            "request_status":   r.adapter_result.request_status,
                            "request_made":     r.adapter_result.request_count > 0,
                            "raw_record_count": r.adapter_result.raw_record_count,
                            "failure_reason":   r.adapter_result.failure_reason,
                        }
                    }
                    if r.adapter_result is not None else {}
                ),
            }
            for cat, r in fallback_results.items()
        },
    }


# ---------------------------------------------------------------------------
# Field status map builder
# ---------------------------------------------------------------------------

def _build_field_status_map(
    missing_after_primary:  list[str],
    fallback_results:       dict[str, "RouteAttemptResult"],
    post_fallback_missing:  list[str],
) -> dict[str, str]:
    """
    Build a per-field terminal status map using the strict enum.
    NOT_CALLED / _NOT_YET_ATTEMPTED is never a terminal status.
    """
    status_map: dict[str, str] = {}

    for field_path in REQUIRED_PACKET_FIELDS:
        if field_path not in missing_after_primary:
            # Present from primary acquisition
            status_map[field_path] = AcquisitionFieldStatus.PRIMARY_RETRIEVED
            continue

        category = _get_category_for_field(field_path)
        result   = fallback_results.get(category)

        if result is None:
            status_map[field_path] = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION
        elif result.status == AcquisitionFieldStatus._NOT_YET_ATTEMPTED:
            status_map[field_path] = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION
        else:
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

    Gate outcomes:
      PACKET_COMPLETE / PACKET_RECONSTRUCTED_COMPLETE → row proceeds normally
      PACKET_PARTIAL_HOLD → row proceeds; pipeline adds note (no hard block)
      PACKET_INCOMPLETE_REJECTED → pipeline sets DATA_CONTRACT_FAIL and skips row

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
    # Step 1: PRIMARY_ACQUISITION — assess what enrichment already has
    # ------------------------------------------------------------------
    has_box_score  = bool(enr.get("box_score_log") or enr.get("game_log"))
    has_event_stat = bool(enr.get("event_status") or enr.get("game_status"))
    has_role_data  = bool(
        enr.get("role_timestamp")
        or (row.get("role_status") or {}).get("role_timestamp")
    )

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
    initial_missing_fields = detect_missing(packet)
    coverage_audit         = build_coverage_audit(packet, initial_missing_fields)

    # ------------------------------------------------------------------
    # Step 4: FALLBACK_ROUTING — attempt reconstruction / real HTTP fetch
    # ------------------------------------------------------------------
    fallback_results: dict[str, RouteAttemptResult] = {}

    if initial_missing_fields:
        categories     = classify_missing_fields(initial_missing_fields)
        fallback_results = route_fallback_for_categories(categories, packet, enr)

        # Apply any values written back into enr by external adapters
        # (box_score_log adapter writes directly into enr; rebuild packet)
        for cat, result in fallback_results.items():
            if cat == "box_score_log" and result.status in (
                AcquisitionFieldStatus.FALLBACK_RETRIEVED,
                AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            ):
                if not packet.get("box_score_log") and enr.get("box_score_log"):
                    packet = build_packet(row, enr, as_of=run_ts)

            elif cat == "event_status" and result.status in _QUALIFYING_FIELD_STATUSES:
                if result.value_retrieved and not enr.get("event_status"):
                    enr["event_status"] = result.value_retrieved
                    packet["event_status"] = result.value_retrieved

            elif cat == "market_comparison" and result.status in _QUALIFYING_FIELD_STATUSES:
                if result.value_retrieved and not enr.get("market_comparison"):
                    enr["market_comparison"] = result.value_retrieved
                    packet["market_comparison"] = result.value_retrieved

            elif cat == "news_contradiction" and result.status in _QUALIFYING_FIELD_STATUSES:
                if result.value_retrieved and not enr.get("news_contradiction_check"):
                    enr["news_contradiction_check"] = result.value_retrieved
                    packet["news_contradiction_check"] = result.value_retrieved

            elif cat == "role_status" and result.status in _QUALIFYING_FIELD_STATUSES:
                nf = result.value_retrieved or {}
                rs = packet.get("role_status") or {}
                if nf.get("active_status") and not rs.get("active_status"):
                    rs["active_status"] = nf["active_status"]
                if nf.get("role_timestamp") and not rs.get("role_timestamp"):
                    rs["role_timestamp"] = nf["role_timestamp"]

    # Re-check what is still missing AFTER reconstruction + external fetch
    post_fallback_missing = detect_missing(packet)

    # ------------------------------------------------------------------
    # Step 5: SOURCE_RECONCILIATION
    # ------------------------------------------------------------------
    reconciliation = _run_source_reconciliation(packet, fallback_results)

    # ------------------------------------------------------------------
    # Step 6: OPPORTUNITY_PACKET_VALIDATION — 3-tier packet_status
    # ------------------------------------------------------------------
    packet_status, fields_reconstructed, fields_unresolved = _validate_packet(
        packet,
        post_fallback_missing,
        fallback_results,
        reconciliation,
    )

    packet["packet_status"] = packet_status

    # ------------------------------------------------------------------
    # Step 7: Build field status map (strict enum — no NOT_CALLED terminal)
    # ------------------------------------------------------------------
    field_status_map = _build_field_status_map(
        initial_missing_fields,
        fallback_results,
        post_fallback_missing,
    )
    packet["field_status_map"] = field_status_map

    # ------------------------------------------------------------------
    # Step 8: Build and attach extended acquisition_audit
    # ------------------------------------------------------------------
    acquisition_audit = _build_acquisition_audit(
        primary_api_result     = primary_api_result,
        initial_missing_fields = initial_missing_fields,
        coverage_audit         = coverage_audit,
        fallback_results       = fallback_results,
        reconciliation         = reconciliation,
        packet_status          = packet_status,
        fields_reconstructed   = fields_reconstructed,
        fields_unresolved      = fields_unresolved,
        run_ts                 = run_ts,
    )
    packet["acquisition_audit"] = acquisition_audit

    # ------------------------------------------------------------------
    # Step 9: Stamp gate result on row
    # ------------------------------------------------------------------
    gate_result: dict[str, Any] = {
        "gate":                   "WNBA_EVIDENCE_ACQUISITION",
        "patch":                  "WOW-PATCH-2026-08-06-WNBA-EXTERNAL-EVIDENCE-ADAPTERS",
        "packet_status":          packet_status,
        "fields_unresolved":      fields_unresolved,
        "fields_reconstructed":   fields_reconstructed,
        "missing_after_primary":  post_fallback_missing,
        "acquisition_audit":      acquisition_audit,
        "field_status_map":       field_status_map,
        "can_execute":            False,
    }

    row["gates"]["wnba_evidence_acquisition"] = gate_result
    return gate_result
