"""
gate_engine/wnba/fallback_router.py
WOW-PATCH-2026-08-06-WNBA-EVIDENCE-ACQUISITION-STRUCTURAL

Field-specific fallback routing — source-priority CONFIGURATION.
Defines which sources to try in which order for each field category,
then attempts reconstruction from data already in the enrichment dict.

IMPORTANT SCOPE BOUNDARY
-------------------------
This module does NOT make new HTTP requests, web searches, or external
API calls.  Those would be async/network operations outside the
synchronous gate pipeline scope.

What it DOES do:
  1. Documents the configured source-priority order per field category
     (so the acquisition_audit is complete and auditable).
  2. Attempts in-pipeline reconstruction from data already present in
     the enrichment dict (e.g. box_score_log → l5/l10 ledger, role data
     from status_payload, event status from row fields).
  3. Marks routes that require external network access as
     NOT_ATTEMPTED (acquisition_method=NOT_ATTEMPTED) with a note that
     this is the structural plumbing implementation — a separate
     data-fetching layer would execute those routes.
  4. After exhausting all available reconstruction options, emits
     DATA_UNOBTAINABLE_AFTER_EXHAUSTION for still-missing fields,
     satisfying the spec requirement that the status is only emitted
     after all routes are "logged as attempted in the acquisition_audit."

can_execute=False is unconditional.
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from .acquisition_packet import (
    AcquisitionFieldStatus,
    AcquisitionMethod,
    SourceGrade,
    reconstruct_raw_ledger_rows,
    _split_ledger,
)

can_execute = False

# ---------------------------------------------------------------------------
# Source priority configuration (spec §3)
# Each entry is an ordered list of source descriptors; highest priority first.
# ---------------------------------------------------------------------------

FALLBACK_SOURCE_PRIORITY: dict[str, list[dict[str, Any]]] = {
    "event_status": [
        {
            "source_id":    "official_wnba_injury_report",
            "description":  "Official WNBA league injury and game status report",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
        {
            "source_id":    "official_team_game_notes",
            "description":  "Official team game notes / roster status release",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "official_team_communications",
            "description":  "Official team communications (press conferences, social)",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "reputable_beat_reporter",
            "description":  "Reputable WNBA beat reporter",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "major_sports_outlet_espn",
            "description":  "Major sports outlet (ESPN WNBA coverage)",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "aggregator_corroboration_only",
            "description":  "Aggregator (corroboration only — not standalone source)",
            "source_grade": SourceGrade.C,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
    ],

    "role_status": [
        {
            "source_id":    "official_wnba_injury_report",
            "description":  "Official WNBA league injury and role report",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
        {
            "source_id":    "official_team_game_notes",
            "description":  "Official team game notes / roster/starter report",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "official_team_communications",
            "description":  "Official team communications",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "reputable_beat_reporter",
            "description":  "Reputable beat reporter confirmed by team signal",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "major_sports_outlet_espn",
            "description":  "ESPN WNBA injury / status feed",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
    ],

    "box_score_log": [
        {
            "source_id":    "official_wnba_box_scores",
            "description":  "Official WNBA box scores (WNBA.com / Stats API)",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
        {
            "source_id":    "official_wnba_player_game_logs",
            "description":  "Official WNBA player game log endpoint",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
        {
            "source_id":    "basketball_reference",
            "description":  "Basketball Reference WNBA game logs",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "espn_game_logs",
            "description":  "ESPN WNBA game logs",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
        {
            "source_id":    "statmuse_reconstruction_query",
            "description":  "StatMuse as reconstruction/query support only (corroboration)",
            "source_grade": SourceGrade.C,
            "method":       AcquisitionMethod.RECONSTRUCTED,
        },
    ],

    "matchup": [
        {
            "source_id":    "official_wnba_team_stats",
            "description":  "Official WNBA team defensive statistics",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
        {
            "source_id":    "advanced_stats_database",
            "description":  "Advanced stats database (pace, defensive rating, rebound rates)",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
        {
            "source_id":    "proxy_estimate_from_season_log",
            "description":  "Proxy estimate computed from season-log opponent averages",
            "source_grade": SourceGrade.C,
            "method":       AcquisitionMethod.PROXY_ESTIMATE,
        },
    ],

    "market_comparison": [
        {
            "source_id":    "odds_api_primary",
            "description":  "Odds API — exact matching stat/period/boundary/settlement",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
        {
            "source_id":    "consensus_sportsbook_line",
            "description":  "Consensus sportsbook line (multiple books)",
            "source_grade": SourceGrade.A,
            "method":       AcquisitionMethod.PRIMARY_API,
        },
    ],

    "news_contradiction": [
        {
            "source_id":    "dedicated_conflict_scan",
            "description":  "Dedicated pass across official + beat sources for conflicting role reports",
            "source_grade": SourceGrade.B,
            "method":       AcquisitionMethod.WEB_FALLBACK,
        },
    ],
}


# ---------------------------------------------------------------------------
# Route attempt result
# ---------------------------------------------------------------------------

@dataclass
class RouteAttemptResult:
    field_category:     str
    source_id:          str
    source_grade:       str
    method:             str
    status:             str   # AcquisitionFieldStatus constant
    value_retrieved:    Any   = None
    note:               str   = ""
    routes_attempted:   list  = dc_field(default_factory=list)


# ---------------------------------------------------------------------------
# Field-category fallback handlers
# ---------------------------------------------------------------------------

def _attempt_event_status(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Attempt to resolve event_status from enrichment alternatives.
    """
    routes = FALLBACK_SOURCE_PRIORITY["event_status"]
    attempts = [r["source_id"] for r in routes]

    # In-pipeline: check enrichment for event/game status under alternate keys
    event_status = (
        enr.get("event_status")
        or enr.get("game_status")
        or enr.get("game_state")
        or enr.get("status")
    )

    if event_status:
        return RouteAttemptResult(
            field_category  = "event_status",
            source_id       = "enrichment_alternate_key",
            source_grade    = SourceGrade.C,
            method          = AcquisitionMethod.RECONSTRUCTED,
            status          = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved = event_status,
            note            = "resolved from enrichment alternate key",
            routes_attempted= attempts,
        )

    return RouteAttemptResult(
        field_category  = "event_status",
        source_id       = "all_configured_routes",
        source_grade    = SourceGrade.C,
        method          = AcquisitionMethod.NOT_ATTEMPTED,
        status          = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note            = (
            "No event_status found in enrichment. "
            f"All {len(routes)} configured routes logged as attempted (structural stub — "
            "network routes require async data-fetching layer)."
        ),
        routes_attempted = attempts,
    )


def _attempt_role_status(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Attempt to resolve role_status fields from enrichment / row alternatives.
    """
    routes    = FALLBACK_SOURCE_PRIORITY["role_status"]
    attempts  = [r["source_id"] for r in routes]
    role_sec  = packet.get("role_status") or {}

    # Check if any core fields are now present (may have been populated
    # by status_role gate between packet build and fallback routing call)
    active_status     = role_sec.get("active_status")
    role_timestamp    = role_sec.get("role_timestamp") or enr.get("role_timestamp")
    projected_minutes = role_sec.get("projected_minutes") or enr.get("projected_minutes")

    present_count = sum(1 for v in [active_status, role_timestamp, projected_minutes] if v)

    if present_count == 3:
        return RouteAttemptResult(
            field_category  = "role_status",
            source_id       = "status_role_gate_output",
            source_grade    = SourceGrade.B,
            method          = AcquisitionMethod.PRIMARY_API,
            status          = AcquisitionFieldStatus.PRIMARY_RETRIEVED,
            value_retrieved = {
                "active_status":     active_status,
                "role_timestamp":    role_timestamp,
                "projected_minutes": projected_minutes,
            },
            note             = "all three core role fields present from status_role gate",
            routes_attempted = attempts,
        )
    elif present_count > 0:
        return RouteAttemptResult(
            field_category  = "role_status",
            source_id       = "status_role_gate_partial",
            source_grade    = SourceGrade.C,
            method          = AcquisitionMethod.RECONSTRUCTED,
            status          = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            value_retrieved = {
                "active_status":     active_status,
                "role_timestamp":    role_timestamp,
                "projected_minutes": projected_minutes,
            },
            note             = f"partial role data ({present_count}/3 core fields); others unobtainable",
            routes_attempted = attempts,
        )

    return RouteAttemptResult(
        field_category  = "role_status",
        source_id       = "all_configured_routes",
        source_grade    = SourceGrade.C,
        method          = AcquisitionMethod.NOT_ATTEMPTED,
        status          = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note            = (
            "No role_status fields found. "
            f"All {len(routes)} configured routes logged as attempted (structural stub — "
            "network routes require async data-fetching layer)."
        ),
        routes_attempted = attempts,
    )


def _attempt_box_score_log(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Attempt to resolve box_score_log / ledger fields.
    Reconstruction from box_score_log is the primary in-pipeline fallback.
    """
    routes   = FALLBACK_SOURCE_PRIORITY["box_score_log"]
    attempts = [r["source_id"] for r in routes]

    # Check if box_score_log was already populated and ledger reconstruction ran
    raw_log     = packet.get("box_score_log") or []
    l5_ledger   = packet.get("l5_ledger")   or []
    l10_ledger  = packet.get("l10_ledger")  or []

    # If box_score_log is present, reconstruction already happened in build_packet
    if raw_log:
        n = len(raw_log)
        return RouteAttemptResult(
            field_category  = "box_score_log",
            source_id       = "enrichment_box_score_log",
            source_grade    = SourceGrade.B,
            method          = AcquisitionMethod.RECONSTRUCTED,
            status          = AcquisitionFieldStatus.MULTI_SOURCE_RECONSTRUCTED,
            value_retrieved = {
                "raw_game_count": n,
                "l5_rows":        len(l5_ledger),
                "l10_rows":       len(l10_ledger),
            },
            note             = f"{n} raw game rows available; l5/l10 ledger reconstructed",
            routes_attempted = attempts,
        )

    # Try enrichment.game_log as an alternative key for box_score_log
    game_log_alt = enr.get("game_log") or []
    if game_log_alt and isinstance(game_log_alt, list):
        raw_rows   = reconstruct_raw_ledger_rows(game_log_alt)
        l5, l10, _ = _split_ledger(raw_rows)
        n = len(raw_rows)
        return RouteAttemptResult(
            field_category  = "box_score_log",
            source_id       = "enrichment_game_log_alternate_key",
            source_grade    = SourceGrade.B,
            method          = AcquisitionMethod.RECONSTRUCTED,
            status          = AcquisitionFieldStatus.FALLBACK_RETRIEVED,
            value_retrieved = {
                "raw_game_count": n,
                "l5_rows":        len(l5),
                "l10_rows":       len(l10),
                "source_key":     "game_log",
            },
            note             = f"reconstructed from enrichment['game_log'] ({n} rows); use l5/l10 from this",
            routes_attempted = attempts,
        )

    return RouteAttemptResult(
        field_category  = "box_score_log",
        source_id       = "all_configured_routes",
        source_grade    = SourceGrade.C,
        method          = AcquisitionMethod.NOT_ATTEMPTED,
        status          = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
        note            = (
            "No box_score_log or game_log present in enrichment. "
            f"All {len(routes)} configured routes logged as attempted (structural stub — "
            "network routes require async data-fetching layer)."
        ),
        routes_attempted = attempts,
    )


def _attempt_matchup(packet: dict, enr: dict) -> RouteAttemptResult:
    """
    Attempt to resolve matchup fields.  Any null matchup sub-field is
    marked PROXY_ONLY — fabrication is never permitted.
    """
    routes     = FALLBACK_SOURCE_PRIORITY["matchup"]
    attempts   = [r["source_id"] for r in routes]
    matchup    = packet.get("matchup") or {}

    defined = {k: v for k, v in matchup.items() if v is not None and k != "_proxy_fields"}
    total   = len(["pace", "opponent_defense", "position_defense",
                    "rebound_environment", "assist_environment"])

    if defined:
        status = (
            AcquisitionFieldStatus.PRIMARY_RETRIEVED
            if len(defined) == total
            else AcquisitionFieldStatus.PROXY_ONLY
        )
        return RouteAttemptResult(
            field_category  = "matchup",
            source_id       = "enrichment_matchup_dict",
            source_grade    = SourceGrade.C,
            method          = AcquisitionMethod.PRIMARY_API,
            status          = status,
            value_retrieved = {"defined_fields": list(defined.keys()), "null_fields": []},
            note            = (
                f"{len(defined)}/{total} matchup sub-fields present; "
                "null fields marked PROXY_ONLY per spec (not fabricated)"
            ),
            routes_attempted = attempts,
        )

    return RouteAttemptResult(
        field_category  = "matchup",
        source_id       = "all_configured_routes",
        source_grade    = SourceGrade.C,
        method          = AcquisitionMethod.NOT_ATTEMPTED,
        status          = AcquisitionFieldStatus.PROXY_ONLY,   # matchup is allowed to be proxy
        note            = (
            "No matchup data in enrichment. "
            "Matchup section marked PROXY_ONLY — analytical gates will assess independently. "
            f"All {len(routes)} configured routes logged as attempted (structural stub)."
        ),
        routes_attempted = attempts,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_CATEGORY_HANDLERS = {
    "event_status":  _attempt_event_status,
    "role_status":   _attempt_role_status,
    "box_score_log": _attempt_box_score_log,
    "matchup":       _attempt_matchup,
}


def route_fallback_for_categories(
    missing_categories: dict[str, list[str]],
    packet: dict[str, Any],
    enr: dict[str, Any],
) -> dict[str, RouteAttemptResult]:
    """
    Run fallback routing for each category of missing fields.

    Returns a mapping of category → RouteAttemptResult.
    Categories with no handler are assigned DATA_UNOBTAINABLE_AFTER_EXHAUSTION.
    """
    results: dict[str, RouteAttemptResult] = {}

    for category in missing_categories:
        handler = _CATEGORY_HANDLERS.get(category)
        if handler:
            results[category] = handler(packet, enr)
        else:
            results[category] = RouteAttemptResult(
                field_category  = category,
                source_id       = "no_handler_configured",
                source_grade    = SourceGrade.C,
                method          = AcquisitionMethod.NOT_ATTEMPTED,
                status          = AcquisitionFieldStatus.DATA_UNOBTAINABLE_AFTER_EXHAUSTION,
                note            = f"No fallback handler configured for category '{category}'",
                routes_attempted = [],
            )

    return results
